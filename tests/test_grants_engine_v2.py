"""Grants & Funding Engine v2: the behavioural contract, and the traps in the data.

These are the acceptance tests and regression cases shipped with the v2 handoff
(05_QA/acceptance_tests.md, 05_QA/regression_cases.json GRT-001..GRT-006) expressed
against the real engine, plus the data traps that make the 253-record master
dangerous to load naively.

WHY SO MANY TESTS ARE ABOUT STRINGS
-----------------------------------
Every value in the master JSON is a string: ``paid_match_eligible`` is ``"True"`` or
``"False"``, ``eligible_formats`` is sometimes ``"[]"`` and sometimes
``"Film; Television"``, and ``last_verified_at`` is a date on 212 records and a
datetime on 41. ``"False"`` is truthy in Python, so a single missing conversion turns
all 49 deliberately-unsurfaced records into live recommendations. Those conversions
are tested here because the cost of getting one wrong is a producer applying to a
fund that closed.

The clock is pinned. Deadline and staleness gates read a date, and a test that reads
the wall clock passes today and fails in March.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.modules.grants import normalise as N
from app.modules.grants.engine import GrantsMatchingService, ProjectFacts
from app.modules.grants.portfolio import select_portfolio
from app.modules.grants.v2_contracts import display_limit

#: Fixed so deadline arithmetic is stable. The master was frozen 2026-09-04.
TODAY = date(2026, 9, 5)

_MASTER = Path(__file__).parent / "data" / "grants_master_v2.json"


def _master_records() -> list[dict]:
    return json.loads(_MASTER.read_text(encoding="utf-8"))["records"]


def _record(**overrides) -> dict:
    """A minimally-valid live grant row, overridden per test."""
    base = {
        "id": "grant-1",
        "canonical_title": "Example Production Fund",
        "funding_body": "Example Body",
        "territory": "United Kingdom",
        "routing": "GRANTS_FUNDS",
        "lifecycle_state": "LIVE",
        "paid_match_eligible": "True",
        "current_status": "open",
        "current_cycle_verified": "True",
        "official_source_verified": "True",
        "record_verified": "True",
        "last_verified_at": "2026-09-01",
        "next_deadline": "2026-11-30",
        "eligible_formats": '["feature"]',
        "production_stage": "production",
        "opportunity_type": "production_fund",
        "official_source": "https://example.org/fund",
    }
    base.update(overrides)
    return base


def _facts(**overrides) -> ProjectFacts:
    defaults = {
        "format": "Feature Film",
        "genres": ["Drama"],
        "budget_usd": 2_000_000,
        "home_country": "United Kingdom",
        "producer_country": "United Kingdom",
        "script_origin": "United Kingdom",
        "ranked_territories": ["United Kingdom"],
    }
    defaults.update(overrides)
    return ProjectFacts(**defaults)


def _evaluate(record: dict, facts: ProjectFacts | None = None):
    return GrantsMatchingService(today=TODAY).evaluate(record, facts or _facts())


# ── the master dataset itself ────────────────────────────────────────────────


class TestMasterDataset:
    def test_master_is_the_frozen_253(self):
        payload = json.loads(_MASTER.read_text(encoding="utf-8"))
        assert payload["record_count"] == 253
        assert len(payload["records"]) == 253
        assert payload["version"] == "Grants Master v2"

    def test_every_status_normalises(self):
        """No record may fall through to an unhandled status.

        The master has 53 distinct raw spellings of ``current_status`` across mixed
        case, embedded dates and three separator styles. A record whose status does
        not normalise would be silently unclassifiable.
        """
        statuses = {N.canonical_status(r["current_status"]) for r in _master_records()}
        assert statuses <= {
            "OPEN_CURRENT_ROUND", "CLOSED_CURRENT_ROUND", "ROLLING", "UPCOMING",
            "CYCLE_BASED", "CURRENT_PROGRAMME", "SUSPENDED", "CONDITIONAL",
            "INVITE_ONLY", "ROUTED_OUT", "UNKNOWN",
        }

    def test_booleans_are_strings_and_false_is_not_truthy(self):
        """The single most dangerous property of this dataset.

        ``paid_match_eligible`` is the STRING "False" on 49 records. Read without
        conversion it is truthy, and all 49 become live recommendations.
        """
        records = _master_records()
        assert sum(1 for r in records if N.parse_bool(r["paid_match_eligible"]) is True) == 204
        assert sum(1 for r in records if N.parse_bool(r["paid_match_eligible"]) is False) == 49
        assert N.parse_bool("False") is False
        assert N.parse_bool("") is None

    def test_last_verified_at_parses_both_stored_formats(self):
        """Date on 212 records, datetime on 41, one column."""
        assert all(N.parse_date(r["last_verified_at"]) for r in _master_records())
        assert N.parse_date("2026-09-03") == date(2026, 9, 3)
        assert N.parse_date("2026-07-01T00:00:00") == date(2026, 7, 1)


# ── unknown, and its three spellings ─────────────────────────────────────────


class TestUnknownHandling:
    @pytest.mark.parametrize("value", ["", "[]", None, "  "])
    def test_unknown_spellings(self, value):
        assert N.is_unknown(value) is True

    def test_zero_is_an_answer_not_an_absence(self):
        """DATA_README: blank means not stated. Zero means somebody stated zero."""
        assert N.is_unknown(0) is False
        assert N.is_unknown(False) is False

    def test_empty_format_list_does_not_mean_funds_nothing(self):
        """``"[]"`` appears on 60 records and is a serialisation artefact.

        Read as an empty list it says "this fund accepts no formats" and rejects every
        project. It has to read as "not stated" so the gate goes untested.
        """
        assert N.parse_format_list("[]") is None
        assert N.parse_format_list("") is None

    def test_prose_format_lists_parse(self):
        assert N.parse_format_list("Film; Television") == ["feature", "tv_series"]
        assert N.parse_format_list('["feature", "documentary"]') == ["feature", "documentary"]
        # A display label must still match the canonical intake format.
        assert N.parse_format_list("Short Film") == ["short"]

    def test_deadline_prose_never_becomes_a_date(self):
        """Logic Guide §4: do not invent a date from "annual" or TBC."""
        assert N.parse_deadline("tbc_2027") == ("TBC", None)
        assert N.parse_deadline("2027-02") == ("ISO_MONTH_PARTIAL", None)
        assert N.parse_deadline("rolling") == ("ROLLING", None)
        assert N.parse_deadline("2026-11-30") == ("ISO_DATE", date(2026, 11, 30))

    def test_prose_amounts_never_become_numbers(self):
        """Several source amounts are explicit anti-instructions.

        "do not hard-code €1.2m without current official cap evidence" contains a
        number that a regex would extract — precisely the figure the source forbids.
        """
        text, value, _ = N.parse_amount(
            "Selective amount determined per project; do not hard-code EUR 1.2m "
            "without current official cap evidence"
        )
        assert value is None and text
        assert N.parse_amount("50000")[1] == 50000.0
        assert N.parse_amount("")[0] is None

    def test_total_call_pool_is_flagged_not_treated_as_a_cap(self):
        """acceptance_tests: a pool is never shown as a per-project maximum."""
        _, value, is_pool = N.parse_amount(
            "Brazil FSA approximately BRL1.5m total call; Argentina side USD300k"
        )
        assert is_pool is True
        assert value is None


# ── hard gates ───────────────────────────────────────────────────────────────


class TestHardGates:
    def test_wrong_format_is_rejected_before_scoring(self):
        """acceptance_tests: "Wrong format -> Hard reject before scoring"."""
        result = _evaluate(_record(eligible_formats='["documentary"]'))
        assert result.eligibility_status == "INELIGIBLE"
        assert result.raw_score == 0.0
        assert not result.score_components
        assert any(g.reason_code == "GATE_FORMAT_MISMATCH" for g in result.hard_gate_results)

    def test_unstated_format_does_not_reject(self):
        """95 records state no format. Failing closed would delete a third of them."""
        result = _evaluate(_record(eligible_formats="[]"))
        assert result.eligibility_status != "INELIGIBLE"

    def test_closed_round_never_surfaces(self):
        """acceptance_tests: "A closed current round never appears"."""
        result = _evaluate(_record(current_status="CLOSED_CURRENT_ROUND"))
        assert result.eligibility_status == "INELIGIBLE"
        assert any(g.reason_code == "GATE_CYCLE_CLOSED" for g in result.hard_gate_results)

    def test_passed_deadline_is_excluded(self):
        result = _evaluate(_record(next_deadline="2026-01-30"))
        assert result.eligibility_status == "INELIGIBLE"
        assert any(g.reason_code == "GATE_DEADLINE_PASSED" for g in result.hard_gate_results)

    def test_rolling_stays_rolling_and_stays_actionable(self):
        """known_edge_cases: display Rolling, never fabricate a days-until count."""
        result = _evaluate(_record(next_deadline="rolling", recurrence="rolling"))
        assert result.eligibility_status != "INELIGIBLE"
        assert result.display_fields.deadline == "Rolling"
        assert result.display_fields.days_until_deadline is None

    def test_nationality_failure_cannot_be_rescued_by_a_high_score(self):
        """acceptance_tests, stated verbatim as a requirement.

        The fund is in a territory the producer has no route to, but matches on genre
        and format — signals that would otherwise score well.
        """
        result = _evaluate(_record(
            territory="Japan", nationality_required=True,
            genre_tags=["Drama"], eligible_formats='["feature"]',
        ))
        assert result.eligibility_status == "INELIGIBLE"
        assert result.raw_score == 0.0
        assert any(g.reason_code == "GATE_NATIONALITY_NO_ROUTE"
                   for g in result.hard_gate_results)

    def test_incentive_record_is_routed_away_from_grants(self):
        """acceptance_tests: "A tax incentive/rebate record is routed away"."""
        result = _evaluate(_record(routing="INCENTIVE_ENGINE"))
        assert result.eligibility_status == "INELIGIBLE"
        assert any(g.reason_code == "GATE_ROUTING_NOT_GRANTS"
                   for g in result.hard_gate_results)

    @pytest.mark.parametrize("state,code", [
        ("ARCHIVED", "GATE_RECORD_ARCHIVED"),
        ("RECLASSIFIED", "GATE_RECORD_RECLASSIFIED"),
        ("SPLIT_PARENT", "GATE_SPLIT_PARENT_RETIRED"),
    ])
    def test_retained_but_unmatched_lifecycle_states(self, state, code):
        """Archived/reclassified/split rows are kept for audit and never matched.

        known_edge_cases: "never return the parent and a child in the same match
        universe".
        """
        result = _evaluate(_record(lifecycle_state=state))
        assert result.eligibility_status == "INELIGIBLE"
        assert any(g.reason_code == code for g in result.hard_gate_results)

    def test_paid_match_eligible_false_is_excluded(self):
        result = _evaluate(_record(paid_match_eligible="False"))
        assert result.eligibility_status == "INELIGIBLE"

    def test_unverified_cycle_is_demoted_not_deleted(self):
        """Logic Guide §3, and the single highest-impact rule on this dataset.

        124 of the 204 paid-match-eligible records have no current-cycle verification.
        Excluding them empties the section; promoting them asserts 124 open rounds
        nobody checked. The contract's answer is a third status.
        """
        result = _evaluate(_record(current_cycle_verified=""))
        assert result.eligibility_status == "CURRENT_CYCLE_UNVERIFIED"
        assert result.is_presentable is True
        assert result.is_actionable is False
        assert any("not verified" in c.lower() for c in result.caveats)

    def test_budget_gate_only_applies_to_stated_bounds(self):
        """Developer Guide §5: apply only verified min/max budget fields."""
        over = _evaluate(_record(budget_min_usd=10_000, budget_max_usd=100_000))
        assert over.eligibility_status == "INELIGIBLE"
        unstated = _evaluate(_record())
        assert unstated.eligibility_status != "INELIGIBLE"

    def test_derived_stage_mismatch_does_not_exclude(self):
        """No intake field asks for stage, so it is inferred — and §5 forbids gating
        on an inferred fact. A mismatch qualifies the record instead."""
        result = _evaluate(
            _record(production_stage="distribution"),
            _facts(production_stage="development", production_stage_declared=False),
        )
        assert result.eligibility_status != "INELIGIBLE"

    def test_declared_stage_mismatch_does_exclude(self):
        result = _evaluate(
            _record(production_stage="distribution"),
            _facts(production_stage="development", production_stage_declared=True),
        )
        assert result.eligibility_status == "INELIGIBLE"
        assert any(g.reason_code == "GATE_STAGE_MISMATCH" for g in result.hard_gate_results)


# ── scoring and reason codes ─────────────────────────────────────────────────


class TestScoring:
    def test_reference_weights_are_preserved(self):
        """Developer Guide §6 lists the signals v1 already implemented.

        Territory considered +3, script origin +3, genre +2, specialised format +1,
        verified budget fit +2 — for a UK feature drama against a UK feature-only
        drama fund with a matching budget window.
        """
        result = _evaluate(_record(
            genre_tags=["Drama"], budget_min_usd=1_000_000, budget_max_usd=5_000_000,
        ))
        codes = {c.reason_code: c.points for c in result.score_components}
        assert codes["TERRITORY_CONSIDERED"] == 3.0
        assert codes["SCRIPT_ORIGIN_TERRITORY"] == 3.0
        assert codes["GENRE_OVERLAP"] == 2.0
        assert codes["SPECIALISED_FORMAT"] == 1.0
        assert codes["BUDGET_FIT_VERIFIED"] == 2.0
        assert result.raw_score == 11.0

    def test_every_component_carries_a_reason_code_and_a_sentence(self):
        """§15 definition of done: reason codes persisted in the payload."""
        result = _evaluate(_record(genre_tags=["Drama"]))
        assert result.score_components
        for component in result.score_components:
            assert component.reason_code and component.points is not None
            assert component.detail
            assert component.weight_version

    def test_global_and_continent_never_stack_with_a_territory_hit(self):
        """A fund that is already yours does not also score for being open to all."""
        result = _evaluate(_record(territory="Global"),
                           _facts(ranked_territories=["Global"], script_origin="Global"))
        codes = {c.reason_code for c in result.score_components}
        assert "GLOBAL_OPEN" not in codes

    def test_ineligible_records_carry_no_score(self):
        """Logic Guide §5: do not assign 0 and keep it in the same ranking list."""
        result = _evaluate(_record(current_status="closed_current_round"))
        assert result.raw_score == 0.0
        assert result.portfolio_rank is None
        assert result.score_components == []


# ── caveats and the narrative ladder ─────────────────────────────────────────


class TestNarrativeSafety:
    def test_every_match_says_it_is_not_committed_finance(self):
        result = _evaluate(_record())
        assert any("not committed finance" in c.lower() for c in result.caveats)

    def test_unverified_amount_is_never_rendered_as_zero(self):
        """acceptance_tests: "null amount/deadline never renders as 0"."""
        result = _evaluate(_record(max_amount=""))
        assert result.display_fields.amount is None
        assert result.display_fields.amount_value is None

    def test_absent_deadline_is_not_invented_as_rolling(self):
        result = _evaluate(_record(next_deadline="", recurrence="annual"))
        assert result.display_fields.deadline is None


# ── portfolio and entitlement ────────────────────────────────────────────────


class TestPortfolioAndEntitlement:
    def test_entitlements_match_the_contract_file(self):
        assert display_limit("single") == 5
        assert display_limit("professional") == 5
        assert display_limit("producer") == 10
        assert display_limit("studio") == 10

    def test_unknown_package_gets_the_most_conservative_depth(self):
        assert display_limit(None) == 5
        assert display_limit("enterprise-tier-that-does-not-exist") == 5

    def test_raw_score_is_never_modified_by_ranking(self):
        """Developer Guide §10: preserve raw score and portfolio rank separately."""
        records = [_record(id=f"g{i}", canonical_title=f"Fund {i}",
                           funding_body=f"Body {i}") for i in range(6)]
        service = GrantsMatchingService(today=TODAY)
        results = [r for r in service.match(records, _facts()) if r.is_presentable]
        before = {r.opportunity_id: r.raw_score for r in results}
        ranked = select_portfolio(results, project_stage=None)
        assert {r.opportunity_id: r.raw_score for r in ranked} == before
        assert [r.portfolio_rank for r in ranked] == list(range(1, len(ranked) + 1))

    def test_ranking_is_deterministic(self):
        records = _master_records()
        facts = _facts()
        first = GrantsMatchingService(today=TODAY).build_payload(records, facts, "producer")
        second = GrantsMatchingService(today=TODAY).build_payload(records, facts, "producer")
        assert [r.opportunity_id for r in first.recommendations] == \
               [r.opportunity_id for r in second.recommendations]

    def test_diversification_never_puts_a_lower_score_first(self):
        """Balance defers near-duplicates; it does not overrule the evidence."""
        payload = GrantsMatchingService(today=TODAY).build_payload(
            _master_records(), _facts(), "producer")
        scores = [r.raw_score for r in payload.recommendations]
        assert scores == sorted(scores, reverse=True)


# ── the shipped regression cases ─────────────────────────────────────────────


class TestRegressionCases:
    """05_QA/regression_cases.json, GRT-001..GRT-006."""

    def test_grt_005_closed_round_retained_internally_but_not_surfaced(self):
        records = [_record(id="open-1"),
                   _record(id="closed-1", canonical_title="Closed Fund",
                           current_status="closed_current_round")]
        service = GrantsMatchingService(today=TODAY)
        payload = service.build_payload(records, _facts(), "producer")
        shown = {r.opportunity_id for r in payload.recommendations}
        assert "closed-1" not in shown
        # Retained internally: it was evaluated and its reason recorded.
        assert payload.ineligible_count == 1

    def test_grt_006_same_universe_different_depth(self):
        """"Single=5 Professional=5 Producer=10 Studio=10; underlying eligible IDs
        and scores identical"."""
        records = _master_records()
        facts = _facts()
        service = GrantsMatchingService(today=TODAY)
        payloads = {p: service.build_payload(records, facts, p)
                    for p in ("single", "professional", "producer", "studio")}

        counts = {p: len(v.recommendations) for p, v in payloads.items()}
        assert counts == {"single": 5, "professional": 5, "producer": 10, "studio": 10}

        universes = {p: v.all_qualified_match_ids for p, v in payloads.items()}
        assert len({tuple(u) for u in universes.values()}) == 1

        # The cheaper tier sees a strict prefix of the richer one — the same ranking,
        # read less far down.
        single = [r.opportunity_id for r in payloads["single"].recommendations]
        producer = [r.opportunity_id for r in payloads["producer"].recommendations]
        assert producer[:5] == single

    def test_grt_003_feature_only_funds_excluded_for_a_documentary(self):
        records = [
            _record(id="feature-only", eligible_formats='["feature"]'),
            _record(id="doc-fund", canonical_title="Doc Fund",
                    eligible_formats='["documentary"]'),
        ]
        payload = GrantsMatchingService(today=TODAY).build_payload(
            records, _facts(format="Documentary"), "producer")
        shown = {r.opportunity_id for r in payload.recommendations}
        assert shown == {"doc-fund"}

    def test_grt_004_co_production_required_needs_a_route(self):
        record = _record(co_production_required=True)
        sole = _evaluate(record, _facts(co_production_status="sole_producer"))
        assert sole.eligibility_status == "INELIGIBLE"

        treaty = _evaluate(record, _facts(co_production_status="co_production_treaty"))
        assert treaty.eligibility_status != "INELIGIBLE"
        # "route identified != certification != award"
        assert any("certification" in c.lower() for c in treaty.caveats)


