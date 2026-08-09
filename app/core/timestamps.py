"""Coercion for timestamps read back from the database.

The query builder returns whatever the driver produces: Postgres hands back real
``datetime`` objects, SQLite hands back strings. Code that compares a stored
timestamp in Python therefore works on one and raises ``TypeError`` on the other,
which is how the subscriber listing passed every test and then failed in
production with::

    TypeError: '>=' not supported between instances of 'datetime.datetime' and 'str'

Comparisons that can be pushed into the query should be: ``.gte("created_at",
...)`` is evaluated by the database and never has this problem. Use these helpers
only where the comparison genuinely has to happen in Python, such as bucketing
rows that were fetched once for several different counts.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any


def as_datetime(value: Any) -> datetime | None:
    """A timezone-aware ``datetime`` for *value*, or None if it is not a time.

    Accepts ``datetime`` (naive or aware), ``date``, and ISO-8601 strings
    including the trailing ``Z`` form Postgres and JSON both emit. Anything
    unparseable returns None rather than raising: a row with a malformed
    timestamp should drop out of a comparison, not take down the request that
    was counting it.

    Naive values are assumed UTC, which is what every writer in this codebase
    stores, so a naive and an aware value can still be compared.
    """
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_on_or_after(value: Any, cutoff: datetime) -> bool:
    """True when *value* is a timestamp at or after *cutoff*.

    An unparseable or missing timestamp is False: it is not evidence that the row
    falls inside the window.
    """
    moment = as_datetime(value)
    if moment is None:
        return False
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return moment >= cutoff
