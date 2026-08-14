"""Issue 3: the shoot month is compared to the window in Python, not inferred.

The report printed "Best months: March, April, May, June" and, beneath it, an LLM
sentence saying an August shoot "falls within the UK's optimal production window of
March through June".

Two defects, and the order matters. ``bestMonths`` was ``best_months[:4]`` over a
January→December scan, so it was the earliest four acceptable months rather than the
best four: the UK's qualifying months are 3-9 and August (50mm rain, low storm risk)
was cut by the slice; South Africa's are 4-9 and August scores 93, the second-best
month of its year, yet the slice printed April-July. Then nothing compared the shoot
month to the list — the field was marked "AI fills" and the model was handed the
already-truncated list.

So the model was describing a list that had been silently cut. Both halves are tested
here: the window is the full qualifying set, and membership is computed.
"""
from __future__ import annotations

import pytest

from app.modules.reports.shoot_window import (
    ADJACENT,
    INSIDE,
    OUTSIDE,
    UNKNOWN,
    classify_shoot_window,
    format_month_ranges,
    narrative_contradicts_window,
)


class TestMonthInsideRange:
    def test_month_inside_the_window(self):
        assert classify_shoot_window([5], [3, 4, 5, 6])["verdict"] == INSIDE

    def test_every_shoot_month_inside(self):
        assert classify_shoot_window([4, 5, 6], [3, 4, 5, 6, 7])["verdict"] == INSIDE

    def test_august_is_inside_the_real_uk_window(self):
        """The UK's qualifying months are 3-9 on the seeded weather data.

        The report said March-June because of the [:4] slice. Against the untruncated
        window an August shoot genuinely IS inside it — which is why fixing the prose
        alone would have left the UK's shoot window understated.
        """
        uk_qualifying = [3, 4, 5, 6, 7, 8, 9]
        assert classify_shoot_window([8], uk_qualifying)["verdict"] == INSIDE

    def test_august_is_inside_the_real_south_africa_window(self):
        """South Africa's qualifying months are 4-9; August scores 93 of 100."""
        sa_qualifying = [4, 5, 6, 7, 8, 9]
        assert classify_shoot_window([8], sa_qualifying)["verdict"] == INSIDE


class TestMonthOutsideRange:
    def test_month_immediately_before_the_window(self):
        result = classify_shoot_window([2], [3, 4, 5, 6])
        assert result["verdict"] == ADJACENT
        assert result["monthsInside"] == []

    def test_month_immediately_after_the_window(self):
        result = classify_shoot_window([7], [3, 4, 5, 6])
        assert result["verdict"] == ADJACENT

    def test_august_against_a_march_to_june_window_is_never_inside(self):
        """The acceptance criterion, stated directly.

        Kept even though the real UK window is wider, because it is the assertion the
        original defect is written against: whatever the window is, a month outside it
        must not classify as inside.
        """
        result = classify_shoot_window([8], [3, 4, 5, 6])
        assert result["verdict"] != INSIDE
        assert result["verdict"] == OUTSIDE

    def test_august_against_an_april_to_july_window_is_never_inside(self):
        result = classify_shoot_window([8], [4, 5, 6, 7])
        assert result["verdict"] != INSIDE
        # Immediately after July.
        assert result["verdict"] == ADJACENT

    def test_far_outside_is_outside_not_adjacent(self):
        assert classify_shoot_window([12], [4, 5, 6])["verdict"] == OUTSIDE


class TestYearWrappingWindows:
    def test_inside_a_november_to_february_window(self):
        for month in (11, 12, 1, 2):
            assert classify_shoot_window([month], [11, 12, 1, 2])["verdict"] == INSIDE

    def test_march_is_adjacent_to_a_november_to_february_window(self):
        assert classify_shoot_window([3], [11, 12, 1, 2])["verdict"] == ADJACENT

    def test_october_is_adjacent_to_a_november_to_february_window(self):
        assert classify_shoot_window([10], [11, 12, 1, 2])["verdict"] == ADJACENT

    def test_june_is_outside_a_november_to_february_window(self):
        assert classify_shoot_window([6], [11, 12, 1, 2])["verdict"] == OUTSIDE

    def test_december_is_adjacent_to_a_january_window(self):
        """Modular arithmetic, so December borders January without special-casing."""
        assert classify_shoot_window([12], [1, 2])["verdict"] == ADJACENT


