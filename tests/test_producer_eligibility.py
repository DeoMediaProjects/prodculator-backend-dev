"""The country constraint: who is actually allowed to claim.

``project_incentive`` listed producer nationality first among the four dimensions
it exists to combine, and combined the other three. So Canada's CPTC — restricted
to Canadian-controlled corporations, no SPV route on record — came back for a
London production as:

    status           eligible
    canAffectRanking True
    canBeRecommended True
    reasons          []

The rate then fed the ranking that recommended Canada. The one place nationality
WAS compared, ``builder._apply_eligibility``, could not fire either: it read a
``producer_country`` request field no client sends, and compared it to the
requirement as raw text, so the intake label "United Kingdom" would never have
matched the stored ISO code "GB" even once the field was populated.
"""
from __future__ import annotations

import pytest

from app.modules.reports.producer_eligibility import (
    EXCLUDED,
    QUALIFIES,
    ROUTED,
    UNKNOWN,
    evaluate_producer_eligibility,
    legacy_status,
)
from app.modules.reports.project_incentive import resolve_project_incentive


def programme(**extra) -> dict:
    return {
        "territory": "Canada",
        "program": "CPTC",
        "rate_gross": 25,
        "status": "active",
        "is_supplementary": False,
        **extra,
    }


CPTC = programme(
    nationality_requirements='["CA"]',
    spv_eligible=False,
    co_production_eligible=True,
)
OPEN = programme(territory="Hungary", program="Hungary Rebate")
BASE = {"budget_gbp": 5_000_000, "format": "Feature Film"}


class TestTheGateItself:
    def test_no_stated_requirement_qualifies(self):
        """The overwhelming majority of rows. Nothing to fail is a real answer,
        not an untested one."""
        r = evaluate_producer_eligibility(OPEN, {**BASE, "producer_country": "United Kingdom"})
        assert r["verdict"] == QUALIFIES

    def test_a_matching_producer_qualifies(self):
        r = evaluate_producer_eligibility(CPTC, {**BASE, "producer_country": "Canada"})
        assert r["verdict"] == QUALIFIES

    def test_a_label_is_compared_against_the_stored_iso_code(self):
        """Intake sends "United Kingdom"; the row stores "GB". The old comparison
        was raw text, so this pairing could never have matched."""
        r = evaluate_producer_eligibility(
            programme(nationality_requirements='["GB"]', spv_eligible=False),
            {**BASE, "producer_country": "United Kingdom"},
        )
        assert r["verdict"] == QUALIFIES

    def test_a_sub_territory_producer_resolves_to_its_country(self):
        """A company registered in California is a US company to every
        nationality requirement written."""
        r = evaluate_producer_eligibility(
            programme(nationality_requirements='["US"]', spv_eligible=False),
            {**BASE, "producer_country": "California"},
        )
        assert r["verdict"] == QUALIFIES

    def test_an_unmatched_producer_with_no_route_is_excluded(self):
        r = evaluate_producer_eligibility(
            programme(nationality_requirements='["CA"]', spv_eligible=False,
                      co_production_eligible=False),
            {**BASE, "producer_country": "United Kingdom"},
        )
        assert r["verdict"] == EXCLUDED

    def test_an_spv_route_is_a_condition_not_an_exclusion(self):
        r = evaluate_producer_eligibility(
            programme(nationality_requirements='["CA"]', spv_eligible=True),
            {**BASE, "producer_country": "United Kingdom"},
        )
        assert r["verdict"] == ROUTED
        assert legacy_status(r) == "requires_spv"

    def test_a_treaty_route_is_a_condition_while_co_production_is_open(self):
        r = evaluate_producer_eligibility(
            CPTC, {**BASE, "producer_country": "United Kingdom", "co_production_intent": "yes"},
        )
        assert r["verdict"] == ROUTED
        assert legacy_status(r) == "requires_co_production"

    def test_ruling_out_co_production_closes_the_route(self):
        """A producer who said no to treaties has said no to the only way in."""
        r = evaluate_producer_eligibility(
            CPTC, {**BASE, "producer_country": "United Kingdom", "co_production_intent": "no"},
        )
        assert r["verdict"] == EXCLUDED
        assert "ruled out at intake" in r["explanation"]

    def test_undecided_leaves_the_treaty_route_open(self):
        r = evaluate_producer_eligibility(
            CPTC,
            {**BASE, "producer_country": "United Kingdom", "co_production_intent": "undecided"},
        )
        assert r["verdict"] == ROUTED

    def test_an_untestable_requirement_is_unknown_not_qualified(self):
        """The whole point. A gate that cannot be evaluated must not pass."""
        r = evaluate_producer_eligibility(CPTC, BASE)
        assert r["verdict"] == UNKNOWN
        assert r["qualifies"] is False


