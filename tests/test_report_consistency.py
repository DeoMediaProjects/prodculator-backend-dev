"""Cross-section consistency invariants for a generated report.

Every case here is a contradiction a producer found in a delivered PDF. The unit
is "two sections of one report disagreeing", not "one function returns the wrong
value", because each of these bugs was produced by code that was individually
correct and collectively inconsistent.
"""
from __future__ import annotations

import re

import pytest

from app.modules.reports.helpers import (
    DEFAULT_SHOOT_DAYS_PER_WEEK,
    format_payment_timing,
    resolve_payment_timing,
    resolve_schedule,
)
from app.modules.reports.validator import ReportValidator


# ── Payment timing ───────────────────────────────────────────────────────────

class TestPaymentTimingResolution:
    """One canonical window per programme, whatever the source records."""

    def test_numeric_days_are_used_when_the_note_is_blank(self):
        """The bug: Italy recorded 180-365 days and the report said "Data not
        available", because every consumer read only payment_timeline_notes."""
        timing = resolve_payment_timing({
            "payment_timeline_days_min": 180,
            "payment_timeline_days_max": 365,
            "payment_timeline_notes": None,
        })
        assert timing["minMonths"] == 6
        assert timing["maxMonths"] == 12
        assert timing["source"] == "programme"
        assert timing["label"] == "6 to 12 months"
        assert "not available" not in timing["label"].lower()

    def test_equal_bounds_collapse_to_one_figure(self):
        """The bug: the chart rendered "12-12 MO", a range whose ends match,
        which reads as a data error rather than as a twelve-month wait."""
        timing = resolve_payment_timing({
            "payment_timeline_days_min": 360,
            "payment_timeline_days_max": 360,
        })
        assert timing["label"] == "12 months"
        assert "12 to 12" not in timing["label"]
        assert not re.search(r"(\d+)\D+\1\s*month", timing["label"])

    def test_single_month_is_singular(self):
        assert format_payment_timing(1, 1) == "1 month"

    def test_territory_research_is_the_documented_fallback(self):
        """Used only when the programme records nothing, and it says so, so
        research-derived timing is never presented as programme-stated."""
        timing = resolve_payment_timing(
            {},
            {
                "cert_weeks_min": 4, "cert_weeks_max": 8,
                "payment_weeks_min": 9, "payment_weeks_max": 17,
            },
        )
        assert timing["source"] == "territory_research"
        assert timing["minMonths"] is not None and timing["maxMonths"] is not None

    def test_programme_numbers_beat_territory_research(self):
        timing = resolve_payment_timing(
            {"payment_timeline_days_min": 90, "payment_timeline_days_max": 180},
            {"cert_weeks_max": 52, "payment_weeks_max": 52},
        )
        assert timing["source"] == "programme"
        assert (timing["minMonths"], timing["maxMonths"]) == (3, 6)

    def test_half_a_window_is_not_a_window(self):
        """A verified certification window plus a missing payment window is not a
        completion-to-cash figure, and must not be rendered as one."""
        timing = resolve_payment_timing({}, {"cert_weeks_min": 4, "cert_weeks_max": 8})
        assert timing["minMonths"] is None
        assert timing["label"] == "Data not available"

    def test_no_timing_anywhere_says_so(self):
        assert resolve_payment_timing({}, {})["label"] == "Data not available"
        assert resolve_payment_timing(None, None)["label"] == "Data not available"

    def test_free_text_note_survives_when_there_are_no_numbers(self):
        timing = resolve_payment_timing({"payment_timeline_notes": "post-audit, staged"})
        assert timing["label"] == "post-audit, staged"


class TestPaymentTimingConsistencyInvariant:
    def test_sections_disagreeing_is_flagged(self):
        report = {
            "locationRankings": [
                {"name": "Italy", "paymentTiming": {"minMonths": 6, "maxMonths": 12}},
            ],
            "financialAnalysis": {
                "paymentTiming": [
                    {"territory": "Italy", "paymentTiming": {"minMonths": 12, "maxMonths": 12}},
                ],
            },
        }
        warnings: list[str] = []
        ReportValidator._assert_payment_timing_consistency(report, warnings)
        assert any("Italy" in w and "payment window" in w for w in warnings), warnings

    def test_sections_agreeing_is_silent(self):
        window = {"minMonths": 6, "maxMonths": 12}
        report = {
            "locationRankings": [{"name": "Italy", "paymentTiming": dict(window)}],
            "territoryDeepDives": [{"name": "Italy", "paymentTiming": dict(window)}],
            "financialAnalysis": {
                "paymentTiming": [{"territory": "Italy", "paymentTiming": dict(window)}],
            },
        }
        warnings: list[str] = []
        ReportValidator._assert_payment_timing_consistency(report, warnings)
        assert warnings == []

    def test_a_section_with_no_window_does_not_count_as_disagreement(self):
        report = {
            "locationRankings": [{"name": "Japan", "paymentTiming": {"minMonths": 4, "maxMonths": 9}}],
            "territoryDeepDives": [
                {"name": "Japan", "paymentTiming": {"minMonths": None, "maxMonths": None}},
            ],
        }
        warnings: list[str] = []
        ReportValidator._assert_payment_timing_consistency(report, warnings)
        assert warnings == []


# ── Schedule ─────────────────────────────────────────────────────────────────

