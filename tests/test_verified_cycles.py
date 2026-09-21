"""Verified research becoming rankable cycles, and the lines that must not.

The parser's value is in what it drops. ``one_of`` iterates its expected value,
so a bare string is compared character by character, fails, and a FAIL is
``INELIGIBLE_CONFIRMED`` — the opportunity leaves the producer's report for a
reason nobody can see. Most of these tests are about that class of silence.

The last group runs the whole path: claims in, cycles out, ``load_opportunities``
reading them back and ``evaluate_opportunity`` ranking them. A cycle this stages
that the kernel calls NOT_ACTIONABLE is a cycle that did nothing.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import alembic
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.modules.reports.opportunity_catalogue import load_opportunities
from app.modules.reports.opportunity_strategy import evaluate_opportunity
from app.modules.reports.project_dna import build_project_dna
from app.modules.reports.verification_ledger import (
    GATE_FESTIVAL_SECTION,
    GATE_MARKET_CYCLE,
    GATE_MARKET_RULE,
    SourceClaim,
)
from app.modules.reports.verified_cycles import build_cycles, parse_rule_line
from scripts.stage_verified_cycles import cycle_id, stage_verified_cycles

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "alembic/versions"
LEDGER_MIGRATION = "u0v1w2x3y4z5_source_verifications.py"
STAGING_MIGRATIONS = (
    "r7s8t9u0v1w2_engine_v2_staging_tables.py",
    "s8t9u0v1w2x3_observed_open_cycle.py",
    "z5a6b7c8d9e0_cycle_premiere_requirement.py",
    "d0e1f2a3b4c5_cycle_state.py",
)
TODAY = date(2026, 9, 20)
READ_ON = date(2026, 9, 18)
PAGE = "https://festival.example.org/submissions"


def _run(engine, filename: str, direction: str) -> None:
    spec = importlib.util.spec_from_file_location(f"_m_{filename}_{direction}", VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        previous = alembic.op
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            getattr(module, direction)()
        finally:
            alembic.op = previous


def _claim(gate: str, subject: str, field_name: str, value, **overrides) -> SourceClaim:
    base = {
        "gate": gate,
        "subject_id": subject,
        "field": field_name,
        "value": value,
        "source_url": PAGE,
        "source_basis": "The official page says so.",
        "verified_on": READ_ON,
        "verified_by": "researcher",
        "review_state": "VERIFIED",
        "reviewed_by": "reviewer",
        "qa_by": "second-reviewer",
    }
    base.update(overrides)
    return SourceClaim(**base)


def _deadlines(value: str, subject: str = "fest-1") -> SourceClaim:
    return _claim(GATE_FESTIVAL_SECTION, subject, "section_deadlines", value)


def _rules(value: str, subject: str = "fest-1") -> SourceClaim:
    return _claim(GATE_FESTIVAL_SECTION, subject, "section_rules", value)


def _cycles(*claims, today: date = TODAY):
    return build_cycles(list(claims), today=today)


# ── Only reviewed claims get this far ────────────────────────────────────────


def test_a_pending_claim_produces_nothing():
    pending = _deadlines("Competition | 2026-11-15")
    pending = SourceClaim(**{**pending.__dict__, "review_state": "PENDING", "reviewed_by": None})
    cycles, problems = _cycles(pending)
    assert cycles == [] and problems == []


def test_self_signed_independent_qa_produces_nothing():
    """The one rule the ledger enforces by identity rather than policy."""
    self_signed = _deadlines("Competition | 2026-11-15")
    self_signed = SourceClaim(**{**self_signed.__dict__, "qa_by": "researcher"})
    cycles, _ = _cycles(self_signed)
    assert cycles == []


# ── Festival deadlines ───────────────────────────────────────────────────────


def test_each_section_becomes_its_own_cycle():
    cycles, problems = _cycles(
        _deadlines("Feature Competition | 2026-11-15\nShorts | 2026-10-01")
    )
    assert problems == []
    assert [(c.section_name, c.cycle_deadline) for c in cycles] == [
        ("Feature Competition", date(2026, 11, 15)),
        ("Shorts", date(2026, 10, 1)),
    ]


def test_a_tiered_window_closes_on_its_last_date():
    """Early bird and final are one fact: the cycle closes on the final one.

    This used to be refused as "not decidable", which dropped every tiered
    festival — and then cascaded, because each section's rules reported no
    deadline to attach to once the deadline line had been thrown away. Around
    a third of the 2026-09-21 staging run's rejections came from here.

    A festival does not stop accepting submissions before its last published
    date, so reading the last one is reading the source, not guessing.
    """
    cycles, problems = _cycles(
        _deadlines("Competition | early bird 2026-09-01, final 2026-11-15")
    )
    assert cycles[0].cycle_deadline == date(2026, 11, 15)
    assert problems == []


def test_the_labelled_closing_tier_wins_over_document_order():
    """A researcher listing tiers out of order must not move a deadline."""
    cycles, _ = _cycles(
        _deadlines("Competition | 2026-11-15 late | 2026-09-01 early")
    )
    assert cycles[0].cycle_deadline == date(2026, 11, 15)


def test_an_unreadable_date_is_still_refused():
    cycles, problems = _cycles(_deadlines("Competition | 2026-13-45"))
    assert cycles == []
    assert "not a real date" in problems[0].reason


def test_a_repeated_date_on_one_line_is_still_one_date():
    cycles, _ = _cycles(_deadlines("Competition | 2026-11-15 (closes 2026-11-15)"))
    assert cycles[0].cycle_deadline == date(2026, 11, 15)


@pytest.mark.parametrize("value", ["NOT_ANNOUNCED", "ROLLING", "UNKNOWN"])
def test_a_real_answer_that_is_not_a_date_still_makes_a_cycle(value):
    """These used to be dropped, and dropping them emptied Section 09.

    "The next call is not announced" is the finding the research asked for.
    There was nowhere to put it, so 132 verified market cycles and every
    rolling festival left the system rather than being reported as what they
    are. The state now travels with the cycle.
    """
    cycles, problems = _cycles(_deadlines(value))
    assert len(cycles) == 1
    assert cycles[0].cycle_state == value
    assert cycles[0].cycle_deadline is None
    # No section: the claim named none, and "All sections" would assert a
    # scope nobody verified.
    assert cycles[0].section_name == ""
    assert problems == []


def test_only_a_rolling_call_is_observed_open():
    """A rolling call was seen open on the day it was read. An unannounced one
    has nothing to have been seen open, and recording a date there would be
    the inference this module exists to avoid."""
    rolling, _ = _cycles(_deadlines("ROLLING"))
    unannounced, _ = _cycles(_deadlines("NOT_ANNOUNCED"))
    assert rolling[0].observed_open_on is not None
    assert unannounced[0].observed_open_on is None


def test_a_section_repeated_with_two_deadlines_is_refused():
    cycles, problems = _cycles(
        _deadlines("Competition | 2026-11-15\ncompetition | 2026-12-01")
    )
    assert len(cycles) == 1
    assert "repeats section" in problems[0].reason


def test_a_line_with_no_section_name_is_refused():
    cycles, problems = _cycles(_deadlines("| 2026-11-15"))
    assert cycles == []
    assert problems[0].reason == "names no section"


# ── Rules ────────────────────────────────────────────────────────────────────


def test_one_of_always_yields_a_list():
    """A string here would be compared character by character and FAIL."""
    rule, problem = parse_rule_line("format one_of feature")
    assert problem is None
    assert rule.expected == ["feature"]


def test_one_of_splits_on_commas_when_it_has_them():
    rule, _ = parse_rule_line("format one_of feature, short")
    assert rule.expected == ["feature", "short"]


def test_a_numeric_operator_needs_a_number():
    rule, _ = parse_rule_line("runtime_minutes at_most 40")
    assert rule.expected == 40
    rule, problem = parse_rule_line("runtime_minutes at_most forty")
    assert rule is None and "needs a number" in problem


def test_prose_given_to_a_comparing_operator_is_refused():
    """``equals`` against a sentence never matches, and never matching is FAIL."""
    rule, problem = parse_rule_line(
        "production_countries equals any country on the programme list; see rules"
    )
    assert rule is None
    assert "use manual_confirmation" in problem


def test_a_long_equals_value_is_refused_even_without_punctuation():
    rule, problem = parse_rule_line("stage equals " + "a" * 60)
    assert rule is None and "too long" in problem


def test_manual_confirmation_carries_the_condition_and_no_expected():
    rule, _ = parse_rule_line(
        "production_countries manual_confirmation eligible region per programme list"
    )
    assert rule.operator == "manual_confirmation"
    assert rule.expected is None
    assert rule.condition.startswith("production_countries manual_confirmation")


def test_manual_confirmation_reads_in_either_order():
    rule, _ = parse_rule_line("manual_confirmation attached_team a named director")
    assert rule.project_field == "attached_team"
    assert rule.operator == "manual_confirmation"


def test_a_field_outside_project_dna_is_refused():
    rule, problem = parse_rule_line("vibe one_of strong")
    assert rule is None and "not a Project DNA field" in problem


def test_an_operator_nobody_implemented_is_refused():
    rule, problem = parse_rule_line("format roughly_like feature")
    assert rule is None and problem == "names no known operator"


def test_the_condition_is_the_researcher_s_own_words():
    line = "runtime_minutes at_most 40"
    rule, _ = parse_rule_line(line)
    assert rule.condition == line


# ── Rules attaching to sections ──────────────────────────────────────────────


def test_a_rule_reaches_only_its_own_section():
    cycles, problems = _cycles(
        _deadlines("Competition | 2026-11-15\nShorts | 2026-10-01"),
        _rules("Shorts | runtime_minutes at_most 40"),
    )
    assert problems == []
    by_section = {c.section_name: c for c in cycles}
    assert by_section["Shorts"].rules[0].expected == 40
    assert by_section["Competition"].rules == ()


def test_a_rule_for_a_section_with_no_deadline_has_nowhere_to_go():
    cycles, problems = _cycles(
        _deadlines("Competition | 2026-11-15"),
        _rules("Panorama | runtime_minutes at_most 40"),
    )
    assert cycles[0].rules == ()
    assert "no verified deadline" in problems[0].reason
    # The sections that DO have a deadline are named. The usual cause is two
    # names for one section across two gates, and this is what lets the person
    # who wrote both settle it without anyone guessing.
    assert "dated sections are: competition" in problems[0].reason


def test_a_premiere_requirement_lands_on_its_section_not_the_festival():
    cycles, problems = _cycles(
        _deadlines("Competition | 2026-11-15\nShorts | 2026-10-01"),
        _rules("Competition | premiere_requirement WORLD"),
    )
    assert problems == []
    by_section = {c.section_name: c.premiere_requirement for c in cycles}
    assert by_section == {"Competition": "WORLD", "Shorts": None}


def test_an_untyped_premiere_requirement_is_refused():
    cycles, problems = _cycles(
        _deadlines("Competition | 2026-11-15"),
        _rules("Competition | premiere_requirement must not have screened anywhere"),
    )
    assert cycles[0].premiere_requirement is None
    assert "WORLD, INTERNATIONAL, NATIONAL or NONE" in problems[0].reason


# ── Markets ──────────────────────────────────────────────────────────────────


def test_a_market_track_becomes_one_cycle_with_no_section():
    cycles, problems = _cycles(
        _claim(GATE_MARKET_CYCLE, "mkt-1", "deadline", "2026-11-30"),
        _claim(GATE_MARKET_RULE, "mkt-1", "hard_gates", "stage one_of development"),
    )
    assert problems == []
    assert cycles[0].kind == "MARKET_LAB_WIP"
    assert cycles[0].section_name == ""
    assert cycles[0].rules[0].expected == ["development"]


def test_a_market_gate_with_no_verified_deadline_still_stages():
    """The overwhelmingly common case: the call is not announced yet.

    All 132 verified MARKET_CYCLE claims in production read this way, and all
    132 were discarded here — which is the whole reason 203 staged market
    tracks produced an empty Section 09. The track is staged with its state
    and its hard gates; the engine reaches NOT_ACTIONABLE, so it counts in the
    universe and stays out of the recommendations.
    """
    cycles, problems = _cycles(
        _claim(GATE_MARKET_CYCLE, "mkt-1", "deadline", "NOT_ANNOUNCED"),
        _claim(GATE_MARKET_RULE, "mkt-1", "hard_gates", "stage one_of development"),
    )
    assert len(cycles) == 1
    assert cycles[0].cycle_state == "NOT_ANNOUNCED"
    assert cycles[0].cycle_deadline is None
    # The gates are kept. They are what the engine will evaluate the day a
    # date appears, and re-researching them then would be waste.
    assert len(cycles[0].rules) == 1
    assert problems == []


# ── Observed-open ────────────────────────────────────────────────────────────


def test_a_future_deadline_read_today_was_observed_open():
    cycles, _ = _cycles(_deadlines("Competition | 2026-11-15"))
    assert cycles[0].observed_open_on == READ_ON


def test_a_deadline_already_past_when_read_was_not():
    """The page was showing a closed call, which is evidence of closure."""
    cycles, _ = _cycles(_deadlines("Competition | 2026-01-15"))
    assert cycles[0].observed_open_on is None


# ── Through the database and back into the kernel ────────────────────────────


def _database(tmp_path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'cycles.db'}")
    _run(engine, LEDGER_MIGRATION, "upgrade")
    for filename in STAGING_MIGRATIONS:
        _run(engine, filename, "upgrade")
    return engine


def _record(engine, kind: str, record_id: str, name: str) -> None:
    with engine.begin() as conn:
        table = sa.Table("engine_handoff_records", sa.MetaData(), autoload_with=conn)
        conn.execute(table.insert(), {
            "kind": kind, "record_id": record_id, "source_version": "test",
            "name": name, "routing": None, "payload_hash": "x" * 64, "payload": {},
            "imported_at": date(2026, 9, 16),
        })


def _record_claims(engine, *claims) -> None:
    from app.modules.reports import verification_store as store

    store.record_claims(engine, list(claims), apply=True, today=TODAY)
    for claim in claims:
        store.review(
            engine, gate=claim.gate, subject_id=claim.subject_id,
            field_name=claim.field, reviewer="reviewer",
            qa_by="second-reviewer", today=TODAY,
        )


def test_staging_refuses_before_the_handoff_records_exist(tmp_path):
    engine = _database(tmp_path)
    _record_claims(engine, _deadlines("Competition | 2026-11-15"))
    with pytest.raises(RuntimeError, match="stage_engine_handoffs"):
        stage_verified_cycles(engine, apply=False, today=TODAY)


def test_a_cycle_for_an_unstaged_record_is_skipped_and_named(tmp_path):
    engine = _database(tmp_path)
    _record(engine, "FESTIVAL", "fest-1", "Example Festival")
    _record_claims(engine, _deadlines("Competition | 2026-11-15", subject="fest-9"))
    result = stage_verified_cycles(engine, apply=False, today=TODAY)
    assert result.unstaged_records == ["FESTIVAL fest-9"]
    assert result.new_cycles == 0


def test_staged_cycles_load_back_and_the_kernel_can_rank_them(tmp_path):
    engine = _database(tmp_path)
    _record(engine, "FESTIVAL", "fest-1", "Example Festival")
    _record_claims(
        engine,
        _deadlines("Feature Competition | 2026-11-15"),
        _rules(
            "Feature Competition | premiere_requirement WORLD\n"
            "Feature Competition | runtime_minutes at_least 60"
        ),
    )
    result = stage_verified_cycles(engine, apply=True, today=TODAY)
    # One rule, not two: the premiere line is the cycle's own requirement rather
    # than a gate the kernel evaluates against a Project DNA field.
    assert result.new_cycles == 1 and result.new_rules == 1

    loaded = load_opportunities(engine, kind="FESTIVAL", today=TODAY)
    assert len(loaded) == 1
    opportunity = loaded[0]
    assert opportunity.name == "Example Festival — Feature Competition"
    assert opportunity.premiere_requirement == "WORLD"
    assert opportunity.observed_open_on == READ_ON

    dna = build_project_dna({"format": "feature", "runtime_minutes": 95}, {})
    recommendation = evaluate_opportunity(opportunity, dna, today=TODAY)
    assert recommendation.eligibility != "NOT_ACTIONABLE", (
        "a staged cycle the kernel will not rank has done nothing"
    )
    assert recommendation.application_status == "OPEN"


def test_rerunning_changes_nothing(tmp_path):
    engine = _database(tmp_path)
    _record(engine, "FESTIVAL", "fest-1", "Example Festival")
    _record_claims(
        engine,
        _deadlines("Competition | 2026-11-15"),
        _rules("Competition | runtime_minutes at_least 60"),
    )
    stage_verified_cycles(engine, apply=True, today=TODAY)
    again = stage_verified_cycles(engine, apply=True, today=TODAY)
    assert (again.new_cycles, again.updated_cycles) == (0, 0)
    assert (again.new_rules, again.removed_rules) == (0, 0)


def test_a_corrected_rule_supersedes_the_one_it_replaces(tmp_path):
    engine = _database(tmp_path)
    _record(engine, "FESTIVAL", "fest-1", "Example Festival")
    _record_claims(
        engine,
        _deadlines("Competition | 2026-11-15"),
        _rules("Competition | runtime_minutes at_least 60"),
    )
    stage_verified_cycles(engine, apply=True, today=TODAY)

    # The ledger never overwrites, so a correction arrives as a new claim. Here
    # it is applied directly, which is what the reviewer's correction becomes.
    with engine.begin() as conn:
        table = sa.Table("source_verifications", sa.MetaData(), autoload_with=conn)
        conn.execute(
            table.update()
            .where(table.c.field == "section_rules")
            .values(value="Competition | runtime_minutes at_least 70")
        )
    result = stage_verified_cycles(engine, apply=True, today=TODAY)
    assert (result.new_rules, result.removed_rules) == (1, 1)

    opportunity = load_opportunities(engine, kind="FESTIVAL", today=TODAY)[0]
    assert [gate.expected for gate in opportunity.gates] == [70]


def test_a_cycle_no_claim_describes_any_more_is_reported_not_deleted(tmp_path):
    engine = _database(tmp_path)
    _record(engine, "FESTIVAL", "fest-1", "Example Festival")
    _record_claims(engine, _deadlines("Competition | 2026-11-15"))
    stage_verified_cycles(engine, apply=True, today=TODAY)
    staged = cycle_id("FESTIVAL", "fest-1", "Competition", date(2026, 11, 15))

    with engine.begin() as conn:
        table = sa.Table("source_verifications", sa.MetaData(), autoload_with=conn)
        conn.execute(
            table.update()
            .where(table.c.field == "section_deadlines")
            .values(value="Competition | 2026-12-01")
        )
    result = stage_verified_cycles(engine, apply=True, today=TODAY)
    assert result.orphaned_cycles == [staged]

    with engine.connect() as conn:
        table = sa.Table("opportunity_cycles", sa.MetaData(), autoload_with=conn)
        assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 2
