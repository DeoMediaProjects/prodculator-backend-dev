"""A territory is more than its rebate.

A producer committed to filming in South Africa, selected it at intake, and got a
25-page report about the United Kingdom, New Mexico and California. South Africa
appeared once, in a flag, described as a territory with "no active incentive
programme on record".

Two separate faults, both of which had to be fixed for the territory to appear:

1. The dataset loader dropped every programme row whose status was not active, so
   a suspended programme became indistinguishable from no programme at all. The
   data itself is explicit that these differ — Nigeria's profile row reads "No
   incentive programme exists at all ... Distinct from South Africa which IS a
   programme that is suspended" — and the intake picker distinguishes them too,
   with an amber outline against a dashed one. Only the report collapsed them.

2. Selection and ranking both gated on having an active incentive row, so the
   incentive table decided whether a territory existed at all. South Africa has a
   curated profile (crew depth 60, infrastructure 55), weather data and a currency
   score, all of which the report discarded — while intake had promised the
   producer the territory stays selectable "for location, crew or currency
   reasons".
"""
from __future__ import annotations

from app.modules.reports.builder import ReportBuilder
from app.modules.reports.helpers import index_incentives_by_territory


def row(territory, rate=25, status="active", **extra):
    return {
        "territory": territory,
        "program": f"{territory} programme",
        "program_name": f"{territory} programme",
        "rate_gross": rate,
        "status": status,
        "is_supplementary": False,
        "parent_territory": None,
        **extra,
    }


SA_SUSPENDED = row(
    "South Africa", 25, status="SUSPENDED",
    program_name="South Africa Foreign Film & TV Production Incentive",
)
SA_PROFILE = {
    "territory": "South Africa",
    "crew_depth_tier": "growing", "crew_depth_score": 60,
    "infrastructure_tier": "growing", "infrastructure_score": 55,
}


def builder(user_territories, active=(), inactive=(), profiles=None, must_film_in=None):
    b = ReportBuilder.__new__(ReportBuilder)
    b.datasets = {
        "_user_territories": list(user_territories),
        "_must_film_in": must_film_in,
        "_shoot_months": [],
        "_shoot_weeks": 4,
    }
    b._territory_incentives = index_incentives_by_territory(list(active))
    b._territory_inactive_incentives = index_incentives_by_territory(list(inactive))
    b._territory_financials = {}
    b._territory_profiles = profiles or {}
    b._project_facts = {"budget_gbp": 45_730}
    b._production_format = "Short"
    b._production_priority = "full"
    b._currency_scores = None
    b.is_preview = False
    return b


class TestTheCommittedTerritoryReachesTheReport:
    def test_it_is_selected_for_analysis(self):
        b = builder(
            ["South Africa", "United Kingdom"],
            active=[row("United Kingdom", 34)],
            inactive=[SA_SUSPENDED],
            profiles={"South Africa": SA_PROFILE},
            must_film_in="South Africa",
        )
        assert "South Africa" in ReportBuilder._select_territories(b)

    def test_it_is_not_filed_as_unanalysable(self):
        b = builder(
            ["South Africa"], inactive=[SA_SUSPENDED],
            profiles={"South Africa": SA_PROFILE},
        )
        ReportBuilder._select_territories(b)
        assert b._unanalysed_territories == []

    def test_it_gets_a_ranking_row_with_its_real_profile_scores(self):
        """The second gate. Even once selected, `_build_location_rankings` skipped
        any territory with no incentive rows, so it still never appeared."""
        b = builder(
            ["South Africa", "United Kingdom"],
            active=[row("United Kingdom", 34)],
            inactive=[SA_SUSPENDED],
            profiles={"South Africa": SA_PROFILE},
        )
        territories = ReportBuilder._select_territories(b)
        rankings = ReportBuilder._build_location_rankings(b, territories)
        sa = next(r for r in rankings if r["name"] == "South Africa")
        assert sa["crewDepth"] == 60
        assert sa["infrastructure"] == 55

    def test_its_incentive_dimension_scores_zero_not_neutral(self):
        """A neutral 50 would quietly credit the territory with half a rebate it
        does not have."""
        b = builder(
            ["South Africa"], inactive=[SA_SUSPENDED],
            profiles={"South Africa": SA_PROFILE},
        )
        rankings = ReportBuilder._build_location_rankings(
            b, ReportBuilder._select_territories(b),
        )
        sa = next(r for r in rankings if r["name"] == "South Africa")
        assert sa["incentiveStrength"] == 0
        assert sa["rebatePercent"] == "N/A"

    def test_the_row_says_why_it_carries_no_rebate(self):
        """Shown beside territories quoting figures, with nothing stating why this
        one does not, an empty row reads as missing data rather than the answer."""
        b = builder(
            ["South Africa"], inactive=[SA_SUSPENDED],
            profiles={"South Africa": SA_PROFILE},
        )
        rankings = ReportBuilder._build_location_rankings(
            b, ReportBuilder._select_territories(b),
        )
        sa = next(r for r in rankings if r["name"] == "South Africa")
        assert sa["hasNoBankableIncentive"] is True
        assert "suspended" in sa["incentiveAvailability"]
        assert any("suspended" in risk for risk in sa["keyRisks"])


