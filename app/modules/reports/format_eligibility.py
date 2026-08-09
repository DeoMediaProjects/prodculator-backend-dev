"""Per-programme format eligibility for incentive programmes.

The problem this replaces
------------------------
``incentive_programs.applicable_formats`` is a JSON array on a Text column, and
``best_incentive`` read it with one rule: NULL means "applies to all formats". Since
the column is NULL on every row, every programme was treated as accepting every
format. A short film was therefore quoted feature-scale rebates from programmes
that may exclude short films entirely, and nothing in the output said so.

Silence is the dangerous part. An unverified programme that wins on rate looks
identical to a verified one, so the headline figure a producer takes to a financier
carries no signal that its eligibility was never established.

The model
---------
Eligibility is a property of a PROGRAMME, not a territory: two programmes in the
same territory routinely differ, and one of them may accept short animation while
the other is feature-only. So the status lives on the programme row:

``verified``     ``applicable_formats`` is a complete whitelist. A format in the
                 list is eligible; a format absent from it is INELIGIBLE.
``conditional``  eligibility depends on something beyond format alone (runtime, a
                 theatrical commitment, local spend). ``format_conditions``
                 carries the human-readable rule.
``unknown``      not established. Never treated as eligible.

Unknown is the default for every existing row, because the alternative defaults to
a financial claim nobody checked.
"""
from __future__ import annotations

import json as _json
from typing import Any

from app.core.formats import canonical_format

# ── Statuses ─────────────────────────────────────────────────────────────────

STATUS_VERIFIED = "verified"
STATUS_CONDITIONAL = "conditional"
STATUS_UNKNOWN = "unknown"

VALID_STATUSES = frozenset({STATUS_VERIFIED, STATUS_CONDITIONAL, STATUS_UNKNOWN})

# ── Verdicts ─────────────────────────────────────────────────────────────────
# What the pipeline is allowed to do with a programme for this production.

#: In the verified whitelist. Quote the rebate normally.
ELIGIBLE = "eligible"
#: Verified whitelist that excludes this format. Not an available incentive.
INELIGIBLE = "ineligible"
#: A condition applies and could not be settled from the project data. Show the
#: programme and the condition; do not present the rebate as confirmed.
NEEDS_CONFIRMATION = "needs_confirmation"
#: Eligibility never established. Show the programme; do not present the rebate as
#: confirmed.
UNVERIFIED = "unverified"

#: Verdicts whose rebate figure must not be presented as a confirmed amount.
UNCONFIRMED_VERDICTS = frozenset({NEEDS_CONFIRMATION, UNVERIFIED})

#: Ranked worst to best, so a sort can prefer a confirmed programme over an
#: unconfirmed one regardless of which quotes the larger rebate.
_VERDICT_RANK: dict[str, int] = {
    INELIGIBLE: 0,
    UNVERIFIED: 1,
    NEEDS_CONFIRMATION: 2,
    ELIGIBLE: 3,
}

LABELS: dict[str, str] = {
    ELIGIBLE: "Eligible",
    INELIGIBLE: "Not eligible for this format",
    NEEDS_CONFIRMATION: "Conditional",
    UNVERIFIED: "Format eligibility unverified",
}


def verdict_rank(verdict: str) -> int:
    """Higher is more dependable. Used to order candidates."""
    return _VERDICT_RANK.get(verdict, 0)


def is_confirmed(verdict: str) -> bool:
    return verdict == ELIGIBLE


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_applicable_formats(value: Any) -> list[str] | None:
    """Canonical format tokens from the stored column, or None if it states nothing.

    The column holds JSON on a Text column and historically stored display labels
    ("Feature Film"), so every value is canonicalised on read. None and an empty
    list both mean "states nothing", which is not the same as "states all".
    """
    if value is None:
        return None
    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            raw = _json.loads(text)
        except (ValueError, TypeError):
            # A bare label rather than JSON. Accept it rather than discard a
            # curator's intent over punctuation.
            raw = [text]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return None
    out: list[str] = []
    for item in raw:
        token = canonical_format(item)
        if token and token not in out:
            out.append(token)
    return out or None


def eligibility_status(row: dict) -> str:
    """The programme's declared status, defaulting to unknown.

    A row that declares ``verified`` without a whitelist has declared nothing
    usable, so it degrades to unknown rather than silently excluding every format.
    """
    raw = (row.get("format_eligibility_status") or "").strip().lower()
    status = raw if raw in VALID_STATUSES else STATUS_UNKNOWN
    if status == STATUS_VERIFIED and parse_applicable_formats(row.get("applicable_formats")) is None:
        return STATUS_UNKNOWN
    return status


# ── Conditions ───────────────────────────────────────────────────────────────
# Conditions are prose written by a curator. A machine may only settle one when the
# project data answers it unambiguously; anything else is surfaced to the producer
# rather than guessed. Guessing here would mean inventing a tax rule.

_RUNTIME_PATTERNS = (
    # "minimum runtime 40 minutes", "at least 40 minutes", "40 minutes or more".
    # The optional noun absorbs the way programmes actually phrase this: "minimum
    # runtime", "minimum length", "minimum duration of". Without it, only the bare
    # "minimum 40 minutes" parsed and every other phrasing fell through to needing
    # confirmation even when the rule was machine-readable.
    r"(?:minimum|at least|no less than|over|from)\s*"
    r"(?:(?:running\s+)?(?:runtime|run\s+time|length|duration)\s*(?:of\s*)?)?"
    r"(\d{1,3})\s*(?:minutes|mins|min)\b",
    r"(\d{1,3})\s*(?:minutes|mins|min)\b\s*(?:or more|minimum|and over)",
)


