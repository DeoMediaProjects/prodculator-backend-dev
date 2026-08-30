"""The co-production section: reconciled, not ranked.

Two things are load-bearing. Partners must not be ordered best-first, because
they are not alternatives to one another and saying so states something false
about the structure. And the partner incentives must not be summed, because
cumulation ceilings and public-support intensity limits bite on the total and
none of that has been assessed.
"""
from __future__ import annotations

import html as _html
import re

import pytest

from app.modules.reports.coproduction_section import (
    build_coproduction_opportunities,
    build_coproduction_structure,
)
from app.modules.reports.pdf_service import PDFService


def _scenario(territory, spend, *, share=None, status="candidate"):
    return {
        "territory": territory,
        "scenario_spend": spend,
        "scenario_currency": "EUR",
        "participation_percent": share,
        "partner_status": status,
    }


def _scenarios(*entries):
    return {e["territory"]: e for e in entries}


FR = _scenario("France", 4_000_000, share=50, status="confirmed")
DE = _scenario("Germany", 3_000_000, share=30)
IE = _scenario("Ireland", 3_000_000, share=20)

ESTIMATES = [
    {"territory": "France", "program": "CNC Tax Rebate",
     "confirmedIncentive": "€1,200,000", "calculationStatus": "ESTIMATED",
     "calculationStatusLabel": "Calculated"},
    {"territory": "Germany", "program": "GMPF",
     "confirmedIncentive": None, "calculationStatus": "REQUIRES_COST_BREAKDOWN",
     "calculationStatusLabel": "Needs a cost breakdown"},
]


def _build(**overrides):
    args = {
        "mode": "coproduction",
        "scenarios": _scenarios(FR, DE, IE),
        "estimates": ESTIMATES,
        "budget": 10_000_000.0,
        "currency": "EUR",
    }
    args.update(overrides)
    return build_coproduction_structure(**args)


# ── when the section exists at all ───────────────────────────────────────────


def test_a_comparison_has_no_structure_to_report():
    """None rather than an empty structure: a section that renders with nothing
    in it invites the reader to wonder what went missing."""
    assert _build(mode="comparison") is None
    assert _build(mode="undecided") is None


def test_no_scenarios_means_no_section():
    assert _build(scenarios={}) is None


# ── the rule that separates this from every other territory section ──────────


class TestNotRanked:
    def test_the_object_says_it_is_not_a_ranking(self):
        assert _build()["partnersAreRanked"] is False

    def test_the_combined_incentive_is_withheld_with_its_reason(self):
        structure = _build()
        assert structure["combinedIncentiveWithheld"] is True
        assert "cumulation" in structure["combinedIncentiveReason"].lower()

    def test_an_unknown_allocation_sorts_last_rather_than_as_zero(self):
        """Ordering is by size so the majority partner reads first. An unknown
        allocation cannot be placed, which is not the same as being small."""
        structure = _build(scenarios=_scenarios(
            _scenario("Ireland", None), _scenario("France", 4_000_000),
        ))
        assert [p["territory"] for p in structure["partners"]] == ["France", "Ireland"]


# ── reconciliation ───────────────────────────────────────────────────────────


class TestReconciliation:
    def test_shares_that_add_up_reconcile(self):
        structure = _build()
        assert structure["reconciliationStatus"] == "reconciled"
        assert structure["reconciliationRemaining"] == 0.0

    def test_under_allocation_is_reported_as_ordinary(self):
        structure = _build(budget=12_000_000.0)
        assert structure["reconciliationStatus"] == "under_allocated"
        assert structure["reconciliationRemaining"] == pytest.approx(2_000_000.0)
        assert "assembled" in structure["reconciliationExplanation"]

    def test_over_allocation_is_reported_as_a_problem(self):
        """A structure cannot spend more than it is financed for, so this must not
        share its wording with under-allocation."""
        structure = _build(budget=8_000_000.0)
        assert structure["reconciliationStatus"] == "over_allocated"
        assert structure["reconciliationRemaining"] < 0
        assert "cannot spend more" in structure["reconciliationExplanation"]

    def test_a_missing_allocation_makes_the_total_unassessable(self):
        """Treating it as zero would report a shortfall that may not exist."""
        structure = _build(scenarios=_scenarios(
            FR, _scenario("Germany", None),
        ))
        assert structure["reconciliationStatus"] == "not_assessable"
        assert structure["reconciliationRemaining"] is None

    def test_unallocated_spend_counts_toward_the_budget(self):
        structure = _build(
            scenarios=_scenarios(FR, DE), unallocated_spend=3_000_000,
        )
        assert structure["reconciliationStatus"] == "reconciled"

    def test_unallocated_spend_is_called_out_as_earning_nothing(self):
        structure = _build(unallocated_spend=500_000)
        assert any("earning nothing" in n for n in structure["structureNotes"])


# ── the figures come from one place ──────────────────────────────────────────


class TestFiguresAreCopied:
    def test_a_partner_figure_matches_the_incentive_section(self):
        partners = {p["territory"]: p for p in _build()["partners"]}
        assert partners["France"]["incentive"] == "€1,200,000"
        assert partners["France"]["programme"] == "CNC Tax Rebate"

    def test_a_withheld_figure_stays_withheld_here(self):
        """Showing a blank where the incentive section shows a status would read
        as nil, which is a different claim entirely."""
        partners = {p["territory"]: p for p in _build()["partners"]}
        assert partners["Germany"]["incentive"] is None
        assert partners["Germany"]["calculationStatusLabel"] == "Needs a cost breakdown"

    def test_a_partner_with_no_estimate_is_still_listed(self):
        partners = {p["territory"]: p for p in _build()["partners"]}
        assert "Ireland" in partners
        assert partners["Ireland"]["programme"] is None


