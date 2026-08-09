"""Incentive eligibility is not recorded per format, and short films pay for it.

``incentive_programs.applicable_formats`` exists, ``best_incentive`` filters on it,
and it is NULL on every row. NULL means "applies to all formats", so a short film
is currently modelled against every programme as though each one accepted shorts.
Short-form work is frequently excluded from production tax credits and supported
instead by separate grant schemes with much smaller awards, so a feature-scale
rebate quoted against a short overstates what the production can claim.

Until the eligibility data exists, the report says so. These tests pin both halves:
the caveat appears for the affected formats, and it disappears on its own once the
data is populated, so it cannot become permanent furniture.
"""
from __future__ import annotations

import pytest

from app.modules.reports.helpers import (
    FORMATS_NEEDING_ELIGIBILITY_CHECK,
    best_incentive,
    format_eligibility_is_recorded,
    needs_format_eligibility_check,
)


class TestNeedsFormatEligibilityCheck:
    @pytest.mark.parametrize("fmt", ["Short", "short", "SHORT", " Short ", "Short Film", "short film"])
    def test_short_form_needs_confirmation(self, fmt):
        assert needs_format_eligibility_check(fmt) is True

    @pytest.mark.parametrize("fmt", [
        "Feature Film", "TV Series", "TV Pilot", "Limited Series",
        "Documentary", "Animated Feature",
    ])
    def test_other_formats_use_the_default_assumption(self, fmt):
        """Most production incentives are written for features and long-form TV,
        so the default is safe there and a warning on every report would be noise."""
        assert needs_format_eligibility_check(fmt) is False

    @pytest.mark.parametrize("fmt", [None, "", "   "])
    def test_no_format_is_not_a_warning(self, fmt):
        assert needs_format_eligibility_check(fmt) is False

    def test_the_frontend_list_is_mirrored(self):
        """The wizard holds the same list so it can ask for confirmation. If one
        side gains a format the other must too, or a producer is either warned
        without being asked to confirm, or asked without being told why."""
        assert FORMATS_NEEDING_ELIGIBILITY_CHECK == frozenset({"short", "short film"})


class TestFormatEligibilityIsRecorded:
    def test_all_null_is_not_recorded(self):
        """The live state: 49 rows, every applicable_formats NULL."""
        assert format_eligibility_is_recorded([{"applicable_formats": None}] * 49) is False

    def test_no_rows_is_not_recorded(self):
        assert format_eligibility_is_recorded([]) is False
        assert format_eligibility_is_recorded(None) is False

    def test_an_empty_list_is_not_recorded(self):
        """An empty array means "all formats" in best_incentive, so it states
        nothing about eligibility."""
        assert format_eligibility_is_recorded([{"applicable_formats": []}]) is False

    def test_one_populated_row_counts_as_recorded(self):
        rows = [{"applicable_formats": None}, {"applicable_formats": ["feature"]}]
        assert format_eligibility_is_recorded(rows) is True

    def test_a_json_string_column_is_parsed(self):
        """Some drivers hand JSON columns back as text."""
        assert format_eligibility_is_recorded([{"applicable_formats": '["feature", "short"]'}]) is True

    def test_unparseable_json_is_not_treated_as_recorded(self):
        assert format_eligibility_is_recorded([{"applicable_formats": "{not json"}]) is False


class TestBestIncentiveFormatFiltering:
    """The filtering itself, which the caveat exists to cover the absence of."""

    def test_a_programme_that_excludes_the_format_is_skipped(self):
        rows = [
            {"program": "features only", "rate_gross": 40, "applicable_formats": ["feature"]},
            {"program": "shorts too", "rate_gross": 20, "applicable_formats": ["feature", "short"]},
        ]
        assert best_incentive(rows, "short")["program"] == "shorts too"

    def test_a_null_column_is_treated_as_every_format(self):
        """Which is exactly why the caveat is needed while the column is unset:
        the highest rate wins even though nothing confirmed it accepts a short."""
        rows = [
            {"program": "unstated high", "rate_gross": 40, "applicable_formats": None},
            {"program": "states shorts", "rate_gross": 20, "applicable_formats": ["short"]},
        ]
        assert best_incentive(rows, "short")["program"] == "unstated high"

    def test_excluding_every_row_falls_back_rather_than_failing(self):
        """Documented graceful degradation. It is also why the report has to say
        the figure is unconfirmed: the fallback returns an ineligible programme
        rather than no programme."""
        rows = [{"program": "features only", "rate_gross": 40, "applicable_formats": ["feature"]}]
        assert best_incentive(rows, "short")["program"] == "features only"
