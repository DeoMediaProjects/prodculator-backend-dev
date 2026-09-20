"""One provenance envelope for every engine result in a report run.

WHY AN ENVELOPE AND NOT A CONVENTION
------------------------------------
A report run freezes one ProjectFacts snapshot, and every specialist engine
computes against it. Two runs' results merged into one report is the hardest
kind of wrong to notice: each section reads plausibly, nothing raises, and only
the numbers disagree with each other.

The validator has checked for that since the snapshot landed, but it checked a
hard-coded list of four keys. That is a convention, and the failure mode of a
convention is the sixth engine: someone adds ``comparableStrategy`` to the
report, forgets to add it to the list, and the check goes on passing while no
longer covering the thing it was added for.

So the check here is by SHAPE. Anything in the report that looks like an engine
result — a mapping carrying recommendations, a universe count, or provenance of
its own — must be stamped. An engine added next year is covered on the day it is
added, without anyone remembering this file exists.

WHAT STAMPING MEANS
-------------------
``stamped`` is the only way a result should acquire its provenance. It exists so
the four field names are written once: an engine that spells
``projectfacts_version`` as ``projectFactsVersion`` would be silently unstamped
under a check looking for the first spelling, and would sail through.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: The fields every engine result carries. Named here so no engine spells one
#: of them differently and quietly falls out of the consistency check.
SNAPSHOT_ID_FIELD = "projectfacts_snapshot_id"
SNAPSHOT_VERSION_FIELD = "projectfacts_version"
ENGINE_NAME_FIELD = "engine_name"
ENGINE_VERSION_FIELD = "engine_version"

#: The report-level fields naming the run's own snapshot.
REPORT_SNAPSHOT_ID_FIELD = "projectFactsSnapshotId"
REPORT_SNAPSHOT_VERSION_FIELD = "projectFactsVersion"

#: Keys that are report metadata rather than engine results, and are exempt
#: from stamping even though they are mappings. Listed explicitly and kept
#: short: every addition here is a hole in the check.
_EXEMPT_KEYS: frozenset[str] = frozenset(
    {
        "projectFactsSnapshot",
        "executiveSummary",
        "financialReadiness",
        "scriptIntelligence",
        "dimensionVerdicts",
    }
)

#: Markers that identify a mapping as an engine result. Any one is enough: an
#: engine that returns a universe count but no recommendations this run has
#: still run, and still needs to say which facts it ran against.
_ENGINE_RESULT_MARKERS: frozenset[str] = frozenset(
    {
        "recommendations",
        "eligible_universe_count",
        "universe_count",
        "actionable_count",
        SNAPSHOT_ID_FIELD,
        SNAPSHOT_VERSION_FIELD,
    }
)


class InconsistentInputVersion(ValueError):
    """Engine results in one report that were computed against different facts."""


def stamped(
    payload: Mapping[str, Any],
    snapshot: Any,
    *,
    engine_name: str,
    engine_version: str,
) -> dict[str, Any]:
    """One engine result, carrying the snapshot it was computed against.

    ``snapshot`` is a ``ProjectFactsSnapshot``. Passing the object rather than
    its two fields is deliberate: a caller holding the snapshot cannot stamp a
    result with an ID from one run and a version from another.
    """
    return {
        **dict(payload),
        SNAPSHOT_ID_FIELD: snapshot.snapshot_id,
        SNAPSHOT_VERSION_FIELD: snapshot.version,
        ENGINE_NAME_FIELD: engine_name,
        ENGINE_VERSION_FIELD: engine_version,
    }


def looks_like_an_engine_result(key: str, value: Any) -> bool:
    """Whether this top-level report entry is something an engine produced."""
    if key in _EXEMPT_KEYS or not isinstance(value, Mapping):
        return False
    return bool(_ENGINE_RESULT_MARKERS & set(value))


def engine_results(report: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    return [
        (key, value)
        for key, value in report.items()
        if looks_like_an_engine_result(key, value)
    ]


def assert_consistent(report: Mapping[str, Any]) -> None:
    """Every engine result in this report ran against the report's own facts.

    Raises rather than warning. A warning here would be advice about a report
    that is already internally inconsistent, and the report would still be
    delivered.
    """
    snapshot_id = report.get(REPORT_SNAPSHOT_ID_FIELD)
    version = report.get(REPORT_SNAPSHOT_VERSION_FIELD)

    for key, result in engine_results(report):
        result_id = result.get(SNAPSHOT_ID_FIELD)
        result_version = result.get(SNAPSHOT_VERSION_FIELD)

        if snapshot_id and (result_id is None or result_version is None):
            raise InconsistentInputVersion(
                f"INCONSISTENT_INPUT_VERSION: {key} lacks ProjectFacts provenance"
            )
        if result_id is not None and (not snapshot_id or result_id != snapshot_id):
            raise InconsistentInputVersion(
                f"INCONSISTENT_INPUT_VERSION: {key} uses a different ProjectFacts "
                "snapshot"
            )
        if result_version is not None and (not version or result_version != version):
            raise InconsistentInputVersion(
                f"INCONSISTENT_INPUT_VERSION: {key} uses a different ProjectFacts "
                "version"
            )


def assert_strategies_consistent(
    strategies: Iterable[Any], *, snapshot_id: str, version: str
) -> None:
    """The same check for canonical strategy objects rather than report dicts.

    Strategy objects carry their provenance as attributes, so the report-shaped
    check cannot see them. A strategy with no snapshot recorded is refused
    rather than assumed to belong to this run: an untagged result is exactly the
    one whose origin nobody can establish.
    """
    for strategy in strategies:
        if strategy is None:
            continue
        name = type(strategy).__name__
        result_id = getattr(strategy, SNAPSHOT_ID_FIELD, None)
        result_version = getattr(strategy, SNAPSHOT_VERSION_FIELD, None)
        if result_id is None or result_version is None:
            raise InconsistentInputVersion(
                f"INCONSISTENT_INPUT_VERSION: {name} carries no ProjectFacts "
                "provenance"
            )
        if result_id != snapshot_id or result_version != version:
            raise InconsistentInputVersion(
                f"INCONSISTENT_INPUT_VERSION: {name} was computed against "
                f"ProjectFacts {result_id}/{result_version}, not "
                f"{snapshot_id}/{version}"
            )
