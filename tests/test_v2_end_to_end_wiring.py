"""The v2 path running end to end: loader, roles, sequencing, builder.

These close the gap between "the contracts exist" and "the v2 path actually
runs". Each piece was correct in isolation and reachable from nothing; what is
tested here is that a real report run now exercises all five engines rather
than only grants, and that the two rules the wiring had to carry — Section 10's
role guardrail and Section 13's sequencing — hold once it does.
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.modules.reports.commercial_strategy import (
    COMMERCIAL_COMPARABLE,
    PRODUCTION_COMPARABLE,
    ComparableProfile,
    ComparableRelationship,
    SourcedValue,
    match_comparables,
    match_company,
)
from app.modules.reports.opportunity_catalogue import load_opportunities
from app.modules.reports.orchestration import EngineResult, sequence_next_steps
from app.modules.reports.project_dna import build_project_dna

TODAY = date(2026, 9, 20)
SOURCE = "https://example.org/official"
MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic/versions"


def _run(engine, filename, direction="upgrade"):
    import alembic
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location(
        f"_m_{filename}_{direction}", MIGRATIONS / filename
    )
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        previous = alembic.op
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            getattr(module, direction)()
        finally:
            alembic.op = previous


@pytest.fixture()
def staged():
    engine = sa.create_engine("sqlite://")
    _run(engine, "r7s8t9u0v1w2_engine_v2_staging_tables.py")
    _run(engine, "s8t9u0v1w2x3_observed_open_cycle.py")
    _run(engine, "u0v1w2x3y4z5_source_verifications.py")
    return engine


def _stage_cycle(engine, **overrides):
    row = {
        "id": "cycle-1",
        "kind": "FESTIVAL",
        "record_id": "fest-1",
        "section_name": "International Feature",
        "cycle_open": date(2026, 8, 1),
        "cycle_deadline": date(2026, 12, 1),
        "cycle_verified": True,
        "rules_complete": True,
        "source_url": SOURCE,
        "verified_on": date(2026, 9, 1),
    }
    row.update(overrides)
    with engine.begin() as conn:
        table = sa.Table("opportunity_cycles", sa.MetaData(), autoload_with=conn)
        conn.execute(table.insert(), row)
        records = sa.Table(
            "engine_handoff_records", sa.MetaData(), autoload_with=conn
        )
        conn.execute(
            records.insert(),
            {
                "kind": row["kind"],
                "record_id": row["record_id"],
                "source_version": "v1",
                "name": "A Festival",
                "routing": "FESTIVAL",
                "payload_hash": "x" * 64,
                "payload": {},
                "imported_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            },
        )
    return row


# ── The loader ───────────────────────────────────────────────────────────────


def test_an_empty_staging_table_yields_an_empty_universe():
    """The honest output today, and not an error."""
    engine = sa.create_engine("sqlite://")
    assert load_opportunities(engine, kind="FESTIVAL", today=TODAY) == []


def test_a_staged_cycle_becomes_an_opportunity(staged):
    _stage_cycle(staged)
    loaded = load_opportunities(staged, kind="FESTIVAL", today=TODAY)
    assert len(loaded) == 1
    assert loaded[0].name == "A Festival — International Feature"
    assert loaded[0].record_id == "fest-1"


def test_a_cycle_with_no_deadline_is_refused_rather_than_loaded_blank(staged):
    """An unverified cycle must not be one bug away from being ranked."""
    _stage_cycle(staged, cycle_deadline=None)
    assert load_opportunities(staged, kind="FESTIVAL", today=TODAY) == []


def test_a_cycle_with_no_source_is_refused(staged):
    _stage_cycle(staged, source_url="")
    assert load_opportunities(staged, kind="FESTIVAL", today=TODAY) == []


def test_only_the_requested_kind_is_loaded(staged):
    _stage_cycle(staged)
    assert load_opportunities(staged, kind="MARKET_LAB_WIP", today=TODAY) == []


def test_an_unrecorded_premiere_requirement_stays_unknown(staged):
    _stage_cycle(staged)
    assert load_opportunities(staged, kind="FESTIVAL", today=TODAY)[0].premiere_requirement is None


def test_a_verified_ledger_fact_reaches_the_engine(staged):
    """The whole chain: researcher records, reviewer verifies, engine reads."""
    from app.modules.reports import verification_store
    from app.modules.reports.verification_ledger import (
        GATE_FESTIVAL_SECTION,
        SourceClaim,
    )

    _stage_cycle(staged)
    claim = SourceClaim(
        gate=GATE_FESTIVAL_SECTION,
        subject_id="cycle-1",
        field="premiere_requirement",
        value="WORLD",
        source_url="https://example.org/festival/rules",
        source_basis="Entries must not have screened publicly before the festival.",
        verified_on=date(2026, 9, 19),
        verified_by="researcher-a",
    )
    verification_store.record_claims(staged, [claim], apply=True, today=TODAY)

    # Unreviewed, the engine still sees nothing.
    assert load_opportunities(staged, kind="FESTIVAL", today=TODAY)[0].premiere_requirement is None

    verification_store.review(
        staged,
        gate=GATE_FESTIVAL_SECTION,
        subject_id="cycle-1",
        field_name="premiere_requirement",
        reviewer="reviewer-b",
        qa_by="reviewer-c",
        today=TODAY,
    )
    assert (
        load_opportunities(staged, kind="FESTIVAL", today=TODAY)[0].premiere_requirement
        == "WORLD"
    )


def test_an_absent_ledger_does_not_break_the_loader():
    """A report must not fail because the research table is not migrated here."""
    engine = sa.create_engine("sqlite://")
    _run(engine, "r7s8t9u0v1w2_engine_v2_staging_tables.py")
    _run(engine, "s8t9u0v1w2x3_observed_open_cycle.py")
    _stage_cycle(engine)
    assert len(load_opportunities(engine, kind="FESTIVAL", today=TODAY)) == 1


# ── Comparable roles (implementation note section 10) ────────────────────────


def _claim(value):
    return SourcedValue(value, SOURCE, TODAY)


def _project():
    return build_project_dna(
        {
            "format": "feature film",
            "genre": ["Drama"],
            "production_country": "United Kingdom",
        },
        {},
    )


def _comparable(title="A Film", **fields):
    base = dict(id=title, title=title, source_url=SOURCE, verified_on=TODAY)
    base.update(fields)
    return ComparableProfile(**base)


def test_production_evidence_earns_only_the_production_role():
    strategy = match_comparables(
        [
            _comparable(
                format=_claim("feature"),
                budget_gbp=_claim(1_000_000),
                production_countries=_claim(["United Kingdom"]),
            )
        ],
        _project(),
        today=TODAY,
    )
    roles = strategy.recommendations[0].roles
    assert PRODUCTION_COMPARABLE in roles
    assert COMMERCIAL_COMPARABLE not in roles


def test_commercial_evidence_earns_the_commercial_role():
    strategy = match_comparables(
        [_comparable(format=_claim("feature"), genres=_claim(["drama"]))],
        _project(),
        today=TODAY,
    )
    assert COMMERCIAL_COMPARABLE in strategy.recommendations[0].roles


def test_a_verified_company_relationship_confers_the_commercial_role():
    strategy = match_comparables(
        [
            _comparable(
                format=_claim("feature"),
                budget_gbp=_claim(1_000_000),
                production_countries=_claim(["United Kingdom"]),
                relationships=(
                    ComparableRelationship(
                        "COMPANY", "Buyer", "distribution", SOURCE, TODAY
                    ),
                ),
            )
        ],
        _project(),
        today=TODAY,
    )
    assert strategy.recommendations[0].supports_commercial_evidence


def test_a_production_only_comparable_cannot_become_buyer_evidence():
    """The note's guardrail: production similarity is not a commercial claim.

    The title carries a verified company relationship, so the naive reading is
    that this company handled a similar film. It did not — it handled a film
    that is only a PRODUCTION analogue, and the two are different claims.
    """
    from dataclasses import replace

    strategy = match_comparables(
        [
            _comparable(
                format=_claim("feature"),
                budget_gbp=_claim(1_000_000),
                production_countries=_claim(["United Kingdom"]),
            )
        ],
        _project(),
        today=TODAY,
    )
    production_only = replace(
        strategy.recommendations[0],
        roles=(PRODUCTION_COMPARABLE,),
        verified_relationships=(
            ComparableRelationship("COMPANY", "Buyer", "distribution", SOURCE, TODAY),
        ),
    )
    narrowed = replace(strategy, recommendations=(production_only,))

    from app.modules.reports.commercial_strategy import CompanyProfile

    company = CompanyProfile(
        id="Buyer",
        name="Buyer",
        role=_claim("distributor"),
        active=_claim(True),
        formats=_claim(["feature"]),
        genres=_claim(["drama"]),
    )
    result = match_company(company, _project(), today=TODAY, comparables=narrowed)
    assert not any("comparable title history" in reason for reason in result.reasons)


# ── Next Steps sequencing (section 13) ───────────────────────────────────────


def _result(engine_name, steps):
    return EngineResult(
        engine_name=engine_name,
        engine_version="1.0",
        projectfacts_snapshot_id="snap-a",
        projectfacts_version="1",
        next_steps=tuple(steps),
    )


def test_an_unblocking_action_leads_whatever_its_stated_urgency():
    """A producer told to approach distributors first has the wrong list."""
    ordered = sequence_next_steps(
        [
            _result("sales", [{"action": "Approach three distributors", "urgency": "high"}]),
            _result(
                "incentive",
                [{"action": "Supply qualifying spend per territory", "urgency": "medium"}],
            ),
        ]
    )
    assert ordered[0]["action"] == "Supply qualifying spend per territory"


def test_urgency_orders_within_a_dependency_band():
    ordered = sequence_next_steps(
        [
            _result("a", [{"action": "Later", "urgency": "low"}]),
            _result("b", [{"action": "Sooner", "urgency": "blocking"}]),
        ]
    )
    assert [step["action"] for step in ordered] == ["Sooner", "Later"]


def test_an_unrecognised_urgency_is_treated_as_medium_not_as_first():
    ordered = sequence_next_steps(
        [
            _result("a", [{"action": "Odd", "urgency": "whenever"}]),
            _result("b", [{"action": "High", "urgency": "high"}]),
        ]
    )
    assert ordered[0]["action"] == "High"


def test_an_engine_s_own_order_survives_inside_its_band():
    ordered = sequence_next_steps(
        [_result("a", [{"action": "First"}, {"action": "Second"}])]
    )
    assert [step["action"] for step in ordered] == ["First", "Second"]


def test_every_step_is_attributed_and_positioned():
    ordered = sequence_next_steps([_result("festivals", [{"action": "Confirm"}])])
    assert ordered[0]["engine"] == "festivals"
    assert ordered[0]["sequence_position"] == 1


def test_sequencing_adds_no_action_and_drops_none():
    """Section 13 orders actions; it does not invent or remove them."""
    steps = [{"action": f"Do {i}"} for i in range(5)]
    ordered = sequence_next_steps([_result("a", steps)])
    assert {s["action"] for s in ordered} == {s["action"] for s in steps}


# ── The builder attempts every engine ────────────────────────────────────────


def test_the_builder_offers_all_five_engines_to_the_orchestrator(monkeypatch):
    """A comparison exercising only grants tells a reviewer nothing.

    Empty universes are the correct output today; a section the builder never
    attempted is not evidence of anything.
    """
    from app.modules.reports.builder import ReportBuilder
    from app.modules.reports.project_facts_v1 import build_project_facts_snapshot

    class _On:
        REPORT_ORCHESTRATION_V2_ENABLED = True

    monkeypatch.setattr("app.core.config.get_settings", lambda: _On(), raising=True)
    db = sa.create_engine("sqlite://")
    _run(db, "t9u0v1w2x3y4_commercial_staging.py")
    monkeypatch.setattr("app.core.db.engine", db, raising=False)

    metadata = {"script_title": "A Film", "_package": "producer"}
    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = []
    instance.grants_payload = None
    instance.request_metadata = metadata
    instance.project_dna = build_project_dna(metadata, {})
    instance.project_facts_snapshot = build_project_facts_snapshot(
        metadata, instance.project_dna
    )

    report: dict = {}
    instance._attach_orchestration_v2(report)

    engines = set(report["orchestrationV2"]["engine_versions"])
    assert {"festivals", "markets_labs_wip", "comparables", "sales"} <= engines
    # Every universe is empty today, which is the correct output: no cycle is
    # staged and no commercial row is approved until the gates close.
    for section in report["orchestrationV2"]["sections"]:
        for block in section["blocks"]:
            assert block["data"]["eligible_universe_count"] == 0


def test_one_engine_failing_does_not_cost_the_others(monkeypatch):
    from app.modules.reports.builder import ReportBuilder
    from app.modules.reports.project_facts_v1 import build_project_facts_snapshot

    class _On:
        REPORT_ORCHESTRATION_V2_ENABLED = True

    monkeypatch.setattr("app.core.config.get_settings", lambda: _On(), raising=True)
    monkeypatch.setattr("app.core.db.engine", sa.create_engine("sqlite://"), raising=False)

    def _explode(*args, **kwargs):
        raise RuntimeError("catalogue unavailable")

    monkeypatch.setattr(
        "app.modules.reports.commercial_catalogue.load_commercial_catalogue",
        _explode,
        raising=True,
    )

    metadata = {"script_title": "A Film", "_package": "producer"}
    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = []
    instance.grants_payload = None
    instance.request_metadata = metadata
    instance.project_dna = build_project_dna(metadata, {})
    instance.project_facts_snapshot = build_project_facts_snapshot(
        metadata, instance.project_dna
    )

    report: dict = {}
    instance._attach_orchestration_v2(report)

    engines = set(report["orchestrationV2"]["engine_versions"])
    assert "festivals" in engines
    assert "comparables" not in engines
    assert any("commercial" in warning for warning in instance.warnings)
