"""One eligibility status, and every section of the report obeying it.

The report used to disagree with itself: it called New York's 30% the strongest rate
available, ranked New York first partly on that strength, drew a waterfall
subtracting the rebate, said the producer "qualifies outright" — and then, in the
tax-incentive section, said short-film eligibility for that same programme was
unverified.

Every one of those came from a different check. Producer structure, minimum spend,
format acceptance and bankability were each answered independently, so whichever a
section happened to consult became that section's answer. resolve_project_incentive
combines them once with a fixed precedence, and these tests pin both the precedence
and the sections that consume it.
"""
from __future__ import annotations

import pytest

from app.modules.reports.builder import ReportBuilder
from app.modules.reports.project_incentive import (
    CONDITIONAL,
    ELIGIBLE,
    INELIGIBLE,
    UNVERIFIED,
    resolve_project_incentive,
)
from app.modules.reports.service import ReportService

BUDGET_GBP = 44_490.0
PROJECT = {"budget_gbp": BUDGET_GBP}


def prog(territory="New York", rate=30, **extra):
    return {
        "territory": territory,
        "program": f"{territory} programme",
        "program_name": f"{territory} programme",
        "rate_gross": rate,
        "status": "active",
        "is_supplementary": False,
        "currency": "USD",
        "parent_territory": None,
        **extra,
    }


VERIFIED_ELIGIBLE = {"format_eligibility_status": "verified",
                     "applicable_formats": ["feature", "short"]}
VERIFIED_INELIGIBLE = {"format_eligibility_status": "verified",
                       "applicable_formats": ["feature"]}
CONDITION = {"format_eligibility_status": "conditional",
             "format_conditions": "Allowed if runtime is at least 5 minutes."}
HARD_BUDGET_FAIL = {"qualifying_spend_min": 300_000, "qualifying_spend_currency": "USD"}


def status(row, project=None, fmt="Short"):
    return resolve_project_incentive(row, project or PROJECT, production_format=fmt)


def financials(rows, fmt="Short", runtime=None):
    service = ReportService.__new__(ReportService)
    datasets = {
        "_budget_gbp": {"converted": BUDGET_GBP},
        "_budget_amount": 60_000,
        "_budget_currency": "USD",
        "_production_format": fmt,
        "_runtime_minutes": runtime,
        "_fx_rates_from_budget": {},
        "incentives": rows,
    }
    ReportService._pre_compute_territory_financials(service, datasets)
    return datasets.get("_territory_financials", {})


class _StubContext:
    """Only the attributes _classify_estimate reads. Exercising the real readiness
    context would drag in the whole report; the check under test is the eligibility
    wording, not the surrounding assessment."""
    profile = {}
    today = None
    completion_date = None


def money(text):
    return float(str(text).replace("$", "").replace("£", "").replace(",", ""))


# ── A: verified eligible ─────────────────────────────────────────────────────

class TestA_VerifiedEligible:
    def test_it_participates_in_everything(self):
        result = status(prog(**VERIFIED_ELIGIBLE))
        assert result["status"] == ELIGIBLE
        assert result["canAffectNetCost"] and result["canAffectRanking"]
        assert result["canBeRecommended"]

    def test_the_rebate_reduces_the_net_cost(self):
        f = financials([prog(**VERIFIED_ELIGIBLE)])["New York"]
        assert money(f["net_rebate"]) > 0
        assert money(f["net_budget"]) < money(f["total_budget"])

    def test_the_waterfall_keeps_its_subtraction(self):
        f = financials([prog(**VERIFIED_ELIGIBLE)])["New York"]
        assert f["incentive_is_confirmed"] is True


# ── B: format unknown ────────────────────────────────────────────────────────

