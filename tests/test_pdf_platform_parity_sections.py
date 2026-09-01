"""Sections that were rendered on the platform but silently absent from the PDF.

scriptIntelligence (the AI-narrative panel: creative recognition, schedule/
weather notes, complexity drivers) and dimensionVerdicts (the per-territory,
per-dimension verdict breakdown) were both computed by the backend and shown
in ReportViewer, but neither ever appeared in report_base.html — a producer
who downloaded the PDF got a document missing content they'd already seen on
the platform.
"""
from __future__ import annotations

from app.modules.reports.pdf_service import PDFService


def _base_report(**overrides) -> dict:
    report = {
        "genre": "Thriller",
        "tone": "Tense",
        "scale": "Mid-budget feature",
        "complexity": "Medium",
        "locationRankings": [
            {
                "name": "France", "country": "France", "score": 82,
                "bankabilityLabel": "BANKABLE",
                "incentiveStrength": 70, "incentiveReliability": 80,
                "costEfficiency": 60, "currencyAdvantage": 50,
                "crewDepth": 90, "infrastructure": 85,
                "reasoning": ["Strong incentive."],
            },
        ],
        "incentiveEstimates": [],
    }
    report.update(overrides)
    return report


def _render(report: dict) -> str:
    return PDFService().render_report_html(
        report, script_title="Parity Fixture", created_at="2026-08-30T00:00:00Z",
    )


class TestScriptIntelligenceInPDF:
    def test_creative_recognition_renders(self):
        html = _render(_base_report(scriptIntelligence={
            "creativeRecognition": "Strong festival potential noted here.",
        }))
        assert "Creative recognition" in html
        assert "Strong festival potential noted here." in html

    def test_schedule_weather_notes_render(self):
        html = _render(_base_report(scriptIntelligence={
            "scheduleWeatherNotes": "Winter shoot risks snow delays in the Alps.",
        }))
        assert "Winter shoot risks snow delays in the Alps." in html

    def test_complexity_drivers_render_as_a_list(self):
        html = _render(_base_report(scriptIntelligence={
            "complexityDrivers": ["Large ensemble cast", "Multiple night exteriors"],
        }))
        assert "Complexity drivers" in html
        assert "Large ensemble cast" in html
        assert "Multiple night exteriors" in html

    def test_absent_when_not_supplied(self):
        html = _render(_base_report())
        assert "Creative recognition" not in html
        assert "Complexity drivers" not in html


class TestDimensionVerdictsInPDF:
    def test_verdicts_for_the_top_territory_render(self):
        html = _render(_base_report(dimensionVerdicts={
            "France": {
                "Incentive value": "Strong, verified rate.",
                "Crew depth": "Deep local crew base.",
            },
        }))
        assert "Incentive value:" in html
        assert "Strong, verified rate." in html
        assert "Crew depth:" in html
        assert "Deep local crew base." in html

    def test_absent_when_not_supplied(self):
        html = _render(_base_report())
        assert "Incentive value:" not in html

    def test_a_territory_with_no_verdict_entry_does_not_crash(self):
        html = _render(_base_report(dimensionVerdicts={"Germany": {"Crew depth": "x"}}))
        assert "France" in html
