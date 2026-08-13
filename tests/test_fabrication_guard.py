"""FIX-06: a territory with no claimable incentive may not be quoted a figure.

The prompt now forbids it. A prompt rule is a request, and the failure mode is
the expensive kind — a producer reads "roughly 25% back" beside a territory that
will pay nothing and builds a finance plan on it. So the figures are removed
after generation, deterministically, from the same canonical report object every
surface renders.

The guard is deliberately narrow. It strips a sentence only when that sentence
names a blocked territory AND quotes a figure. It has no opinion about
FORMAT_UNVERIFIED, whose figures are legitimate and labelled illustrative
elsewhere; stripping those would be its own kind of lie.
"""
from __future__ import annotations

import pytest

from app.modules.reports.fabrication_guard import (
    FIGURE_RE,
    blocked_territories,
    scrub_report,
)


def report(**over):
    base = {
        "locationRankings": [
            {"name": "South Africa", "hasNoBankableIncentive": True,
             "incentiveAvailability": "The DTIC rebate is suspended.",
             "reasoning": [], "keyRisks": [], "keyAdvantages": []},
            {"name": "United Kingdom", "reasoning": [], "keyRisks": [], "keyAdvantages": []},
        ],
        "incentiveEstimates": [],
        "executiveSummary": {"keyInsights": "", "keyFlags": []},
    }
    base.update(over)
    return base


class TestWhichTerritoriesAreBlocked:
    def test_a_territory_with_no_bankable_incentive_is_blocked(self):
        assert "South Africa" in blocked_territories(report())

    def test_a_normal_territory_is_not(self):
        assert "United Kingdom" not in blocked_territories(report())

    def test_a_format_ineligible_estimate_is_blocked(self):
        r = report(incentiveEstimates=[
            {"territory": "California",
             "formatEligibility": {"gateState": "FORMAT_INELIGIBLE",
                                   "explanation": "Does not accept shorts."}},
        ])
        assert "California" in blocked_territories(r)

    def test_a_format_unverified_estimate_is_not_blocked(self):
        """Its figure is legitimate and labelled illustrative elsewhere."""
        r = report(incentiveEstimates=[
            {"territory": "New Mexico",
             "formatEligibility": {"gateState": "FORMAT_UNVERIFIED"}},
        ])
        assert "New Mexico" not in blocked_territories(r)

    def test_the_reason_comes_from_the_report_not_from_this_module(self):
        assert "suspended" in blocked_territories(report())["South Africa"]


class TestStripping:
    def test_a_percentage_claimed_for_a_blocked_territory_is_removed(self):
        r = report()
        r["executiveSummary"]["keyInsights"] = (
            "South Africa offers a 25% rebate on qualifying spend."
        )
        warnings = scrub_report(r)
        assert "25%" not in r["executiveSummary"]["keyInsights"]
        assert warnings

    def test_a_money_amount_is_removed(self):
        r = report()
        r["executiveSummary"]["keyInsights"] = (
            "South Africa would return roughly ZAR 250,000 to the production."
        )
        scrub_report(r)
        assert "250,000" not in r["executiveSummary"]["keyInsights"]

    def test_the_surrounding_prose_survives(self):
        """Sentence-level, not field-level. The crew sentence is true and useful."""
        r = report()
        r["executiveSummary"]["keyInsights"] = (
            "South Africa has a growing crew base in Cape Town. "
            "South Africa offers a 25% rebate. "
            "Local costs are competitive."
        )
        scrub_report(r)
        text = r["executiveSummary"]["keyInsights"]
        assert "growing crew base" in text
        assert "Local costs are competitive" in text
        assert "25%" not in text

    def test_a_figure_for_a_different_territory_is_untouched(self):
        r = report()
        r["executiveSummary"]["keyInsights"] = (
            "The United Kingdom returns 25.5% net on qualifying spend."
        )
        scrub_report(r)
        assert "25.5%" in r["executiveSummary"]["keyInsights"]

    def test_a_sentence_naming_a_blocked_territory_without_a_figure_survives(self):
        r = report()
        r["executiveSummary"]["keyInsights"] = (
            "South Africa remains attractive for locations and crew."
        )
        scrub_report(r)
        assert "South Africa remains attractive" in r["executiveSummary"]["keyInsights"]

    def test_ranking_narrative_is_scrubbed_too(self):
        r = report()
        r["locationRankings"][0]["keyAdvantages"] = [
            "South Africa pays 25% back on local spend.",
            "Cape Town Film Studios is the largest facility in sub-Saharan Africa.",
        ]
        scrub_report(r)
        advantages = r["locationRankings"][0]["keyAdvantages"]
        assert len(advantages) == 1
        assert "Cape Town Film Studios" in advantages[0]

    def test_every_removal_is_reported(self):
        """Silent repair would hide a prompt regression."""
        r = report()
        r["executiveSummary"]["keyInsights"] = "South Africa offers 25%."
        warnings = scrub_report(r)
        assert len(warnings) == 1
        assert "South Africa" in warnings[0]
        assert "fabrication" in warnings[0]

    def test_a_clean_report_is_left_alone(self):
        r = report()
        r["executiveSummary"]["keyInsights"] = "South Africa is a strong location choice."
        before = r["executiveSummary"]["keyInsights"]
        assert scrub_report(r) == []
        assert r["executiveSummary"]["keyInsights"] == before

    def test_a_report_with_nothing_blocked_short_circuits(self):
        r = report()
        r["locationRankings"][0].pop("hasNoBankableIncentive")
        r["executiveSummary"]["keyInsights"] = "South Africa offers 25%."
        assert scrub_report(r) == []


class TestFigureDetection:
    @pytest.mark.parametrize(
        "text", ["25%", "25.5 %", "£9,329", "USD 13,124", "$1M", "ZAR 250,000", "€60,000"],
    )
    def test_incentive_figures_are_detected(self, text):
        assert FIGURE_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "45 scenes", "4 weeks", "2026", "80 percent interior",
            "a crew of 30", "3 to 6 months",
        ],
    )
    def test_ordinary_prose_numbers_are_not(self, text):
        """A guard that eats "45 scenes" gets switched off, and then it protects
        nothing at all."""
        assert not FIGURE_RE.search(text)