class TestScheduleResolution:
    def test_declared_duration_is_never_overwritten(self):
        """The producer's own schedule is a decision, not an estimate."""
        schedule = resolve_schedule(declared_weeks=8, script_shoot_days=14)
        assert schedule["shootWeeks"] == 8
        assert schedule["weeksSource"] == "declared"

    def test_the_reported_divergence_is_detected(self):
        """Lost in Translation: 14 shooting days beside an 8 week shoot."""
        schedule = resolve_schedule(declared_weeks=8, script_shoot_days=14)
        assert schedule["divergent"] is True
        assert schedule["impliedWeeks"] == pytest.approx(2.8, abs=0.05)

    def test_a_coherent_pair_is_not_flagged(self):
        assert resolve_schedule(declared_weeks=3, script_shoot_days=14)["divergent"] is False

    def test_weeks_derive_from_days_when_undeclared(self):
        schedule = resolve_schedule(declared_weeks=None, script_shoot_days=55)
        assert schedule["weeksSource"] == "derived_from_script"
        assert schedule["shootWeeks"] == round(55 / DEFAULT_SHOOT_DAYS_PER_WEEK)

    def test_a_short_shoot_never_rounds_to_zero_weeks(self):
        assert resolve_schedule(None, 2)["shootWeeks"] == 1

    def test_a_long_shoot_scales(self):
        schedule = resolve_schedule(None, 120)
        assert schedule["shootWeeks"] == 24

    def test_no_inputs_yields_no_schedule(self):
        schedule = resolve_schedule(None, None)
        assert schedule["shootWeeks"] is None
        assert schedule["divergent"] is False

    def test_one_divisor_across_the_pipeline(self):
        """Completion-date estimation used 5.5 days per week while the report
        service used 5, so the same script produced two shoot lengths."""
        import inspect

        from app.modules.reports import matching

        source = inspect.getsource(matching.estimate_completion_date)
        assert "DEFAULT_SHOOT_DAYS_PER_WEEK" in source
        assert "/ 5.5" not in source


class TestScheduleConsistencyInvariant:
    def test_contradictory_schedule_is_flagged(self):
        report = {
            "executiveSummary": {"shootWeeks": 8},
            "scriptStats": {"estShootingDays": 14},
        }
        warnings: list[str] = []
        ReportValidator._assert_schedule_consistency(report, warnings)
        assert any("shooting days" in w and "weeks" in w for w in warnings), warnings

    def test_coherent_schedule_is_silent(self):
        report = {
            "executiveSummary": {"shootWeeks": 3},
            "scriptStats": {"estShootingDays": 14},
        }
        warnings: list[str] = []
        ReportValidator._assert_schedule_consistency(report, warnings)
        assert warnings == []

    def test_missing_either_figure_is_not_a_contradiction(self):
        warnings: list[str] = []
        ReportValidator._assert_schedule_consistency(
            {"executiveSummary": {"shootWeeks": 8}, "scriptStats": {}}, warnings,
        )
        assert warnings == []


# ── Ranked territories ───────────────────────────────────────────────────────

class TestRankedTerritoryInvariant:
    def test_a_ranked_territory_with_no_profile_is_flagged(self):
        """The bug: the strategy heading declared four ranked territories while
        the cards were sliced to three, so Singapore was counted and never
        shown, yet still appeared in weather and tax sections."""
        report = {
            "locationRankings": [
                {"name": "United Kingdom"}, {"name": "Japan"},
                {"name": "Italy"}, {"name": "Singapore"},
            ],
            "territoryDeepDives": [
                {"name": "United Kingdom"}, {"name": "Japan"}, {"name": "Italy"},
            ],
        }
        warnings: list[str] = []
        ReportValidator._assert_ranked_territory_consistency(report, warnings)
        assert any("Singapore" in w for w in warnings), warnings

    @pytest.mark.parametrize("count", [3, 4, 5, 7])
    def test_matching_sets_are_silent_at_any_count(self, count):
        names = [f"Territory {i}" for i in range(count)]
        report = {
            "locationRankings": [{"name": n} for n in names],
            "territoryDeepDives": [{"name": n} for n in reversed(names)],
        }
        warnings: list[str] = []
        ReportValidator._assert_ranked_territory_consistency(report, warnings)
        assert warnings == []

    def test_no_rankings_is_not_a_violation(self):
        warnings: list[str] = []
        ReportValidator._assert_ranked_territory_consistency({}, warnings)
        assert warnings == []


# ── PDF template: the rendering half of the same invariants ──────────────────

class TestTemplateDoesNotTruncateRankedSections:
    """The template is where the counts diverged, so it is asserted directly.

    A hardcoded slice here is invisible in data-level tests: the payload carried
    four ranked territories and the PDF simply stopped drawing after three.
    """

    @staticmethod
    def _template() -> str:
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "app" / "templates" / "pdf" / "report_base.html"
        return path.read_text(encoding="utf-8")

    def test_ranked_sections_are_not_sliced(self):
        tpl = self._template()
        assert "rankings[:3]" not in tpl
        assert "rankings[1:3]" not in tpl
        assert "territoryDeepDives[:3]" not in tpl
        assert "weatherLogistics[:4]" not in tpl

    def test_the_payment_chart_renders_the_canonical_label(self):
        tpl = self._template()
        assert "entry.label" in tpl
        # The old chart recomputed months from weeks, which is what allowed it to
        # disagree with the cards and to print a degenerate range.
        assert "totalWeeksMax / 4.345" not in tpl
        assert "MO<" not in tpl
