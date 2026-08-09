"""Timestamp coercion for values read back from the database.

The production failure this exists to prevent:

    TypeError: '>=' not supported between instances of 'datetime.datetime' and 'str'

The subscriber listing compared a stored ``created_at`` against an ISO string.
Postgres returns that column as a ``datetime`` and SQLite returns it as a string,
so the comparison passed every test and 500'd the live endpoint. Both shapes are
asserted here so a driver difference can never be the thing that reaches
production untested.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.core.timestamps import as_datetime, is_on_or_after

CUTOFF = datetime(2026, 8, 1, tzinfo=timezone.utc)


class TestAsDatetime:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absent_values_are_none(self, value):
        assert as_datetime(value) is None

    @pytest.mark.parametrize("value", ["not-a-date", "2026-13-45", "abc123"])
    def test_unparseable_values_are_none_not_an_exception(self, value):
        """A malformed timestamp drops out of a comparison; it does not take down
        the request that was counting the row."""
        assert as_datetime(value) is None

    def test_an_aware_datetime_passes_through(self):
        value = datetime(2026, 8, 5, 10, tzinfo=timezone.utc)
        assert as_datetime(value) == value

    def test_a_naive_datetime_is_assumed_utc(self):
        """Every writer in this codebase stores UTC, so a naive value from the
        driver is comparable with an aware one."""
        assert as_datetime(datetime(2026, 8, 5, 10)) == datetime(2026, 8, 5, 10, tzinfo=timezone.utc)

    def test_a_date_becomes_midnight_utc(self):
        assert as_datetime(date(2026, 8, 5)) == datetime(2026, 8, 5, tzinfo=timezone.utc)

    @pytest.mark.parametrize("text", [
        "2026-08-05T10:00:00Z",
        "2026-08-05T10:00:00+00:00",
        "2026-08-05T10:00:00",
        "2026-08-05",
    ])
    def test_iso_strings_parse(self, text):
        parsed = as_datetime(text)
        assert parsed is not None
        assert parsed.tzinfo is not None, "result must be aware so it is comparable"
        assert parsed.year == 2026 and parsed.month == 8 and parsed.day == 5


class TestIsOnOrAfter:
    def test_a_datetime_from_postgres(self):
        """The exact production shape: the driver returns a datetime."""
        assert is_on_or_after(datetime(2026, 8, 5, tzinfo=timezone.utc), CUTOFF) is True

    def test_an_iso_string_from_sqlite(self):
        """The exact test shape, which is why the bug survived to production."""
        assert is_on_or_after("2026-08-05T10:00:00Z", CUTOFF) is True

    def test_both_shapes_agree(self):
        """A driver difference must not change the answer."""
        as_dt = is_on_or_after(datetime(2026, 8, 5, 10, tzinfo=timezone.utc), CUTOFF)
        as_str = is_on_or_after("2026-08-05T10:00:00Z", CUTOFF)
        assert as_dt == as_str

    def test_the_boundary_is_inclusive(self):
        assert is_on_or_after(CUTOFF, CUTOFF) is True
        assert is_on_or_after("2026-08-01T00:00:00Z", CUTOFF) is True

    def test_before_the_cutoff_is_false(self):
        assert is_on_or_after("2026-07-31T23:59:59Z", CUTOFF) is False

    @pytest.mark.parametrize("value", [None, "", "not-a-date"])
    def test_an_unusable_value_is_not_evidence_of_being_in_the_window(self, value):
        assert is_on_or_after(value, CUTOFF) is False

    def test_a_naive_cutoff_still_compares(self):
        assert is_on_or_after("2026-08-05T10:00:00Z", datetime(2026, 8, 1)) is True

    def test_no_python_comparison_of_a_raw_db_timestamp_remains(self):
        """The pattern that caused the 500, asserted absent from the module.

        Comparisons that can live in the query should: ``.gte("created_at", ...)``
        is evaluated by the database and never has this problem.
        """
        import inspect

        from app.modules.subscribers import service

        source = inspect.getsource(service)
        assert ">= start_of_month.isoformat()" not in source
        assert "is_on_or_after" in source