class TestUnknownAndAbsentData:
    def test_no_best_months_is_unknown(self):
        assert classify_shoot_window([8], [])["verdict"] == UNKNOWN

    def test_na_best_months_is_unknown(self):
        assert classify_shoot_window([8], ["N/A"])["verdict"] == UNKNOWN

    def test_no_shoot_months_is_unknown(self):
        assert classify_shoot_window([], [3, 4, 5])["verdict"] == UNKNOWN

    def test_none_inputs_are_unknown(self):
        assert classify_shoot_window(None, None)["verdict"] == UNKNOWN


class TestPartialOverlap:
    def test_a_shoot_straddling_the_boundary_is_not_reported_as_inside(self):
        """"Some of your shoot is in the good window" must not render as "your shoot
        is in the good window"."""
        result = classify_shoot_window([6, 7, 8], [4, 5, 6])
        assert result["verdict"] == ADJACENT
        assert result["partialOverlap"] is True
        assert result["monthsInside"] == [6]
        assert result["monthsOutside"] == [7, 8]


class TestMonthNameInputs:
    def test_month_names_are_accepted(self):
        assert classify_shoot_window(["August"], ["March", "April"])["verdict"] == OUTSIDE

    def test_abbreviations_are_accepted(self):
        assert classify_shoot_window(["Aug"], ["Jul", "Aug"])["verdict"] == INSIDE

    def test_unparseable_entries_are_dropped_not_fatal(self):
        result = classify_shoot_window([8], ["March", "", "wibble", "April"])
        assert result["optimalMonths"] == [3, 4]


class TestFormatMonthRanges:
    def test_contiguous_run_collapses(self):
        assert format_month_ranges([3, 4, 5, 6, 7, 8, 9]) == "March to September"

    def test_single_month(self):
        assert format_month_ranges([8]) == "August"

    def test_two_adjacent_months(self):
        assert format_month_ranges([7, 8]) == "July and August"

    def test_disjoint_runs(self):
        assert format_month_ranges([1, 2, 7, 8, 9]) == (
            "January and February, plus July to September"
        )

    def test_empty(self):
        assert format_month_ranges([]) == ""


class TestNarrativeContradiction:
    def test_inside_claim_against_an_outside_verdict_is_caught(self):
        problem = narrative_contradicts_window(
            "An August 2026 shoot falls within the UK's optimal production window of "
            "March through June.",
            OUTSIDE,
        )
        assert problem is not None
        assert "inside" in problem

    def test_inside_claim_against_an_adjacent_verdict_is_caught(self):
        assert narrative_contradicts_window(
            "The shoot falls within the optimal window.", ADJACENT
        ) is not None

    def test_outside_claim_against_an_inside_verdict_is_caught(self):
        assert narrative_contradicts_window(
            "The August shoot falls outside the optimal window.", INSIDE
        ) is not None

    def test_agreeing_prose_passes(self):
        assert narrative_contradicts_window(
            "An August shoot falls outside the optimal April to July window.", ADJACENT
        ) is None
        assert narrative_contradicts_window(
            "An August shoot falls within the optimal March to September window.", INSIDE
        ) is None

    def test_unknown_verdict_never_contradicts(self):
        assert narrative_contradicts_window("anything at all", UNKNOWN) is None

    def test_empty_text_never_contradicts(self):
        assert narrative_contradicts_window(None, OUTSIDE) is None
        assert narrative_contradicts_window("", OUTSIDE) is None
