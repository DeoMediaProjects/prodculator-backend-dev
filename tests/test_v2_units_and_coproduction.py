"""The unit boundary, the vocabulary bridge and production structure mode.

THE UNIT BOUNDARY IS THE DANGEROUS ONE
--------------------------------------
Prodculator stores rates as percentages: ``rate_gross`` is ``36.0`` and
``qualifying_spend_cap_pct`` is ``80.0``. The v2 reference implementation stores
them as fractions: ``base_rate`` is ``.36``, ``qs_percentage_cap`` is ``0.8``, and
its demo data carries ``.30``, ``.3975``, ``.36`` throughout.

Porting the reference formula against our columns would compute 8000 percent of
global core expenditure, or a rate 100 times too large. Neither raises on its own.
Both produce a plausible number in a document a financier reads, which is why the
conversion is guarded rather than trusted.

MODE IS STRUCTURAL
------------------
The same spend figure means an alternative in comparison mode and an allocation in
co-production mode. The tests below pin that the two cannot be mixed, because a
request carrying both readings leaves a report unable to say which applies.
"""
from __future__ import annotations

import pytest

from app.modules.incentives.v2_contracts import (
    COMPARISON_MODES,
    CUMULATION_STATUSES,
    PARTNER_STATUSES,
    RECONCILIATION_STATUSES,
    STRUCTURE_MODES,
    is_comparison_mode,
    reconcile_allocations,
)
from app.modules.incentives.v2_reference_adapter import (
    ALLOWED_CLAIM,
    LOSSY_ON_EXPORT,
    VocabularyError,
    allowed_claim,
    provenance_from_reference,
    provenance_to_reference,
    status_from_reference,
    status_to_reference,
)
from app.modules.incentives.v2_units import (
    UnitError,
    apply_cap_percent,
    apply_rate,
    fraction_to_percent,
    looks_like_a_fraction,
    percent_to_fraction,
)
from app.modules.reports.schemas import CreateReportRequest

_BASE = dict(
    script_title="A Production",
    genre=["Drama"],
    budget_amount=10_000_000.0,
    format="Feature Film",
    country="France",
)


def _request(**overrides):
    return CreateReportRequest(**{**_BASE, **overrides})


# ── the unit boundary ────────────────────────────────────────────────────────


class TestUnitConversion:
    @pytest.mark.parametrize("percent,fraction", [
        (36.0, 0.36), (80.0, 0.8), (53.0, 0.53), (39.75, 0.3975),
        (0.0, 0.0), (100.0, 1.0),
    ])
    def test_round_trip(self, percent, fraction):
        assert percent_to_fraction(percent) == pytest.approx(fraction)
        assert fraction_to_percent(fraction) == pytest.approx(percent)

    def test_absent_stays_absent(self):
        """An absent rate is not a zero rate."""
        assert percent_to_fraction(None) is None
        assert fraction_to_percent(None) is None

    def test_the_reference_convention_is_rejected_as_a_percentage(self):
        """0.8 is the reference's 80 percent cap. Read as a percentage it would
        silently become 0.8 percent, under-reporting by a hundred times. It is in
        range, so this cannot raise; the diagnostic exists instead."""
        assert looks_like_a_fraction(0.8) is True
        assert looks_like_a_fraction(80.0) is False

    @pytest.mark.parametrize("bad", [80.0, 36.0, 1.5, 100.0001])
    def test_a_percentage_passed_as_a_fraction_raises(self, bad):
        """The 8000 percent bug. Porting min(local, pct * global) with our column
        would compute this, and nothing downstream would notice."""
        with pytest.raises(UnitError, match="above 1.0"):
            fraction_to_percent(bad)

    @pytest.mark.parametrize("bad", [100.01, 150.0, 8000.0])
    def test_a_rate_above_one_hundred_percent_raises(self, bad):
        with pytest.raises(UnitError, match="above 100 percent"):
            percent_to_fraction(bad)

    def test_negatives_are_rejected_in_both_directions(self):
        with pytest.raises(UnitError, match="negative"):
            percent_to_fraction(-1)
        with pytest.raises(UnitError, match="negative"):
            fraction_to_percent(-0.1)

    def test_a_boolean_is_never_a_rate(self):
        with pytest.raises(UnitError, match="boolean"):
            percent_to_fraction(True)

    def test_a_percentage_string_is_accepted(self):
        assert percent_to_fraction("36%") == pytest.approx(0.36)
        assert percent_to_fraction("36.0") == pytest.approx(0.36)

    def test_nonsense_raises_rather_than_becoming_zero(self):
        with pytest.raises(UnitError, match="not a number"):
            percent_to_fraction("thirty six")


