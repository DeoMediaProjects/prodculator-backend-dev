"""Programme thresholds must gate, not merely decorate.

The report that prompted this printed "Min. qualifying spend: $1M" one line above
"Est. rebate: $18,380" on a production whose entire qualifying spend was $61,780, and
ranked that territory second. Six places in the codebase read qualifying_spend_min;
five formatted it for display and the sixth compared it for one territory only.

Seventeen of the forty-nine programmes were unreachable at that budget and every one
was still ranked, carded and quoted. These tests exist so no threshold can go back to
being decorative, and so the untestable cases stay untestable rather than passing.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.modules.reports.helpers import best_incentive
from app.modules.reports.programme_eligibility import (
    AVAILABLE,
    UNAVAILABLE,
    UNVERIFIABLE,
    any_unavailable,
    evaluate_programme_eligibility,
    verdict_rank,
)

# The production from the report in the ticket.
EJE = {"budget_gbp": 45840.0, "completion_date": "2026-09-27"}


def floor(amount, currency="USD", **extra):
    return {"qualifying_spend_min": amount, "qualifying_spend_currency": currency, **extra}


def verdict(row, project=EJE):
    return evaluate_programme_eligibility(row, project)["verdict"]


# ── Minimum qualifying spend ─────────────────────────────────────────────────

class TestMinimumQualifyingSpend:
    def test_a_floor_above_the_budget_makes_the_programme_unavailable(self):
        """California: a $1,000,000 floor against a £45,840 budget."""
        assert verdict(floor(1_000_000)) == UNAVAILABLE

    def test_a_floor_below_the_budget_is_fine(self):
        assert verdict(floor(10_000)) == AVAILABLE

    def test_no_floor_stated_is_fine(self):
        """New Mexico. Most programmes are this case and must not be penalised."""
        assert verdict({"qualifying_spend_min": None}) == AVAILABLE
        assert verdict({"qualifying_spend_min": 0}) == AVAILABLE
        assert verdict({}) == AVAILABLE

    def test_the_explanation_shows_the_arithmetic(self):
        """A producer has to be able to check the exclusion, not just be told."""
        result = evaluate_programme_eligibility(floor(1_000_000), EJE)
        assert "USD 1,000,000" in result["explanation"]
        assert "45,840" in result["explanation"]
        assert "741,562" in result["explanation"]  # the shortfall, computed

    def test_an_uncomparable_currency_does_not_pass_the_gate(self):
        """Morocco (MAD) and Serbia (RSD) state floors in currencies no GBP rate is
        held for. Passing them because the comparison failed is how a gate becomes
        decorative."""
        result = evaluate_programme_eligibility(floor(5_000_000, "MAD"), EJE)
        assert result["verdict"] == UNVERIFIABLE
        assert result["rebateIsClaimable"] is False
        assert "no GBP rate is held" in result["explanation"]

    def test_a_missing_budget_does_not_pass_the_gate(self):
        result = evaluate_programme_eligibility(floor(1_000_000), {})
        assert result["verdict"] == UNVERIFIABLE
        assert result["rebateIsClaimable"] is False

    def test_a_dict_shaped_budget_does_not_silently_pass(self):
        """`_budget_gbp` arrives from the datasets as a dict. Passing it through
        unextracted made every budget gate silently untestable, which is the exact
        failure this module removes."""
        assert verdict(floor(1_000_000), {"budget_gbp": {"converted": 45840}}) == UNVERIFIABLE

    @pytest.mark.parametrize("garbage", ["lots", "", [], {}])
    def test_an_unparseable_floor_is_treated_as_no_floor(self, garbage):
        assert verdict({"qualifying_spend_min": garbage}) == AVAILABLE


# ── Budget eligibility ceiling ───────────────────────────────────────────────

class TestBudgetCeiling:
    CEILING = (
        "GBP 23,500,000 total core expenditure - ABOVE THIS, IFTC IS NOT AVAILABLE "
        "AT ALL, must use AVEC instead"
    )

    def test_a_budget_above_the_ceiling_is_unavailable(self):
        """This gate existed only on the calculator path. The report never applied it."""
        row = {"budget_eligibility_ceiling": self.CEILING}
        assert verdict(row, {"budget_gbp": 40_000_000}) == UNAVAILABLE

    def test_a_budget_under_the_ceiling_is_fine(self):
        row = {"budget_eligibility_ceiling": self.CEILING}
        assert verdict(row, {"budget_gbp": 1_000_000}) == AVAILABLE

    def test_the_amount_is_parsed_out_of_the_free_text(self):
        row = {"budget_eligibility_ceiling": self.CEILING}
        result = evaluate_programme_eligibility(row, {"budget_gbp": 40_000_000})
        assert "23,500,000" in result["explanation"]

    def test_an_unreadable_ceiling_is_untested_not_ignored(self):
        row = {"budget_eligibility_ceiling": "see programme guidelines"}
        assert verdict(row, {"budget_gbp": 40_000_000}) == UNVERIFIABLE


# ── Expiry ───────────────────────────────────────────────────────────────────

class TestExpiry:
    def test_a_programme_closing_before_completion_is_unavailable(self):
        assert verdict({"expiry_date": "2026-01-01"}) == UNAVAILABLE

    def test_a_programme_running_past_completion_is_fine(self):
        assert verdict({"expiry_date": "2030-01-01"}) == AVAILABLE

    def test_a_datetime_expiry_does_not_crash_the_comparison(self):
        """The report-killing TypeError was date minus datetime. This gate shares the
        same parser, so it inherits the fix and must keep it."""
        from datetime import datetime, timezone

        assert verdict({"expiry_date": datetime(2030, 1, 1, 9, 30, tzinfo=timezone.utc)}) == AVAILABLE

    def test_no_completion_date_leaves_it_untested(self):
        assert verdict({"expiry_date": "2026-01-01"}, {"budget_gbp": 45840}) == UNVERIFIABLE

    def test_it_falls_back_to_the_filming_start_when_completion_is_absent(self):
        row = {"expiry_date": "2026-01-01"}
        assert verdict(row, {"filming_start_date": "2026-08-30"}) == UNAVAILABLE


# ── Status ───────────────────────────────────────────────────────────────────

class TestStatus:
    @pytest.mark.parametrize("status", ["active", "", None])
    def test_an_active_programme_passes(self, status):
        assert verdict({"status": status}) == AVAILABLE

    @pytest.mark.parametrize("status", ["suspended", "withdrawn", "pending_verification"])
    def test_a_non_active_programme_is_unavailable(self, status):
        assert verdict({"status": status}) == UNAVAILABLE

    def test_no_programme_says_so_plainly(self):
        result = evaluate_programme_eligibility({"status": "no_programme"}, EJE)
        assert result["verdict"] == UNAVAILABLE
        assert "no incentive programme on record" in result["explanation"]


# ── Several gates at once ────────────────────────────────────────────────────

class TestMultipleGates:
    def test_a_definite_failure_outranks_an_untestable_one(self):
        """A programme that definitely fails one gate is unavailable, not merely
        unverifiable, even when a second gate could not be tested."""
        row = floor(1_000_000, "USD", expiry_date="2026-01-01")
        result = evaluate_programme_eligibility(row, {"budget_gbp": 45840})
        assert result["verdict"] == UNAVAILABLE

    def test_every_gate_that_spoke_is_reported(self):
        row = floor(1_000_000, "USD", status="suspended", expiry_date="2026-01-01")
        result = evaluate_programme_eligibility(row, EJE)
        gates = {r["gate"] for r in result["reasons"]}
        assert gates == {"programme status", "minimum qualifying spend", "programme expiry"}
        assert len(result["failedGates"]) == 3

    def test_passed_gates_are_recorded_too(self):
        """Reassurance is worth recording; it is what lets the section say the floor
        was checked rather than absent."""
        result = evaluate_programme_eligibility(floor(10_000), EJE)
        assert result["verdict"] == AVAILABLE
        assert [r["outcome"] for r in result["reasons"]] == ["pass"]


# ── Selection ────────────────────────────────────────────────────────────────

class TestSelectionPrefersUsableProgrammes:
    def test_a_reachable_lower_rate_beats_an_unreachable_higher_one(self):
        """The exact inversion from the ticket: New Mexico 25% must beat California
        35% for this production, because the 35% cannot be claimed at all."""
        rows = [
            {"program": "reachable 25%", "rate_gross": 25, "qualifying_spend_min": None},
            {"program": "floor 35%", "rate_gross": 35, **floor(1_000_000)},
        ]
        assert best_incentive(rows, "Short", EJE)["program"] == "reachable 25%"

    def test_the_same_pair_inverts_again_at_a_budget_that_clears_the_floor(self):
        """Not a blanket demotion: the higher rate wins as soon as it is reachable."""
        rows = [
            {"program": "reachable 25%", "rate_gross": 25, "qualifying_spend_min": None},
            {"program": "floor 35%", "rate_gross": 35, **floor(1_000_000)},
        ]
        assert best_incentive(rows, "Short", {"budget_gbp": 5_000_000})["program"] == "floor 35%"

    def test_rate_still_decides_between_two_reachable_programmes(self):
        rows = [
            {"program": "low", "rate_gross": 20, "qualifying_spend_min": None},
            {"program": "high", "rate_gross": 35, "qualifying_spend_min": None},
        ]
        assert best_incentive(rows, "Short", EJE)["program"] == "high"

    def test_an_available_programme_beats_an_unverifiable_one(self):
        rows = [
            {"program": "clear 20%", "rate_gross": 20, "qualifying_spend_min": None},
            {"program": "no fx rate 40%", "rate_gross": 40, **floor(5_000_000, "MAD")},
        ]
        assert best_incentive(rows, "Short", EJE)["program"] == "clear 20%"

    def test_the_only_programme_is_still_returned_carrying_its_verdict(self):
        """Demoted, never dropped. The territory keeps its place in the report and
        states why, rather than vanishing from it."""
        rows = [{"program": "only", "rate_gross": 35, **floor(1_000_000)}]
        chosen = best_incentive(rows, "Short", EJE)
        assert chosen["program"] == "only"
        assert verdict(chosen) == UNAVAILABLE

    def test_selection_is_unchanged_when_no_project_facts_are_supplied(self):
        """Every existing caller that passes no project keeps its old behaviour: with
        nothing to test against, no programme states a gate it can fail."""
        rows = [
            {"program": "low", "rate_gross": 20},
            {"program": "high", "rate_gross": 40},
        ]
        assert best_incentive(rows)["program"] == "high"


# ── The blanket caveat ───────────────────────────────────────────────────────

class TestCaveatIsDataDriven:
    def test_it_is_raised_while_any_programme_fails_its_thresholds(self):
        rows = [{"qualifying_spend_min": None}, floor(1_000_000)]
        assert any_unavailable(rows, EJE) is True

    def test_it_retires_once_every_programme_clears_them(self):
        assert any_unavailable([{"qualifying_spend_min": None}, floor(10_000)], EJE) is False

    def test_an_untestable_programme_still_raises_it(self):
        assert any_unavailable([floor(5_000_000, "MAD")], EJE) is True

    def test_no_programmes_raises_nothing(self):
        assert any_unavailable([], EJE) is False
        assert any_unavailable(None, EJE) is False


# ── Ranking demotion ─────────────────────────────────────────────────────────

class TestRankingDemotion:
    def test_an_unusable_territory_sorts_below_every_usable_one(self):
        """Score alone put California second. The six scored dimensions measure how
        good a territory is, not whether this production can use its incentive."""
        from app.modules.reports.builder import ReportBuilder

        report = {
            "locationRankings": [
                {"name": "California", "score": 57},
                {"name": "New Mexico", "score": 60},
                {"name": "Romania", "score": 41},
            ],
            "incentiveEstimates": [
                {"territory": "California", "programmeEligibility": {"available": False}},
                {"territory": "New Mexico", "programmeEligibility": {"available": True}},
                {"territory": "Romania", "programmeEligibility": {"available": True}},
            ],
        }
        ReportBuilder.compute_overall_scores(report)
        assert [r["name"] for r in report["locationRankings"]] == [
            "New Mexico", "Romania", "California",
        ]

    def test_the_recommended_territory_is_never_an_unusable_one(self):
        from app.modules.reports.builder import ReportBuilder

        report = {
            "locationRankings": [
                {"name": "California", "score": 90},
                {"name": "New Mexico", "score": 40},
            ],
            "incentiveEstimates": [
                {"territory": "California", "programmeEligibility": {"available": False}},
                {"territory": "New Mexico", "programmeEligibility": {"available": True}},
            ],
            "executiveSummary": {},
        }
        ReportBuilder.compute_overall_scores(report)
        assert report["executiveSummary"]["recommendedTerritory"] == "New Mexico"

    def test_a_missing_verdict_never_demotes_a_territory(self):
        """Absent data must not silently reorder a report. Asserted on the demotion
        set rather than on the final order, because compute_overall_scores recomputes
        `score` from the six dimensions and a fixture without them scores zero."""
        from app.modules.reports.builder import ReportBuilder

        assert ReportBuilder._unusable_territories({
            "incentiveEstimates": [
                {"territory": "A"},
                {"territory": "B", "programmeEligibility": None},
                {"territory": "C", "programmeEligibility": {}},
                {"territory": "D", "programmeEligibility": {"available": True}},
            ],
        }) == set()

    def test_only_an_explicit_false_demotes(self):
        from app.modules.reports.builder import ReportBuilder

        assert ReportBuilder._unusable_territories({
            "incentiveEstimates": [
                {"territory": "Out", "programmeEligibility": {"available": False}},
                {"territory": "In", "programmeEligibility": {"available": True}},
            ],
        }) == {"Out"}

    @pytest.mark.parametrize("payload", [{}, {"incentiveEstimates": None},
                                         {"incentiveEstimates": "nope"},
                                         {"incentiveEstimates": [None, "x", 3]}])
    def test_a_malformed_report_demotes_nothing_rather_than_raising(self, payload):
        from app.modules.reports.builder import ReportBuilder

        assert ReportBuilder._unusable_territories(payload) == set()


class TestVerdictRanking:
    def test_available_outranks_unverifiable_outranks_unavailable(self):
        assert verdict_rank(AVAILABLE) > verdict_rank(UNVERIFIABLE) > verdict_rank(UNAVAILABLE)

    def test_an_unknown_verdict_is_treated_as_unverifiable(self):
        assert verdict_rank("something else") == verdict_rank(UNVERIFIABLE)
        assert verdict_rank(None) == verdict_rank(UNVERIFIABLE)
