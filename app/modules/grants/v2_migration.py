"""Reconciling live grant rows against the frozen Grants v2 migration maps.

WHAT THIS ANSWERS
-----------------
The handoff's migration instructions say to back up the live table, stage the
v2 master, apply the legacy ID mapping, retire SPLIT parents, route RECLASSIFIED
rows to their target engine and archive the rest. Before any of that runs, one
question has to be answered honestly: for every row currently live, does the
freeze actually say what becomes of it?

This module answers that and nothing else. It reads live rows and the maps, and
returns a verdict per live ID plus the conflicts. It writes nothing, and it has
no database dependency, so the decision is testable without a migration.

WHY A DECISION IS A SET
-----------------------
The mapping's ``decision`` column is compound: ``CORRECT``, but also
``ARCHIVE_RECLASSIFY``, ``CORRECT_RECLASSIFY_NEEDS_TERMS`` and
``SUSPEND_SPLIT``. Eighteen distinct strings appear across 88 rows. Treating the
string as an enum means either eighteen branches that drift apart, or a prefix
match that reads ``CORRECT_SUSPEND`` as ``CORRECT`` and leaves a suspended
programme live and matchable.

So a decision parses into the set of actions it names. A row that both corrects
and reclassifies does both, and a row naming an action this module does not
recognise is reported as unrecognised rather than defaulting to the safe-looking
one — a decision nobody encoded is not the same as a decision to retain.

THE RULE EVERY VERDICT OBEYS
----------------------------
A live row may keep taking paid recommendation slots only where the freeze says
so explicitly. Silence is not retention. An unmapped live row, a row whose
decision needs review, and a row whose mapping resolves to no live ID are all
blocking findings, because each one is a record that would otherwise survive the
migration by default and go on matching against a producer's project with
nothing current behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# ── Actions a decision can name ──────────────────────────────────────────────

#: The record survives as a live, matchable grant, with corrections applied.
CORRECT = "CORRECT"
#: The record belongs to another engine — incentives, markets/labs/WIP.
RECLASSIFY = "RECLASSIFY"
#: History is retained; the record leaves live matching.
ARCHIVE = "ARCHIVE"
#: A generic parent retired in favour of named child programmes.
SPLIT = "SPLIT"
#: The programme exists but is not currently running.
SUSPEND = "SUSPEND"
#: The record is superseded by a replacement that must exist before cutover.
REPLACE = "REPLACE"
#: The freeze did not settle this row.
NEEDS_REVIEW = "NEEDS_REVIEW"

#: Tokens as they appear inside a compound decision string. Order matters only
#: for the multi-word tokens, which are matched before their own components so
#: ``NEEDS_REVIEW_NONMATCHABLE`` does not parse as ``NEEDS`` plus ``REVIEW``.
_TOKENS: tuple[tuple[str, str], ...] = (
    ("NEEDS_REVIEW_NONMATCHABLE", NEEDS_REVIEW),
    ("NEEDS_REVIEW", NEEDS_REVIEW),
    ("NEEDS_TERMS", NEEDS_REVIEW),
    ("CORRECT", CORRECT),
    ("RECLASSIFY", RECLASSIFY),
    ("ARCHIVE", ARCHIVE),
    ("SPLIT", SPLIT),
    ("SUSPEND", SUSPEND),
    ("REPLACE", REPLACE),
)

#: Actions after which the live row must no longer consume a paid match slot.
#: ``REPLACE`` is here because the replacement has to be verified present before
#: the original may be trusted, and this module cannot see whether it is.
_ENDS_LIVE_MATCHING = frozenset({RECLASSIFY, ARCHIVE, SPLIT, SUSPEND, REPLACE})

#: Verification states that support keeping a row in paid matching. Anything
#: else — partially verified, identity only, needs review — leaves the row
#: present but not paid-safe, which the report states separately from the
#: migration action itself.
_PAID_SAFE_VERIFICATION = frozenset({"VERIFIED_CURRENT", "STRUCTURAL_VERIFIED"})


def parse_decision(decision: str | None) -> tuple[frozenset[str], tuple[str, ...]]:
    """The actions a decision string names, and any token not recognised.

    Returns ``(actions, unrecognised)``. An empty decision yields no actions and
    no unrecognised tokens, which the caller reports as an absent decision rather
    than as a retention.
    """
    raw = (decision or "").strip().upper()
    if not raw:
        return frozenset(), ()

    remaining = raw
    actions: set[str] = set()
    for token, action in _TOKENS:
        if token in remaining:
            actions.add(action)
            remaining = remaining.replace(token, " ")

    leftover = tuple(part for part in remaining.replace("_", " ").split() if part)
    return frozenset(actions), leftover


@dataclass(frozen=True)
class LiveRowVerdict:
    """What the freeze says becomes of one currently live grant row."""

    live_id: str
    title: str | None
    actions: frozenset[str]
    verification_status: str | None
    #: False when this row must stop consuming paid recommendation slots, either
    #: because an action retires it or because nothing in the freeze retains it.
    stays_matchable: bool
    #: True only when it stays matchable AND its verification supports that. The
    #: two are separate: a retained row with a partially verified rule is still
    #: live, and still must not be sold as a confirmed opportunity.
    paid_safe: bool
    blocking_reasons: tuple[str, ...] = ()

    @property
    def is_blocking(self) -> bool:
        return bool(self.blocking_reasons)


@dataclass
class ReconciliationReport:
    """The dry run's findings. Never a migration, and never a write."""

    verdicts: list[LiveRowVerdict] = field(default_factory=list)
    #: Mapping rows naming a live ID that is not in the live table. Each is a
    #: mapping written against a row that has since moved or gone.
    mappings_without_a_live_row: list[str] = field(default_factory=list)
    #: Findings carried from the snapshot's own open questions.
    source_open_questions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def live_row_count(self) -> int:
        return len(self.verdicts)

    @property
    def blocking(self) -> list[LiveRowVerdict]:
        return [v for v in self.verdicts if v.is_blocking]

    @property
    def retained(self) -> list[LiveRowVerdict]:
        return [v for v in self.verdicts if v.stays_matchable]

    @property
    def paid_safe(self) -> list[LiveRowVerdict]:
        return [v for v in self.verdicts if v.paid_safe]

    @property
    def is_ready_to_import(self) -> bool:
        """Whether a human has settled every row the migration would touch.

        Deliberately conservative and deliberately not overridable here. An
        operator who disagrees resolves the finding in the source maps, not by
        passing a flag to the importer.
        """
        return (
            not self.blocking
            and not self.mappings_without_a_live_row
            and not any(self.source_open_questions.values())
        )


