"""The remaining short-film contradictions, each pinned.

The confirmed-vs-potential architecture was already in place and working. What was
left were places where a section still described the OLD chart, the OLD label, or a
deadline belonging to a different format — and one case where a hard-ineligible
programme rendered an empty "Potential incentive" card, because the card was gated on
the status rather than on there being an amount to show.
"""
from __future__ import annotations

import re

import pytest

from app.modules.reports.builder import ReportBuilder
from app.modules.reports.pdf_service import PDFService


def visible(html: str) -> str:
    """Rendered text only. The embedded logo is base64 and matches almost anything."""
    stripped = re.sub(r'(?:src|href)="data:[^"]*"', " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped))


def render(report: dict) -> tuple[str, str]:
    html = PDFService().render_report_html(
        report, script_title="SHORT TEST", created_at="2026-08-11T00:00:00Z",
    )
    return html, visible(html)


# ── Issue 5: the empty potential card ────────────────────────────────────────

class TestHardFailureShowsNoPotentialCard:
    def test_a_programme_with_no_potential_amount_renders_no_potential_card(self):
        _html, text = render({"incentiveEstimates": [{
            "territory": "Georgia", "program": "GA Credit", "rate": "30%",
            "incentiveIsConfirmed": False, "potentialIncentive": None,
            "incentiveEligibilityLabel": "Not available to this project",
        }]})
        assert "Potential incentive" not in text
        assert "Not available to this project" in text

    def test_an_unverified_programme_with_an_amount_still_shows_one(self):
        """The working behaviour that must not regress."""
        _html, text = render({"incentiveEstimates": [{
            "territory": "New York", "program": "NY Credit", "rate": "30%",
            "incentiveIsConfirmed": False, "potentialIncentive": "GBP 13,236",
        }]})
        assert "Potential incentive" in text
        assert "13,236" in text
        assert "Illustrative only" in text

    def test_a_confirmed_programme_keeps_its_estimate(self):
        _html, text = render({"incentiveEstimates": [{
            "territory": "Spain", "program": "ES Credit", "rate": "25%",
            "incentiveIsConfirmed": True, "estimatedRebate": "GBP 9,000",
        }]})
        assert "9,000" in text
        assert "Potential incentive" not in text


# ── Issue 1 / 15: Territory Analysis labels ──────────────────────────────────

class TestTerritoryAnalysisDistinguishesRateFromEntitlement:
    def _dive(self, **over):
        return {"territoryDeepDives": [{
            "name": "New York", "country": "New York", "score": 60,
            "headlineRate": "30%", "rebate": "30%", "estimatedRebate": "GBP 0",
            "infrastructure": "Established", "paymentSpeed": "4 to 9 months",
            "culturalTestLikelihood": "N/A", "adminComplexity": "Medium",
            **over,
        }]}

    def test_an_unverified_territory_labels_the_rate_as_the_programmes(self):
        _html, text = render(self._dive(
            incentiveIsConfirmed=False,
            incentiveEligibilityLabel="Eligibility unverified",
            confirmedIncentive=None,
            potentialIncentive="GBP 13,236",
        ))
        assert "Programme rate" in text
        assert "unverified" in text.lower()
        assert "Confirmed incentive" in text
        assert "13,236" in text
        # The ambiguous pairing that started this: a headline rate labelled as
        # though it were this project's rebate.
        assert "Est. rebate" not in text

    def test_a_confirmed_territory_reads_normally(self):
        _html, text = render(self._dive(
            incentiveIsConfirmed=True, estimatedRebate="GBP 9,000",
        ))
        assert "Est. incentive" in text
        assert "9,000" in text

    def test_a_hard_ineligible_territory_shows_no_potential_row(self):
        _html, text = render(self._dive(
            incentiveIsConfirmed=False,
            incentiveEligibilityLabel="Not available to this project",
            potentialIncentive=None,
        ))
        assert "Potential incentive" not in text
        assert "Not available to this project" in text


# ── Issue 7: the chart key describes the chart that is there ─────────────────

class TestFinancialKeyFollowsTheStatus:
    def _scenario(self, **over):
        return {"financialAnalysis": {"budgetScenarios": [{
            "territory": "New York", "currencySymbol": "GBP ",
            "totalBudgetValue": 51_905, "netBudgetValue": 51_905,
            "grossRebateValue": 13_236, "netRebateValue": 0,
            "rateGross": "30%", "rateGrossValue": 30,
            "qualifyingSpend": "GBP 44,000", **over,
        }]}}

    def test_an_unconfirmed_scenario_does_not_explain_a_gross_rebate(self):
        _html, text = render(self._scenario(
            incentiveIsConfirmed=False, potentialIncentive="GBP 13,236",
        ))
        assert "Confirmed incentive" in text
        assert "Confirmed net position" in text
        # Describing a gross-rebate step implies a deduction the chart does not show.
        assert "Gross rebate" not in text

    def test_a_confirmed_scenario_keeps_the_waterfall_explanation(self):
        html, text = render(self._scenario(
            incentiveIsConfirmed=True, netRebateValue=13_236,
            netBudgetValue=38_669, netRebate="GBP 13,236",
        ))
        assert "Gross rebate" in text
        assert "GROSS REBATE" in html  # the chart itself


# ── Issue 3: the score is explained, not silently omitted ────────────────────

class TestScoringIsTransparent:
    def test_an_unscored_dimension_says_so(self):
        _html, text = render({"locationRankings": [{
            "name": "New York", "score": 56, "incentiveStrength": None,
            "incentiveReliability": 90, "costEfficiency": 50,
            "currencyAdvantage": 33, "crewDepth": 62, "infrastructure": 60,
        }]})
        assert "Not scored" in text
        assert "neither raises nor lowers" in text

    def test_a_scored_dimension_shows_its_number_without_the_note(self):
        _html, text = render({"locationRankings": [{
            "name": "Spain", "score": 60, "incentiveStrength": 65,
            "incentiveReliability": 90, "costEfficiency": 50,
            "currencyAdvantage": 33, "crewDepth": 62, "infrastructure": 60,
        }]})
        assert "Not scored" not in text


# ── Issue 13: a deadline belonging to another format ─────────────────────────

class TestFestivalDeadlineMatchesTheFormat:
    def _builder(self, fmt):
        b = ReportBuilder.__new__(ReportBuilder)
        b._production_format = fmt
        return b

    def test_a_feature_only_note_is_withheld_from_a_short(self):
        note = "Feature submissions typically close ~2 months before Nov festival"
        out = ReportBuilder._deadline_for_format(self._builder("Short"), note)
        assert "not independently verified" in out
        assert "feature" in out.lower()

    def test_the_same_note_is_kept_for_a_feature(self):
        note = "Feature submissions typically close ~2 months before Nov festival"
        assert ReportBuilder._deadline_for_format(self._builder("Feature Film"), note) == note

    def test_a_note_that_names_shorts_is_kept_for_a_short(self):
        note = "Short film deadline is in March"
        assert ReportBuilder._deadline_for_format(self._builder("Short"), note) == note

    def test_a_note_that_names_no_format_constrains_nothing(self):
        note = "Submissions open autumn prior year for the February festival"
        assert ReportBuilder._deadline_for_format(self._builder("Short"), note) == note

    @pytest.mark.parametrize("empty", [None, ""])
    def test_no_note_stays_no_note(self, empty):
        assert ReportBuilder._deadline_for_format(self._builder("Short"), empty) is None


# ── Issue 14 / 12: comparables safeguard and the orphan flag ─────────────────

class TestSectionSafeguards:
    def test_comparables_do_not_imply_eligibility(self):
        _html, text = render({"comparables": [
            {"title": "BlacKkKlansman", "genre": "Drama", "location": "US", "year": 2018},
        ]})
        assert "does not establish" in text

    def test_the_summary_flag_row_is_kept_with_its_page(self):
        html, _text = render({"executiveSummary": {
            "keyFlags": ["Extended shoot timeline increases carrying cost."],
        }})
        assert "page-break-before:avoid" in html

    def test_the_short_film_disclaimer_still_keeps_together(self):
        """Working behaviour that must not regress."""
        # The notice lives inside the tax-incentive section, so that section has to
        # render for the notice to appear at all.
        html, text = render({
            "incentiveEstimates": [{
                "territory": "New York", "program": "NY Credit", "rate": "30%",
                "incentiveIsConfirmed": False, "potentialIncentive": "GBP 13,236",
            }],
            "shortFormatIncentiveNotice": "Potential amounts are illustrative only.",
        })
        assert "illustrative" in text.lower()
        assert html.count("page-break-inside:avoid") >= 1
