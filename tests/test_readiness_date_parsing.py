"""_parse_date must always yield a plain date.

Report generation died in production with:

    TypeError: unsupported operand type(s) for -: 'datetime.date' and 'datetime.datetime'
    readiness.py:1300  elif last_reviewed and (ctx.today - last_reviewed).days > STALE_DAYS

``datetime`` is a subclass of ``date``, so ``isinstance(value, date)`` passed a
timestamp straight through untouched. Every caller then subtracted it from
``ctx.today``, a plain date. The trap is silent under a string-returning driver and
fatal under one that hydrates timestamps, which is why it reached production.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.modules.reports.readiness import _parse_date


class TestAlwaysAPlainDate:
    @pytest.mark.parametrize(
        "value",
        [
            datetime(2024, 3, 1, 12, 30),
            datetime(2024, 3, 1, 12, 30, tzinfo=timezone.utc),
            date(2024, 3, 1),
            "2024-03-01",
            "2024-03-01T12:30:00Z",
            "last reviewed 2024-03-01 by admin",
        ],
    )
    def test_the_result_is_never_a_datetime(self, value):
        result = _parse_date(value)
        assert type(result) is date, f"{value!r} yielded {type(result).__name__}"
        assert result == date(2024, 3, 1)

    @pytest.mark.parametrize(
        "value",
        [
            datetime(2024, 3, 1, 12, 30),
            datetime(2024, 3, 1, 12, 30, tzinfo=timezone.utc),
            "2024-03-01T12:30:00Z",
        ],
    )
    def test_the_result_can_be_subtracted_from_today(self, value):
        """The operation that actually crashed. A naive and an aware timestamp both
        have to survive it: mixing tz-aware and naive datetimes raises too."""
        assert (date.today() - _parse_date(value)).days > 0

    @pytest.mark.parametrize("value", [None, "", "not a date", 12345, [], {}])
    def test_unusable_input_is_none_rather_than_a_guess(self, value):
        assert _parse_date(value) is None