# ── the payload the report consumes ──────────────────────────────────────────


class TestReportPayload:
    def test_payload_states_n_shown_from_m_eligible(self):
        """acceptance_tests: "The report shows N shown from M eligible opportunities"."""
        payload = GrantsMatchingService(today=TODAY).build_payload(
            _master_records(), _facts(), "producer")
        assert payload.eligible_match_count > payload.display_limit
        assert payload.narrative_context.summary_statement == (
            f"{len(payload.recommendations)} shown from "
            f"{payload.eligible_match_count} eligible funding opportunities."
        )

    def test_payload_never_claims_committed_finance(self):
        payload = GrantsMatchingService(today=TODAY).build_payload(
            _master_records(), _facts(), "producer")
        assert payload.narrative_context.not_committed_finance is True

    def test_payload_serialises_with_the_schema_spelling(self):
        """``hard_gate_results[].pass`` is a Python keyword and must still be ``pass``
        on the wire, or the payload stops matching matching_result_schema.json."""
        payload = GrantsMatchingService(today=TODAY).build_payload(
            [_record()], _facts(), "producer")
        wire = payload.as_payload_dict()
        gate = wire["recommendations"][0]["hard_gate_results"][0]
        assert "pass" in gate and "passed" not in gate

    def test_payload_has_every_schema_required_key(self):
        payload = GrantsMatchingService(today=TODAY).build_payload(
            [_record()], _facts(), "producer").as_payload_dict()
        for key in ("database_version", "eligible_match_count", "display_limit",
                    "recommendations", "narrative_context"):
            assert key in payload
        for key in ("opportunity_id", "eligibility_status", "hard_gate_results",
                    "raw_score", "score_components", "portfolio_rank",
                    "match_reasons", "caveats", "verification", "display_fields"):
            assert key in payload["recommendations"][0]