class TestB_FormatUnknown:
    def test_the_final_status_is_unverified(self):
        assert status(prog())["status"] == UNVERIFIED

    def test_confirmed_is_zero_and_net_cost_is_untouched(self):
        f = financials([prog()])["New York"]
        assert money(f["net_rebate"]) == 0
        assert money(f["net_budget"]) == money(f["total_budget"])

    def test_the_potential_figure_survives_but_stays_separate(self):
        f = financials([prog()])["New York"]
        assert money(f["potential_net_rebate"]) > 0
        assert f["show_potential_incentive"] is True

    def test_it_may_not_affect_ranking_or_be_recommended(self):
        result = status(prog())
        assert result["canAffectRanking"] is False
        assert result["canBeRecommended"] is False


# ── C: verified ineligible ───────────────────────────────────────────────────

class TestC_VerifiedIneligible:
    def test_no_incentive_is_available_and_none_is_illustrated(self):
        result = status(prog(**VERIFIED_INELIGIBLE))
        assert result["status"] == INELIGIBLE
        assert result["showPotentialAmount"] is False

    def test_neither_confirmed_nor_potential_carries_a_figure(self):
        f = financials([prog(**VERIFIED_INELIGIBLE)])["New York"]
        assert money(f["net_rebate"]) == 0
        assert money(f["potential_net_rebate"]) == 0


# ── D / E: conditional ───────────────────────────────────────────────────────

class TestD_ConditionalUnresolved:
    def test_it_is_conditional_and_confirms_nothing(self):
        result = status(prog(**CONDITION))
        assert result["status"] == CONDITIONAL
        assert result["canAffectNetCost"] is False
        assert result["showPotentialAmount"] is True

    def test_the_net_cost_is_untouched(self):
        f = financials([prog(**CONDITION)])["New York"]
        assert money(f["net_budget"]) == money(f["total_budget"])


class TestE_ConditionalSatisfied:
    def test_a_satisfied_condition_becomes_eligible(self):
        result = status(prog(**CONDITION), {**PROJECT, "runtime_minutes": 12})
        assert result["status"] == ELIGIBLE
        assert result["canAffectNetCost"] is True

    def test_and_the_rebate_participates(self):
        f = financials([prog(**CONDITION)], runtime=12)["New York"]
        assert money(f["net_rebate"]) > 0


# ── F: hard failure precedence ───────────────────────────────────────────────

class TestF_HardBudgetFailureWins:
    def test_a_budget_failure_outranks_an_unknown_format(self):
        """Louisiana: $60,000 budget against a $300,000 minimum, format unknown.
        The budget answer is known, so the status is ineligible rather than
        unverified — and an ineligible programme illustrates nothing."""
        result = status(prog("Louisiana", 25, **HARD_BUDGET_FAIL))
        assert result["status"] == INELIGIBLE
        assert result["showPotentialAmount"] is False

    def test_no_potential_figure_is_printed_beside_not_available(self):
        f = financials([prog("Louisiana", 25, **HARD_BUDGET_FAIL)])["Louisiana"]
        assert money(f["potential_net_rebate"]) == 0
        assert money(f["net_rebate"]) == 0

    def test_the_reason_names_the_budget_not_the_format(self):
        result = status(prog("Louisiana", 25, **HARD_BUDGET_FAIL))
        assert "minimum qualifying spend" in " ".join(result["reasons"]).lower()


# ── G: producer eligibility cannot outvote an unknown format ─────────────────

class TestG_ProducerCannotOverrideFormat:
    def test_a_structurally_fine_producer_still_yields_unverified(self):
        """spv_eligible and no nationality restriction means the producer side is
        satisfied. That must not promote the whole status."""
        row = prog(spv_eligible=True, nationality_requirements=None)
        assert status(row)["status"] == UNVERIFIED

    def test_readiness_does_not_say_qualified_outright(self):
        from app.modules.reports.readiness import _classify_estimate

        _grade, reasons = _classify_estimate({
            "eligibilityStatus": "qualified",
            "bankabilityLabel": "BANKABLE",
            "incentiveIsConfirmed": False,
            "incentiveEligibilityStatus": "unverified",
            "incentiveEligibilityReasons": ["Short-film eligibility is not established."],
            "lastUpdated": "2026-08-01",
        }, _StubContext())
        joined = " ".join(reasons).lower()
        assert "qualifies outright" not in joined
        assert "structural requirements appear satisfied" in joined