# ── structure notes ──────────────────────────────────────────────────────────


class TestStructureNotes:
    def test_candidate_partners_make_the_structure_provisional(self):
        notes = " ".join(_build()["structureNotes"])
        assert "candidates rather than confirmed" in notes

    def test_the_council_of_europe_minimum_is_stated_not_enforced(self):
        """The producer may be mid-way through assembling the structure, and
        refusing the report would not help them."""
        structure = _build(
            scenarios=_scenarios(FR, DE),
            route="Revised Council of Europe Convention",
        )
        assert structure is not None
        assert any("at least 3" in n for n in structure["structureNotes"])

    def test_three_partners_raise_no_council_of_europe_note(self):
        structure = _build(route="Revised Council of Europe Convention")
        assert not any("Council of Europe" in n for n in structure["structureNotes"])


# ── as a reader sees it ──────────────────────────────────────────────────────


def _visible_text(markup: str) -> str:
    out = re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I)
    out = re.sub(r'(?:src|href)="data:[^"]*"', " ", out)
    out = re.sub(r"<[^>]+>", " ", out)
    return _html.unescape(re.sub(r"\s+", " ", out))


class TestRendering:
    @pytest.fixture(scope="class")
    def text(self) -> str:
        markup = PDFService().render_report_html(
            {
                "incentiveEstimates": [],
                "coProductionStructure": _build(budget=12_000_000.0),
            },
            script_title="Co-Production Fixture",
            created_at="2026-08-26T00:00:00Z",
        )
        return _visible_text(markup)

    def test_the_section_states_that_it_does_not_rank(self, text: str):
        """Every other territory table in this report ranks, so a reader arrives
        expecting this one to as well."""
        assert "not ranked against one another" in text

    def test_every_partner_appears(self, text: str):
        for territory in ("France", "Germany", "Ireland"):
            assert territory in text

    def test_the_reconciliation_verdict_is_shown(self, text: str):
        assert "Under allocated" in text

    def test_the_withheld_sum_is_explained_rather_than_omitted(self, text: str):
        assert "Combined incentive not stated" in text

    def test_an_unsupplied_allocation_does_not_render_as_a_dash(self, text: str):
        markup = PDFService().render_report_html(
            {
                "incentiveEstimates": [],
                "coProductionStructure": _build(
                    scenarios=_scenarios(FR, _scenario("Germany", None)),
                ),
            },
            script_title="Co-Production Fixture",
            created_at="2026-08-26T00:00:00Z",
        )
        assert "Not supplied" in _visible_text(markup)


# ── co-production opportunities (undecided mode) ─────────────────────────────


def _opp_estimates():
    return [
        {"territory": "France", "program": "CNC Tax Rebate", "coProductionEligible": True},
        {"territory": "Germany", "program": "GMPF", "coProductionEligible": False},
        {"territory": "Ireland", "program": "Section 481", "coProductionEligible": True},
    ]


class TestCoProductionOpportunities:
    def test_only_populated_for_undecided(self):
        assert build_coproduction_opportunities(mode="comparison", estimates=_opp_estimates()) is None
        assert build_coproduction_opportunities(mode="coproduction", estimates=_opp_estimates()) is None

    def test_lists_only_eligible_territories(self):
        opportunities = build_coproduction_opportunities(mode="undecided", estimates=_opp_estimates())
        territories = {o["territory"] for o in opportunities}
        assert territories == {"France", "Ireland"}
        assert "Germany" not in territories

    def test_none_when_nothing_is_eligible(self):
        estimates = [{"territory": "Germany", "program": "GMPF", "coProductionEligible": False}]
        assert build_coproduction_opportunities(mode="undecided", estimates=estimates) is None

    def test_deduplicates_multiple_programmes_per_territory(self):
        estimates = [
            {"territory": "France", "program": "CNC Tax Rebate", "coProductionEligible": True},
            {"territory": "France", "program": "Another Programme", "coProductionEligible": True},
        ]
        opportunities = build_coproduction_opportunities(mode="undecided", estimates=estimates)
        assert len(opportunities) == 1
        assert opportunities[0]["territory"] == "France"

    def test_renders_in_the_pdf_kept_separate_from_the_structure_section(self):
        # The opportunities notice lives inside the Tax Incentive Analysis
        # section, which only renders once there is at least one estimate —
        # true of every real report, so the fixture needs one too.
        markup = PDFService().render_report_html(
            {
                "incentiveEstimates": [
                    {"territory": "France", "program": "CNC Tax Rebate", "rate": "30%"},
                ],
                "coProductionStructure": None,
                "coProductionOpportunities": build_coproduction_opportunities(
                    mode="undecided", estimates=_opp_estimates(),
                ),
            },
            script_title="Undecided Fixture",
            created_at="2026-08-26T00:00:00Z",
        )
        text = _visible_text(markup)
        assert "Co-production opportunities in this comparison" in text
        assert "France" in text
        assert "Ireland" in text
        # Not shown as a chosen structure — no partner-reconciliation language.
        assert "not ranked against one another" not in text