class TestApplyingRates:
    def test_a_rate_is_applied_from_the_stored_percentage(self):
        assert apply_rate(10_000_000, 36.0) == pytest.approx(3_600_000)

    def test_the_uk_iftc_case(self):
        """12,000,000 qualifying spend at the 53 percent gross rate."""
        assert apply_rate(12_000_000, 53.0) == pytest.approx(6_360_000)

    def test_applying_an_absent_rate_raises_rather_than_returning_zero(self):
        with pytest.raises(UnitError, match="absent"):
            apply_rate(1_000_000, None)

    def test_a_cap_of_eighty_percent_restricts(self):
        assert apply_cap_percent(20_000_000, 80.0) == pytest.approx(16_000_000)

    @pytest.mark.parametrize("no_restriction", [None, 100.0])
    def test_no_cap_and_a_hundred_percent_cap_agree(self, no_restriction):
        assert apply_cap_percent(20_000_000, no_restriction) == 20_000_000

    def test_a_fraction_cap_is_caught_not_silently_applied(self):
        """0.8 from the reference would restrict to 0.8 percent of the base."""
        assert apply_cap_percent(20_000_000, 0.8) == pytest.approx(160_000)
        assert looks_like_a_fraction(0.8), (
            "0.8 is in fraction range and must be flagged before it reaches a cap"
        )


# ── the vocabulary bridge ────────────────────────────────────────────────────


class TestVocabularyBridge:
    def test_confirmed_and_known_are_the_same_thing(self):
        assert provenance_from_reference("confirmed") == "known"
        assert provenance_to_reference("known") == "confirmed"

    @pytest.mark.parametrize("value", ["planning_assumption", "unknown"])
    def test_the_other_provenances_round_trip_unchanged(self, value):
        assert provenance_from_reference(value) == value
        assert provenance_to_reference(value) == value

    def test_reference_calculated_is_our_estimated(self):
        assert status_from_reference("calculated") == "ESTIMATED"
        assert status_to_reference("ESTIMATED") == "calculated"

    @pytest.mark.parametrize("ours", [
        "CONDITIONAL", "REQUIRES_COST_BREAKDOWN", "NOT_ELIGIBLE",
        "PROGRAMME_UNVERIFIED",
    ])
    def test_shared_statuses_round_trip(self, ours):
        assert status_from_reference(status_to_reference(ours)) == ours

    @pytest.mark.parametrize("ours", sorted(LOSSY_ON_EXPORT))
    def test_our_extra_statuses_export_to_the_closest_honest_equivalent(self, ours):
        """The reference has no blocked, suspended or no-programme member. All
        three mean no figure and excluded from ranking, so programme_unverified is
        the closest honest export, and the loss is one-way."""
        assert status_to_reference(ours) == "programme_unverified"
        assert status_from_reference("programme_unverified") == "PROGRAMME_UNVERIFIED"

    def test_the_lossy_set_is_exactly_the_statuses_the_reference_lacks(self):
        exported = {status_to_reference(s) for s in LOSSY_ON_EXPORT}
        assert exported == {"programme_unverified"}
        assert "ESTIMATED" not in LOSSY_ON_EXPORT

    @pytest.mark.parametrize("bad", ["approved", "CALCULATED_MAYBE", "", "secured"])
    def test_an_unknown_value_raises_rather_than_guessing(self, bad):
        with pytest.raises(VocabularyError):
            status_from_reference(bad)
        with pytest.raises(VocabularyError):
            status_to_reference(bad)

    def test_every_status_declares_a_permitted_narrative_claim(self):
        from app.modules.incentives.v2_contracts import CALCULATION_STATUSES

        assert set(ALLOWED_CLAIM) == set(CALCULATION_STATUSES)

    def test_only_estimated_permits_a_calculated_claim(self):
        assert allowed_claim("ESTIMATED") == "estimated_calculated_amount"
        assert allowed_claim("CONDITIONAL") == "potential_modelled_amount"
        for status in ("REQUIRES_COST_BREAKDOWN", "NOT_ELIGIBLE", "BLOCKED",
                       "SUSPENDED", "NO_PROGRAMME", "PROGRAMME_UNVERIFIED"):
            assert allowed_claim(status) == "no_project_amount"


# ── production structure mode ────────────────────────────────────────────────


class TestModeVocabulary:
    def test_the_three_modes(self):
        assert STRUCTURE_MODES == ("comparison", "coproduction", "undecided")

    def test_undecided_calculates_as_comparison(self):
        """It keeps comparison logic and surfaces co-production opportunities
        separately, so it is not a third calculation path."""
        assert COMPARISON_MODES == {"comparison", "undecided"}
        assert is_comparison_mode("undecided") is True
        assert is_comparison_mode("coproduction") is False

    def test_an_absent_mode_means_comparison(self):
        """Existing behaviour, so old requests keep working."""
        assert is_comparison_mode(None) is True

    def test_partner_status_never_implies_approval(self):
        assert PARTNER_STATUSES == ("candidate", "confirmed")
        assert "approved" not in PARTNER_STATUSES
        assert "certified" not in PARTNER_STATUSES

    def test_cumulation_defaults_to_the_unchecked_end(self):
        assert CUMULATION_STATUSES[0] == "not_checked"
        assert "passed" in CUMULATION_STATUSES