def _stated_minimum_runtime(conditions: str) -> int | None:
    import re

    for pattern in _RUNTIME_PATTERNS:
        match = re.search(pattern, conditions, re.I)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def evaluate_condition(
    conditions: str | None,
    project: dict | None,
) -> tuple[bool | None, str]:
    """Settle a ``conditional`` programme against the project where possible.

    Returns ``(True | False | None, explanation)``. None means the project does not
    carry what the condition asks about, which is a request for confirmation and
    never an assumption of eligibility.
    """
    text = (conditions or "").strip()
    if not text:
        return None, "This programme has format conditions that are not recorded in detail."

    data = project or {}

    minimum = _stated_minimum_runtime(text)
    if minimum is not None:
        runtime = data.get("runtime_minutes")
        try:
            runtime = float(runtime) if runtime is not None else None
        except (TypeError, ValueError):
            runtime = None
        if runtime is None:
            return None, (
                f"{text} Your project's runtime is not recorded, so this cannot be "
                f"checked automatically."
            )
        if runtime >= minimum:
            return True, f"{text} Your runtime of {runtime:g} minutes meets the {minimum}-minute minimum."
        return False, (
            f"{text} Your runtime of {runtime:g} minutes is below the {minimum}-minute minimum."
        )

    return None, text


# ── The decision ─────────────────────────────────────────────────────────────

def evaluate_format_eligibility(
    row: dict,
    production_format: str | None,
    project: dict | None = None,
) -> dict[str, Any]:
    """Whether *row* may be quoted as an incentive for this production's format.

    Returns a dict carrying the verdict, the status it came from, the whitelist,
    a display label, an explanation, and the provenance recorded on the row, so
    every surface renders the same decision from the same object.
    """
    status = eligibility_status(row)
    allowed = parse_applicable_formats(row.get("applicable_formats"))
    wanted = canonical_format(production_format)

    provenance = {
        "status": status,
        "applicableFormats": allowed,
        "conditions": row.get("format_conditions") or None,
        "sourceUrl": row.get("format_source_url") or None,
        "verifiedAt": (
            str(row["format_verified_at"])[:10] if row.get("format_verified_at") else None
        ),
    }

    def result(verdict: str, explanation: str) -> dict[str, Any]:
        return {
            **provenance,
            "verdict": verdict,
            "label": LABELS[verdict],
            "confirmed": is_confirmed(verdict),
            "explanation": explanation,
        }

    # No declared format means no format-based judgement to make. The programme is
    # neither confirmed nor excluded on these grounds.
    if not wanted:
        if status == STATUS_UNKNOWN:
            return result(UNVERIFIED, "This programme's format eligibility has not been verified.")
        return result(
            NEEDS_CONFIRMATION,
            "No production format was supplied, so format eligibility could not be checked.",
        )

    if status == STATUS_VERIFIED:
        assert allowed is not None  # eligibility_status() guarantees this
        if wanted in allowed:
            return result(ELIGIBLE, f"This programme accepts {_display(wanted)} projects.")
        return result(
            INELIGIBLE,
            f"This programme does not accept {_display(wanted)} projects.",
        )

    if status == STATUS_CONDITIONAL:
        # A conditional row may still carry a whitelist. If the format is not on
        # it, the condition cannot rescue it.
        if allowed is not None and wanted not in allowed:
            return result(
                INELIGIBLE,
                f"This programme does not accept {_display(wanted)} projects.",
            )
        settled, explanation = evaluate_condition(row.get("format_conditions"), project)
        if settled is True:
            return result(ELIGIBLE, explanation)
        if settled is False:
            return result(INELIGIBLE, explanation)
        return result(NEEDS_CONFIRMATION, explanation)

    # Unknown status. An unverified whitelist still constrains: someone recorded a
    # scope for this programme, and the two directions are not symmetric. Excluding a
    # format the list omits understates the rebate, which is recoverable. Including
    # one it omits overstates it, which is what this module exists to prevent. So the
    # list is honoured as an exclusion but never promoted to a confirmation: a format
    # ON an unverified list is still only unverified.
    if allowed is not None and wanted not in allowed:
        return result(
            INELIGIBLE,
            f"This programme's recorded scope does not include {_display(wanted)} projects.",
        )

    return result(
        UNVERIFIED,
        f"Whether this programme accepts {_display(wanted)} projects has not been verified.",
    )


def _display(token: str) -> str:
    from app.core.formats import FORMAT_DISPLAY

    return FORMAT_DISPLAY.get(token, token.replace("_", " ")).lower()


def any_unverified_for_format(
    rows: list[dict],
    production_format: str | None,
    project: dict | None = None,
) -> bool:
    """True when at least one row cannot confirm eligibility for this format.

    Drives the blanket caveat, so it retires itself once every programme carries
    verified or settled data rather than needing a code change.

    *project* is passed through so a ``conditional`` programme whose rule the project
    facts already settle counts as answered. Without it a conditional programme would
    hold the caveat open permanently, no matter how complete the research became.
    """
    for row in rows or []:
        verdict = evaluate_format_eligibility(
            row, production_format, project
        ).get("verdict")
        if verdict in UNCONFIRMED_VERDICTS:
            return True
    return False
