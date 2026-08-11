"""A customer's report must not contain our data backlog.

The Financial Readiness section carries an "unverified or stale inputs" table. It is
genuinely useful — it tells a producer which figures are soft — but it was rendering
the raw database column beneath a heading that says INPUT, and, beneath one that says
WHAT TO DO, instructions addressed to us: "add comparable productions in the admin
comparables dataset", "curate a sourced cost-efficiency score", "record dated
submission deadlines in the admin festivals dataset".

A producer cannot act on any of that. It is our maintenance queue, printed inside a
document they may hand to a financier.
"""
from __future__ import annotations

import re

import pytest

from app.modules.reports.pdf_service import PDFService
from app.modules.reports.readiness import _flag, _humanise_field


def visible(html: str) -> str:
    stripped = re.sub(r'(?:src|href)="data:[^"]*"', " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped))


def render_with_flags(flags: list[dict]) -> str:
    html = PDFService().render_report_html(
        {
            "financialReadiness": {
                "verdict": "CONDITIONAL",
                "score": 60,
                "flags": flags,
                "flagCounts": {"critical": 0, "warning": 1, "info": 2},
                "components": [],
            }
        },
        script_title="T",
        created_at="2026-08-11T00:00:00Z",
    )
    return visible(html)


SAMPLE = [
    _flag(
        "warning",
        "territory_profiles.cost_efficiency_score (New Mexico)",
        "No sourced cost-efficiency score.",
        "Treat this territory's cost ranking as indicative.",
        label="Cost-efficiency data for New Mexico",
    ),
    _flag(
        "info",
        "comparable_productions.budget_usd",
        "0 comparables with a known budget.",
        "Treat the budget-versus-market comparison as unanchored.",
        label="Comparable production budgets",
    ),
    _flag(
        "info",
        "festivals.submission_deadline",
        "Matched festivals carry no dated deadline.",
        "Check each festival's own site for this cycle's dates.",
        label="Festival submission deadlines",
    ),
]


class TestNoInternalPlumbingReachesTheReader:
    @pytest.mark.parametrize("column", [
        "territory_profiles.",
        "comparable_productions.",
        "festivals.submission_deadline",
        "grants.max_amount",
        "incentive_programs.",
    ])
    def test_no_database_column_is_printed(self, column):
        assert column not in render_with_flags(SAMPLE)

    @pytest.mark.parametrize("phrase", [
        "admin comparables dataset",
        "admin festivals dataset",
        "admin grants dataset",
        "Curate a sourced",
        "Record max_amount",
    ])
    def test_no_instruction_addressed_to_us_is_printed(self, phrase):
        """These told the customer to edit datasets they cannot see."""
        assert phrase not in render_with_flags(SAMPLE)

    def test_the_human_label_is_printed_instead(self):
        text = render_with_flags(SAMPLE)
        assert "Cost-efficiency data for New Mexico" in text
        assert "Comparable production budgets" in text

    def test_the_raw_field_is_still_on_the_object_for_support(self):
        """Dropping it entirely would leave nobody able to tell which record is
        thin. It is withheld from the reader, not discarded."""
        assert SAMPLE[0]["input"] == "territory_profiles.cost_efficiency_score (New Mexico)"


class TestEveryFlagCarriesAReadableLabel:
    def test_a_flag_without_an_explicit_label_still_gets_a_readable_one(self):
        flag = _flag("info", "territory_profiles.crew_depth_score (Ireland)", "d", "a")
        assert flag["label"] == "Crew depth score (Ireland)"
        assert "territory_profiles" not in flag["label"]

    @pytest.mark.parametrize("field,expected", [
        ("budget_amount", "Budget amount"),
        ("comparable_productions.budget_usd", "Budget usd"),
        ("festivals.submission_deadline", "Submission deadline"),
        ("territory_profiles.cost_efficiency_score (New Mexico)",
         "Cost efficiency score (New Mexico)"),
    ])
    def test_the_fallback_never_leaks_a_table_name(self, field, expected):
        label = _humanise_field(field)
        assert label == expected
        assert "." not in label
        assert "_" not in label

    @pytest.mark.parametrize("junk", ["", None])
    def test_an_empty_field_still_produces_something_readable(self, junk):
        assert _humanise_field(junk) == "Report input"
