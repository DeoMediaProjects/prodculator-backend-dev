"""A rebate calculation is arithmetic; eligibility is a fact about the programme.

For a short film the two were conflated: an unverified programme's figure was
subtracted from the budget and presented as the net production cost, which makes an
unconfirmed incentive look financially guaranteed.

The split is made once, in _pre_compute_territory_financials, and the CONFIRMED value
is what the existing net_rebate/net_budget fields carry. That matters more than it
looks: every chart, budget scenario, waterfall and total already reads those fields,
so making the safe value the default fixes every consumer at once rather than leaving
one of them summing an illustrative number.
"""
from __future__ import annotations

import pytest

from app.modules.reports.service import ReportService

BUDGET_GBP = 400_000.0


def programme(territory, rate=25, **extra):
    return {
        "territory": territory,
        "program": f"{territory} programme",
        "program_name": f"{territory} programme",
        "rate_gross": rate,
        "status": "active",
        "is_supplementary": False,
        "currency": "GBP",
        "parent_territory": None,
        **extra,
    }


def financials(production_format, rows, runtime=None):
    service = ReportService.__new__(ReportService)
    datasets = {
        "_budget_gbp": {"converted": BUDGET_GBP},
        "_budget_amount": 500_000,
        "_budget_currency": "USD",
        "_production_format": production_format,
        "_runtime_minutes": runtime,
        "_fx_rates_from_budget": {},
        "incentives": rows,
    }
    ReportService._pre_compute_territory_financials(service, datasets)
    return datasets.get("_territory_financials", {})


def money(text):
    return float(str(text).replace("$", "").replace("£", "").replace(",", ""))


VERIFIED_ELIGIBLE = {"format_eligibility_status": "verified",
                     "applicable_formats": ["feature", "short"]}
VERIFIED_INELIGIBLE = {"format_eligibility_status": "verified",
                       "applicable_formats": ["feature"]}
CONDITIONAL = {"format_eligibility_status": "conditional",
               "format_conditions": "Short animation allowed if runtime is at least 5 minutes."}


class TestRule1VerifiedEligible:
    def test_the_figure_is_confirmed_and_reduces_the_net_cost(self):
        f = financials("Short", [programme("France", **VERIFIED_ELIGIBLE)])["France"]
        assert f["incentive_is_confirmed"] is True
        assert f["incentive_eligibility_status"] == "eligible"
        assert money(f["net_rebate"]) > 0
        assert money(f["net_budget"]) == money(f["total_budget"]) - money(f["net_rebate"])


class TestRule2VerifiedIneligible:
    def test_no_rebate_is_presented_and_the_net_cost_is_the_full_budget(self):
        f = financials("Short", [programme("Morocco", **VERIFIED_INELIGIBLE)])["Morocco"]
        assert f["incentive_is_confirmed"] is False
        assert f["incentive_eligibility_status"] == "ineligible"
        assert money(f["net_rebate"]) == 0
        assert money(f["net_budget"]) == money(f["total_budget"])


class TestRule3Unknown:
    @pytest.mark.parametrize("row_extra", [
        {},                                                  # applicable_formats NULL
        {"format_eligibility_status": "unknown"},
        {"applicable_formats": None, "format_eligibility_status": None},
    ])
    def test_an_unknown_programme_does_not_reduce_the_net_cost(self, row_extra):
        f = financials("Short", [programme("South Africa", **row_extra)])["South Africa"]
        assert f["incentive_is_confirmed"] is False
        assert money(f["net_rebate"]) == 0
        assert money(f["net_budget"]) == money(f["total_budget"])

    def test_the_illustrative_figure_is_kept_but_kept_separate(self):
        """The spec's worked example: 500,000 stays 500,000, and the 125,000 is
        reported as potential rather than deleted or subtracted."""
        f = financials("Short", [programme("South Africa")])["South Africa"]
        assert money(f["potential_net_rebate"]) > 0
        assert money(f["net_rebate"]) == 0
        # The two must never be the same field, or a consumer will sum the wrong one.
        assert f["potential_net_rebate"] != f["net_rebate"]


class TestRule5Conditional:
    def test_a_condition_the_project_cannot_settle_is_not_confirmed(self):
        f = financials("Short", [programme("Spain", **CONDITIONAL)])["Spain"]
        assert f["incentive_is_confirmed"] is False
        # The project-level taxonomy, not the format-level verdict: the status a
        # section reads is now the combined one, and an unresolved condition is
        # "conditional" there.
        assert f["incentive_eligibility_status"] == "conditional"
        assert money(f["net_rebate"]) == 0

    def test_a_condition_the_project_satisfies_is_confirmed(self):
        """Short animation allowed if runtime >= 5; the project runs 12 minutes."""
        f = financials("Short", [programme("Spain", **CONDITIONAL)], runtime=12)["Spain"]
        assert f["incentive_eligibility_status"] == "eligible"
        assert f["incentive_is_confirmed"] is True
        assert money(f["net_rebate"]) > 0

    def test_a_condition_the_project_fails_is_not_confirmed(self):
        f = financials("Short", [programme("Spain", **CONDITIONAL)], runtime=3)["Spain"]
        assert f["incentive_is_confirmed"] is False
        assert money(f["net_rebate"]) == 0


class TestOtherFormatsAreUnaffected:
    @pytest.mark.parametrize("fmt", ["Feature Film", "TV Series", "Documentary"])
    def test_a_non_short_format_still_confirms_normally(self, fmt):
        """The rule is not "unverified programmes pay nothing"; it is scoped to the
        format whose eligibility genuinely diverges from what these programmes are
        written for. A feature must not be penalised by the absence of a record
        saying features are accepted."""
        f = financials(fmt, [programme("France")])["France"]
        assert f["incentive_is_confirmed"] is True
        assert money(f["net_rebate"]) > 0
        assert money(f["net_budget"]) < money(f["total_budget"])


class TestNothingLeaksIntoConfirmedTotals:
    def test_the_default_fields_every_consumer_reads_carry_the_confirmed_value(self):
        """net_rebate and net_budget are read by the waterfall charts, the budget
        scenarios and the executive summary. If the confirmed value were exposed
        under a NEW name instead, each of those would have to be found and changed,
        and the one that was missed would still show an illustrative number as real."""
        f = financials("Short", [programme("South Africa")])["South Africa"]
        assert money(f["net_rebate"]) == 0
        assert money(f["net_budget_value"]) == money(f["total_budget_value"])
        assert f["net_rebate_value"] == 0

    def test_an_unconfirmed_territory_never_shows_a_saving(self):
        rows = [programme("A"), programme("B", **VERIFIED_ELIGIBLE)]
        result = financials("Short", rows)
        assert money(result["A"]["net_budget"]) == money(result["A"]["total_budget"])
        assert money(result["B"]["net_budget"]) < money(result["B"]["total_budget"])

    def test_a_mixed_report_confirms_only_the_verified_territory(self):
        """The best-territory rule: a verified 20% must be the confirmed one even
        when an unverified programme computes a larger illustrative figure."""
        rows = [
            programme("Unverified", rate=40),
            programme("Verified", rate=20, **VERIFIED_ELIGIBLE),
        ]
        result = financials("Short", rows)
        assert money(result["Unverified"]["net_rebate"]) == 0
        assert money(result["Verified"]["net_rebate"]) > 0
        # The larger number still exists, but only as an illustration.
        assert money(result["Unverified"]["potential_net_rebate"]) > money(
            result["Verified"]["net_rebate"]
        )
