"""Selecting the programme rule version that applied on a given date.

The specification is explicit that historic rules must remain available "for
projects whose qualifying period predates the change", and the QA matrix tests it
directly: an effective-date boundary must select the historical rule rather than
the newest row.

Selection is by date, never by recency. A newest-row shortcut is the bug this
module exists to prevent, because it silently recalculates a 2025 production under
a 2026 rate.

No database access here so the rule can be unit tested against plain dicts; the
caller supplies the candidate versions.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from app.modules.incentives.v2_contracts import CALCULATION_READY


class RuleVersionError(RuntimeError):
    """No rule version covers the requested date."""


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def covers(version: dict, on: date) -> bool:
    """Whether a rule version was in force on ``on``.

    ``effective_from`` is inclusive. ``effective_to`` is inclusive and NULL means
    open ended, which is the current version. Treating a NULL end date as "does
    not apply" would exclude precisely the row that usually should win.
    """
    start = _as_date(version.get("effective_from"))
    if start is None or on < start:
        return False
    end = _as_date(version.get("effective_to"))
    return end is None or on <= end


def select_rule_version(
    versions: Iterable[dict],
    on: date,
    *,
    require_ready: bool = True,
) -> dict:
    """The rule version in force on ``on``.

    ``require_ready`` enforces the calculation gate: a version whose
    ``calculation_verification_status`` is not ready may exist and be readable,
    but must not be used to produce a figure. Pass False to inspect a version for
    admin display.

    Raises ``RuleVersionError`` rather than falling back to the newest version. A
    fallback would produce a number under a rule that did not apply, which is
    worse than producing none.
    """
    candidates = [v for v in versions if isinstance(v, dict) and covers(v, on)]
    if not candidates:
        raise RuleVersionError(
            f"No rule version in force on {on.isoformat()}. A project whose "
            f"qualifying date falls outside every recorded rule period cannot be "
            f"calculated; record the applicable historical rule first."
        )

    if len(candidates) > 1:
        # Overlapping periods are a data error, not something to resolve by
        # picking one. Two rules in force on the same day means one of them has
        # the wrong effective date, and choosing silently hides that.
        overlapping = ", ".join(
            str(v.get("rule_version") or "unversioned") for v in candidates
        )
        raise RuleVersionError(
            f"{len(candidates)} rule versions are in force on {on.isoformat()} "
            f"({overlapping}). Effective periods must not overlap; correct the "
            f"dates before calculating."
        )

    version = candidates[0]
    if require_ready:
        status = str(version.get("calculation_verification_status") or "").lower()
        if status != CALCULATION_READY:
            raise RuleVersionError(
                f"Rule version {version.get('rule_version')!r} is in force on "
                f"{on.isoformat()} but its calculation verification status is "
                f"{status or 'unset'!r}, not {CALCULATION_READY!r}. No figure may "
                f"be produced from it."
            )
    return version


def qualifying_date(project: dict) -> date | None:
    """The date that decides which rule version applies.

    Principal photography start is the usual statutory hook, so it is preferred.
    Completion date is the fallback for a project that has not dated its shoot.
    Returns None when neither is known, which the caller must treat as "cannot
    select a version" rather than defaulting to today: today's rate is not
    necessarily the rate that will apply to a future shoot.
    """
    for field in ("filming_start_date", "completion_date"):
        parsed = _as_date(project.get(field))
        if parsed is not None:
            return parsed
    return None