class TestEuMembership:
    """Malta's requirement is ``["MT","EU"]`` — a membership test, not a country
    match. Read as country codes alone, a rule most of the continent satisfies
    becomes one only Malta does."""

    MALTA = programme(
        territory="Malta", program="Malta Cash Rebate",
        nationality_requirements='["MT","EU"]',
        spv_eligible=False, co_production_eligible=False,
    )

    @pytest.mark.parametrize("home", ["France", "Germany", "Ireland", "Malta"])
    def test_an_eu_producer_satisfies_it(self, home):
        r = evaluate_producer_eligibility(self.MALTA, {**BASE, "producer_country": home})
        assert r["verdict"] == QUALIFIES, home

    @pytest.mark.parametrize("home", ["United Kingdom", "Canada", "Australia"])
    def test_a_non_eu_producer_does_not(self, home):
        r = evaluate_producer_eligibility(self.MALTA, {**BASE, "producer_country": home})
        assert r["verdict"] == EXCLUDED, home


class TestItReachesTheCombinedStatus:
    """A gate nothing consults is decoration. These are the assertions that would
    have caught the original defect."""

    def test_an_excluded_producer_cannot_be_recommended(self):
        r = resolve_project_incentive(
            programme(nationality_requirements='["CA"]', spv_eligible=False,
                      co_production_eligible=False),
            {**BASE, "producer_country": "United Kingdom"},
        )
        assert r["status"] == "ineligible"
        assert r["canBeRecommended"] is False
        assert r["canAffectRanking"] is False
        assert r["canAffectNetCost"] is False

    def test_the_exclusion_is_explained_not_just_asserted(self):
        r = resolve_project_incentive(
            programme(nationality_requirements='["CA"]', spv_eligible=False,
                      co_production_eligible=False),
            {**BASE, "producer_country": "United Kingdom"},
        )
        assert any("CA" in reason and "GB" in reason for reason in r["reasons"])

    def test_a_route_makes_it_conditional_rather_than_confirmed(self):
        r = resolve_project_incentive(
            CPTC, {**BASE, "producer_country": "United Kingdom", "co_production_intent": "yes"},
        )
        assert r["status"] == "conditional"
        assert r["canAffectRanking"] is False
        # Still worth showing: the money exists, it just is not banked yet.
        assert r["showPotentialAmount"] is True

    def test_an_unknown_jurisdiction_is_unverified(self):
        r = resolve_project_incentive(CPTC, BASE)
        assert r["status"] == "unverified"
        assert r["canAffectRanking"] is False

    def test_a_qualifying_producer_is_unaffected(self):
        r = resolve_project_incentive(CPTC, {**BASE, "producer_country": "Canada"})
        assert r["status"] == "eligible"
        assert r["canBeRecommended"] is True

    def test_a_programme_with_no_requirement_is_unaffected(self):
        """The blast radius has to stay where the data is. Most rows state no
        nationality requirement and must behave exactly as before."""
        r = resolve_project_incentive(OPEN, {**BASE, "producer_country": "United Kingdom"})
        assert r["status"] == "eligible"
        assert r["canBeRecommended"] is True

    def test_the_dimension_is_reported_alongside_the_others(self):
        r = resolve_project_incentive(CPTC, {**BASE, "producer_country": "United Kingdom"})
        assert r["producerStatus"] == ROUTED
        assert r["requiredNationalities"] == ["CA"]