# ── the legacy path ──────────────────────────────────────────────────────────


class TestCutoverReview:
    """migration_instructions.md step 7: the semantic duplicate review, as behaviour."""

    def test_records_held_for_review_do_not_match(self):
        """Rows outside the frozen inventory, or duplicates of rows inside it, are
        retained for audit and excluded from matching.

        Nine of the original 114 appeared in none of the five disposition files and
        are carried in the master under renamed identities ("BFI Film Fund —
        Development" -> "BFI National Lottery Development Funding"). Left live, a
        producer sees the same fund twice under two names, one of them unverified.
        """
        result = _evaluate(_record(lifecycle_state="NEEDS_REVIEW"))
        assert result.eligibility_status == "INELIGIBLE"
        assert any(g.reason_code == "GATE_RECORD_NEEDS_REVIEW"
                   for g in result.hard_gate_results)

    def test_a_held_record_keeps_its_data(self):
        """Held is not deleted. The row still renders its identity, so an admin
        reviewing it can see what they are deciding about."""
        result = _evaluate(_record(lifecycle_state="NEEDS_REVIEW"))
        assert result.display_fields.title == "Example Production Fund"
        assert result.opportunity_id == "grant-1"


class TestTerritoryScopeBug:
    """A national fund filed under one of its own provinces disappears entirely.

    Found in production data: both National Film and Video Foundation records — South
    Africa's national film funding body — were filed under "Western Cape" and
    "Gauteng". Because they also carry nationality_required, the nationality gate found
    no route from a South African applicant to a Western Cape one and excluded them
    outright. South Africa's national funder was invisible to South African producers,
    and the report could not say so, because an excluded record produced no output at
    all before v2.

    The same shape hit Harold Greenberg (national Canadian fund filed under British
    Columbia). Fixed in migration c5d6e7f8a9b0, each correction taken from the record's
    own eligibility text.
    """

    def _nfvf(self, territory: str) -> dict:
        return _record(
            canonical_title="National Film and Video Foundation — Production Funding",
            funding_body="National Film and Video Foundation (NFVF) South Africa",
            territory=territory,
            nationality_required=True,
            genre_tags=["Drama"],
        )

    def test_province_scoped_national_fund_is_excluded_from_its_own_country(self):
        """The bug, pinned. Delete this test only when the data model stops relying on
        territory string equality."""
        facts = _facts(home_country="South Africa", producer_country="South Africa",
                       script_origin="South Africa", ranked_territories=["South Africa"])
        result = _evaluate(self._nfvf("Western Cape"), facts)
        assert result.eligibility_status == "INELIGIBLE"
        assert any(g.reason_code == "GATE_NATIONALITY_NO_ROUTE"
                   for g in result.hard_gate_results)

    def test_correctly_scoped_national_fund_matches_its_own_country(self):
        facts = _facts(home_country="South Africa", producer_country="South Africa",
                       script_origin="South Africa", ranked_territories=["South Africa"])
        result = _evaluate(self._nfvf("South Africa"), facts)
        assert result.eligibility_status != "INELIGIBLE"
        codes = {c.reason_code for c in result.score_components}
        assert "NATIONALITY_HOME" in codes
        assert "TERRITORY_CONSIDERED" in codes

    def test_every_mis_scoped_master_record_is_covered_by_the_correction(self):
        """The master file still carries the wrong territories — and must.

        01_DATA/prodculator_grants_master_v2.json is the client's frozen inventory and
        tests/data/grants_master_v2.json is a byte-for-byte copy of it, so neither is
        edited to fix data. The correction lives in migration c5d6e7f8a9b0 instead.

        What this test guards is that the two stay in step: every record in the master
        whose own eligibility text contradicts its territory must appear in the
        migration's fix list. A future data refresh that introduces a new one fails
        here rather than silently shipping a fund nobody in its own country can see.
        """
        # Loaded by path: alembic/versions has no __init__.py, and `import alembic`
        # resolves to the installed library rather than this repo's migrations.
        import importlib.util

        migration_path = (
            Path(__file__).parent.parent / "alembic" / "versions"
            / "c5d6e7f8a9b0_fix_national_funds_filed_subnationally.py"
        )
        spec = importlib.util.spec_from_file_location("_territory_fix", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        corrected_titles = {title for title, _wrong, _right in migration._FIXES}
        offenders = []
        for record in _master_records():
            territory = (record.get("territory") or "").strip()
            body = record.get("funding_body") or ""
            summary = str(record.get("eligibility_summary") or "")
            # A body naming a country, filed under a region of that country, while its
            # own eligibility text names the country.
            if territory in ("Western Cape", "Gauteng", "KwaZulu-Natal") and \
                    "South Africa" in body and "South Africa" in summary:
                offenders.append(record.get("canonical_title"))

        uncovered = [title for title in offenders if title not in corrected_titles]
        assert uncovered == [], (
            f"mis-scoped fund(s) with no correction in c5d6e7f8a9b0: {uncovered}"
        )
        # And the correction must not have drifted into fixing things that are fine.
        assert offenders, "scan found nothing — the heuristic has stopped working"


class TestLegacyPathIsDead:
    def test_score_70_helper_cannot_execute(self):
        """Developer Guide §14: "Legacy score-70 helper: Must not execute in v2 path".

        It returned every fund with a hardcoded matchScore of 70, no gates, and an
        invented "Rolling" deadline. It raises now rather than being deleted, so an
        accidental caller fails loudly instead of being served plausible fiction.
        """
        from app.modules.reports.service import ReportService

        service = ReportService.__new__(ReportService)
        with pytest.raises(NotImplementedError, match="Grants Engine v2"):
            service._find_grants(None, [])