# ── I: ranking ───────────────────────────────────────────────────────────────

class TestI_RankingIsNotInflated:
    def test_an_unverified_dimension_is_unscored_not_rewarded(self):
        """The whole point: a 30% programme nobody has confirmed this project can
        use must not beat a verified 20% one on the strength of the bigger number."""
        weights = {"incentiveStrength": 0.30, "incentiveReliability": 0.15,
                   "costEfficiency": 0.20, "currencyAdvantage": 0.15,
                   "crewDepth": 0.10, "infrastructure": 0.10}
        base = {"incentiveReliability": 90, "costEfficiency": 50,
                "currencyAdvantage": 33, "crewDepth": 62, "infrastructure": 60}

        unverified_strong = ReportBuilder._weighted_score(
            {**base, "incentiveStrength": None}, weights, 0)
        verified_weaker = ReportBuilder._weighted_score(
            {**base, "incentiveStrength": 65}, weights, 0)
        assert verified_weaker > unverified_strong

    def test_a_verified_exclusion_scores_zero_rather_than_neutral(self):
        weights = {"incentiveStrength": 1.0}
        assert ReportBuilder._weighted_score({"incentiveStrength": 0}, weights, 0) == 0
        # Unknown is not zero: neutral is the codebase's convention for unscored.
        assert ReportBuilder._weighted_score({"incentiveStrength": None}, weights, 0) == 50


# ── K: next steps ────────────────────────────────────────────────────────────

class TestK_VerifyBeforeSpending:
    def _builder(self, estimates):
        b = ReportBuilder.__new__(ReportBuilder)
        b._built_incentive_estimates = estimates
        b._production_format = "Short"
        return b

    def test_verification_leads_the_actions(self):
        summary = {"actionTimeline": [
            {"action": "Establish a New York qualified production entity"},
        ]}
        b = self._builder([
            {"territory": "New York", "program": "NY Film Tax Credit",
             "incentiveIsConfirmed": False},
        ])
        ReportBuilder._inject_eligibility_first_step(b, summary)

        first = summary["actionTimeline"][0]["action"].lower()
        assert "confirm" in first and "eligib" in first
        assert "entity" in summary["actionTimeline"][1]["action"].lower()

    def test_nothing_is_prepended_when_every_incentive_is_confirmed(self):
        summary = {"actionTimeline": [{"action": "Register with the film office"}]}
        b = self._builder([{"territory": "Spain", "incentiveIsConfirmed": True}])
        ReportBuilder._inject_eligibility_first_step(b, summary)
        assert len(summary["actionTimeline"]) == 1

    def test_it_is_not_added_twice(self):
        summary = {"actionTimeline": [
            {"action": "Confirm short-film eligibility with the commission"},
        ]}
        b = self._builder([{"territory": "New York", "incentiveIsConfirmed": False}])
        ReportBuilder._inject_eligibility_first_step(b, summary)
        assert len(summary["actionTimeline"]) == 1


# ── Other formats stay unaffected ────────────────────────────────────────────

class TestFeaturesAreUnaffected:
    @pytest.mark.parametrize("fmt", ["Feature Film", "TV Series", "Documentary"])
    def test_an_unrecorded_format_does_not_block_a_feature(self, fmt):
        assert status(prog(), fmt=fmt)["status"] == ELIGIBLE

    def test_but_a_hard_budget_failure_still_blocks_one(self):
        """Hard gates are not short-film specific. A budget below the minimum is a
        fact about the project whatever it is."""
        result = status(prog("Louisiana", 25, **HARD_BUDGET_FAIL), fmt="Feature Film")
        assert result["status"] == INELIGIBLE