def reconcile(
    live_rows: Iterable[dict[str, Any]],
    legacy_mapping: Iterable[dict[str, Any]],
    *,
    source_open_questions: dict[str, list[str]] | None = None,
) -> ReconciliationReport:
    """Compare the live grant table against the frozen legacy ID mapping.

    ``live_rows`` are ``grant_opportunities`` records; only ``id`` and ``title``
    are read. ``legacy_mapping`` is ``legacy_id_mapping.csv`` as dicts.

    Every live row gets a verdict, including rows the mapping never names. That
    is the finding the migration most needs: the freeze maps 88 rows, the live
    table holds more, and the unnamed remainder would otherwise survive cutover
    untouched and unreviewed.
    """
    report = ReconciliationReport(
        source_open_questions=dict(source_open_questions or {})
    )

    by_id: dict[str, dict[str, Any]] = {}
    for row in legacy_mapping:
        existing = str(row.get("existing_id") or "").strip()
        if existing:
            by_id[existing] = row

    live_ids: set[str] = set()
    for row in live_rows:
        live_id = str(row.get("id") or "").strip()
        title = row.get("title")
        if not live_id:
            report.verdicts.append(
                LiveRowVerdict(
                    live_id="",
                    title=title,
                    actions=frozenset(),
                    verification_status=None,
                    stays_matchable=False,
                    paid_safe=False,
                    blocking_reasons=("Live row has no ID to map against.",),
                )
            )
            continue
        live_ids.add(live_id)
        report.verdicts.append(_verdict_for(live_id, title, by_id.get(live_id)))

    report.mappings_without_a_live_row = sorted(
        mapped_id for mapped_id in by_id if mapped_id not in live_ids
    )
    return report


def _verdict_for(
    live_id: str, title: Any, mapping: dict[str, Any] | None
) -> LiveRowVerdict:
    if mapping is None:
        # Silence is not retention. This row predates the freeze's review and
        # would otherwise carry on matching with nothing current behind it.
        return LiveRowVerdict(
            live_id=live_id,
            title=title,
            actions=frozenset(),
            verification_status=None,
            stays_matchable=False,
            paid_safe=False,
            blocking_reasons=(
                "No entry in the frozen legacy ID mapping — the freeze does not "
                "say what becomes of this row.",
            ),
        )

    actions, unrecognised = parse_decision(mapping.get("decision"))
    verification = (mapping.get("verification_status") or "").strip().upper() or None
    resolved = str(mapping.get("resolved_live_id") or "").strip()

    reasons: list[str] = []
    if not actions:
        reasons.append("Mapping carries no decision.")
    if unrecognised:
        reasons.append(
            "Mapping decision names actions this migration does not encode: "
            + ", ".join(unrecognised)
            + "."
        )
    if NEEDS_REVIEW in actions:
        reasons.append("Mapping decision is unresolved and needs human review.")
    if not resolved:
        reasons.append(
            "Mapping resolves to no live ID, so a legacy reference to this row "
            "would be left dangling."
        )
    elif resolved != live_id:
        # Not blocking on its own: a resolved ID that differs is how a SPLIT
        # parent points at its canonical child. It is recorded so the importer
        # carries the redirect rather than dropping the reference.
        reasons_note = None
        if SPLIT not in actions and REPLACE not in actions:
            reasons_note = (
                "Mapping resolves to a different live ID without a split or "
                "replace decision to explain the redirect."
            )
        if reasons_note:
            reasons.append(reasons_note)

    stays_matchable = bool(
        actions
        and NEEDS_REVIEW not in actions
        and not (actions & _ENDS_LIVE_MATCHING)
    )
    paid_safe = bool(
        stays_matchable and not reasons and verification in _PAID_SAFE_VERIFICATION
    )

    return LiveRowVerdict(
        live_id=live_id,
        title=title if title is not None else mapping.get("existing_title"),
        actions=actions,
        verification_status=verification,
        stays_matchable=stays_matchable,
        paid_safe=paid_safe,
        blocking_reasons=tuple(reasons),
    )