class TestSuspendedIsNotAbsent:
    def test_a_suspended_programme_is_described_as_suspended(self):
        b = builder(["South Africa"], inactive=[SA_SUSPENDED])
        reason = ReportBuilder._no_programme_reason(b, "South Africa")
        assert "suspended" in reason
        assert "not the absence of one" in reason
        assert "reinstated" in reason

    def test_the_programme_is_named(self):
        b = builder(["South Africa"], inactive=[SA_SUSPENDED])
        reason = ReportBuilder._no_programme_reason(b, "South Africa")
        assert "South Africa Foreign Film & TV Production Incentive" in reason

    def test_no_programme_at_all_is_described_as_structural(self):
        """Nigeria. Its own record insists on the distinction: not a slow or
        developing timeline, there is no programme to time."""
        b = builder(["Nigeria"])
        reason = ReportBuilder._no_programme_reason(b, "Nigeria")
        assert "at all" in reason
        assert "structural fact rather than a delay" in reason
        assert "suspended" not in reason

    def test_the_two_reasons_are_not_the_same_sentence(self):
        b = builder(["South Africa"], inactive=[SA_SUSPENDED])
        assert (
            ReportBuilder._no_programme_reason(b, "South Africa")
            != ReportBuilder._no_programme_reason(b, "Nigeria")
        )


class TestNothingBankableLeaksIn:
    def test_a_suspended_rate_is_never_ranked(self):
        """The whole reason inactive rows are indexed separately. A suspended 25%
        must not compete with an active one on rate."""
        b = builder(
            ["South Africa", "United Kingdom"],
            active=[row("United Kingdom", 34)],
            inactive=[SA_SUSPENDED],
            profiles={"South Africa": SA_PROFILE},
        )
        rankings = ReportBuilder._build_location_rankings(
            b, ReportBuilder._select_territories(b),
        )
        sa = next(r for r in rankings if r["name"] == "South Africa")
        assert sa["rebatePercent"] == "N/A"
        assert sa["incentiveStrength"] == 0

    def test_a_territory_we_know_nothing_about_is_still_reported_as_unanalysed(self):
        """The rule is "we hold something real about it", not "include everything".
        A bare label with no profile and no programme has nothing to say."""
        b = builder(["Atlantis"])
        assert ReportBuilder._select_territories(b) == []
        assert [u["territory"] for u in b._unanalysed_territories] == ["Atlantis"]

    def test_an_active_territory_is_unaffected(self):
        b = builder(["United Kingdom"], active=[row("United Kingdom", 34)])
        rankings = ReportBuilder._build_location_rankings(
            b, ReportBuilder._select_territories(b),
        )
        uk = next(r for r in rankings if r["name"] == "United Kingdom")
        assert uk.get("hasNoBankableIncentive") is None
        assert uk["rebatePercent"] != "N/A"
        # This fixture is a SHORT against a programme with no recorded format
        # eligibility, so the dimension is correctly not scored — None, meaning
        # neutral in the weighted total. What matters for this test is that it is not
        # 0: zero is reserved for a researched "no rebate here", which is exactly the
        # treatment an active territory must not receive.
        assert uk["incentiveStrength"] != 0

    def test_a_feature_against_an_active_territory_still_scores(self):
        """The not-scored rule is scoped to formats whose eligibility genuinely
        diverges from what these programmes are written for. A feature is not held
        back by the absence of a record stating features are accepted."""
        b = builder(["United Kingdom"], active=[row("United Kingdom", 34)])
        b._production_format = "Feature Film"
        b._project_facts = {"budget_gbp": 45_730, "format": "Feature Film"}
        rankings = ReportBuilder._build_location_rankings(
            b, ReportBuilder._select_territories(b),
        )
        uk = next(r for r in rankings if r["name"] == "United Kingdom")
        assert uk["incentiveStrength"] > 0
