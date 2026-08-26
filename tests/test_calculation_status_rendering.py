"""Calculation status as a reader sees it.

The point of putting a status on the page is that a missing figure stops looking
like an oversight. So these render the real template and assert on visible text:
a programme with no cost base says so where its figure would be, and a programme
that calculated cleanly is not decorated with a chip that tells the reader
nothing.
"""
from __future__ import annotations

import html as _html
import re

import pytest

from app.modules.reports.pdf_service import PDFService


def _visible_text(markup: str) -> str:
    """Rendered text only, so an assertion cannot match a style rule or the logo."""
    out = re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I)
    out = re.sub(r'(?:src|href)="data:[^"]*"', " ", out)
    out = re.sub(r"<[^>]+>", " ", out)
    return _html.unescape(re.sub(r"\s+", " ", out))


def _estimate(**overrides):
    estimate = {
        "territory": "United Kingdom",
        "program": "Audio-Visual Expenditure Credit",
        "rate": "25.5% net",
        "cap": "80% of core expenditure",
        "qualifyingSpend": "£1,000,000",
        "estimatedRebate": "£8,160,000",
        "requirements": ["Pass the cultural test"],
        "lastUpdated": "2026-01-01",
        "calculationStatus": "ESTIMATED",
        "calculationStatusLabel": "Calculated",
        "calculationCarriesFigure": True,
        "calculationInRanking": True,
        "calculationVerification": "ready",
        "calculationIsApproved": True,
    }
    estimate.update(overrides)
    return estimate


def _render(estimates: list[dict]) -> str:
    return PDFService().render_report_html(
        {"incentiveEstimates": estimates},
        script_title="Status Fixture",
        created_at="2026-08-26T00:00:00Z",
    )


class TestNoFigure:
    @pytest.fixture(scope="class")
    def text(self) -> str:
        return _visible_text(_render([_estimate(
            estimatedRebate=None,
            calculationStatus="REQUIRES_COST_BREAKDOWN",
            calculationStatusLabel="Needs a cost breakdown",
            calculationCarriesFigure=False,
            calculationStatusReasons=["Not yet supplied: global core expenditure."],
            calculationStatusNextStep="Supply the statutory cost figures this "
                                      "programme calculates from.",
        )]))

    def test_the_status_appears_where_the_figure_would_be(self, text: str):
        assert "Needs a cost breakdown" in text

    def test_the_reason_is_given_not_just_the_refusal(self, text: str):
        """A producer cannot act on "no figure". They can act on which figure."""
        assert "global core expenditure" in text

    def test_the_next_step_is_offered(self, text: str):
        assert "To firm this up" in text

    def test_no_rebate_amount_is_printed(self, text: str):
        assert "Est. rebate" not in text


class TestCalculated:
    @pytest.fixture(scope="class")
    def text(self) -> str:
        return _visible_text(_render([_estimate()]))

    def test_the_figure_is_printed(self, text: str):
        assert "8,160,000" in text

    def test_a_clean_calculation_carries_no_chip(self, text: str):
        """A "Calculated" badge on every card is decoration, and decoration is
        what makes a real status invisible."""
        assert "Calculated" not in text

    def test_no_next_step_is_invented(self, text: str):
        assert "To firm this up" not in text


class TestConditional:
    @pytest.fixture(scope="class")
    def text(self) -> str:
        return _visible_text(_render([_estimate(
            calculationStatus="CONDITIONAL",
            calculationStatusLabel="Conditional",
            calculationCarriesFigure=True,
            calculationStatusReasons=["One or more figures are planning assumptions."],
            calculationStatusNextStep="Confirm the assumptions listed against this "
                                      "programme.",
        )]))

    def test_the_figure_survives_a_conditional_status(self, text: str):
        """Conditional is a qualified number, not a withheld one."""
        assert "8,160,000" in text

    def test_the_condition_is_named(self, text: str):
        assert "Conditional" in text
        assert "To firm this up" in text


class TestSectionNote:
    def test_the_two_gates_are_distinguished_once_for_the_section(self):
        """Chipping "formula awaiting approval" onto forty cards would be noise,
        and omitting it entirely would let a verified source imply an approved
        formula."""
        text = _visible_text(_render([_estimate()]))
        assert "reviewed separately" in text