class TestReconciliation:
    def test_allocations_plus_unallocated_reconcile(self):
        assert reconcile_allocations(10e6, [4e6, 3e6, 2e6], 1e6) == ("reconciled", 0.0)

    def test_under_allocation_reports_the_remainder(self):
        status, remaining = reconcile_allocations(10e6, [4e6])
        assert status == "under_allocated"
        assert remaining == pytest.approx(6e6)

    def test_over_allocation_is_reported_not_clamped(self):
        """It may be a currency difference rather than an error, and clamping
        would silently discard the producer's figure."""
        status, remaining = reconcile_allocations(10e6, [8e6, 5e6])
        assert status == "over_allocated"
        assert remaining == pytest.approx(-3e6)

    def test_an_unknown_allocation_makes_the_total_unassessable(self):
        """Treating an unknown as zero would report a false shortfall."""
        assert reconcile_allocations(10e6, [4e6, None]) == ("not_assessable", None)

    def test_no_budget_is_unassessable(self):
        assert reconcile_allocations(None, [1e6]) == ("not_assessable", None)

    def test_rounding_tolerance(self):
        assert reconcile_allocations(10e6, [10e6 - 0.005])[0] == "reconciled"

    def test_every_status_is_declared(self):
        produced = {
            reconcile_allocations(10e6, [4e6, 3e6, 2e6], 1e6)[0],
            reconcile_allocations(10e6, [4e6])[0],
            reconcile_allocations(10e6, [20e6])[0],
            reconcile_allocations(None, [])[0],
        }
        assert produced == set(RECONCILIATION_STATUSES)


class TestModeOnTheRequest:
    def test_comparison_is_the_default(self):
        assert _request().production_structure_mode == "comparison"

    def test_a_coproduction_structure_reconciles(self):
        request = _request(
            production_structure_mode="coproduction",
            unallocated_spend=1e6,
            territory_scenarios=[
                {"territory": "France", "scenario_spend": 4e6,
                 "participation_percent": 40, "partner_status": "confirmed"},
                {"territory": "Germany", "scenario_spend": 3e6,
                 "participation_percent": 30, "partner_status": "candidate"},
                {"territory": "Ireland", "scenario_spend": 2e6,
                 "participation_percent": 20, "partner_status": "candidate"},
            ],
        )
        assert request.allocation_reconciliation == ("reconciled", 0.0)

    @pytest.mark.parametrize("mode", ["comparison", "undecided"])
    def test_a_participation_share_is_rejected_outside_coproduction(self, mode):
        with pytest.raises(ValueError, match="participation_percent and partner_status"):
            _request(production_structure_mode=mode, territory_scenarios=[
                {"territory": "France", "participation_percent": 40},
            ])

    @pytest.mark.parametrize("field,value", [
        ("unallocated_spend", 1.0),
        ("co_production_route", "Bilateral treaty"),
        ("supranational_support_interest", "show_opportunity"),
    ])
    def test_structure_fields_are_rejected_in_comparison_mode(self, field, value):
        with pytest.raises(ValueError, match="co-production structure"):
            _request(**{field: value})

    def test_comparison_scenarios_are_not_reconciled(self):
        """They are alternatives, so a shortfall against the budget is meaningless."""
        request = _request(territory_scenarios=[
            {"territory": "France", "scenario_spend": 1e6},
        ])
        assert request.allocation_reconciliation == ("not_assessable", None)

    def test_a_participation_share_outside_zero_to_one_hundred_is_rejected(self):
        with pytest.raises(ValueError, match="between 0 and 100"):
            _request(production_structure_mode="coproduction", territory_scenarios=[
                {"territory": "France", "participation_percent": 140},
            ])

    def test_negative_unallocated_spend_is_rejected(self):
        with pytest.raises(ValueError, match="unallocated_spend cannot be negative"):
            _request(production_structure_mode="coproduction", unallocated_spend=-1.0)

    def test_shares_need_not_total_one_hundred(self):
        """The demo displays the total without forcing it, because a structure
        mid-negotiation legitimately does not add up yet."""
        request = _request(production_structure_mode="coproduction",
                           territory_scenarios=[
                               {"territory": "France", "participation_percent": 40},
                               {"territory": "Germany", "participation_percent": 30},
                           ])
        total = sum(s.participation_percent for s in request.territory_scenarios)
        assert total == 70
