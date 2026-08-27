"""Translation between our vocabulary and the v2 reference implementation's.

The co-production reference package and the ingestion specification disagree
about three vocabularies. Neither is wrong; they were written by different hands
at different times, and both are in the handoff.

    concept       reference package        ingestion specification
    provenance    confirmed                known
    status        lowercase, 5 values      uppercase, ESTIMATED plus others
    status set    5                        8, adding blocked/suspended/no-programme

We follow the ingestion specification, because it is the authority for ingestion
and states its values explicitly, and because the QA matrix requires blocked and
suspended states that the reference omits. This module makes the reference's
contracts, JSON schemas and TypeScript types interoperable anyway, so its code can
be ported without a silent rename.

Every mapping raises on an unknown value. A vocabulary translation that guesses is
worse than one that stops, because the guess propagates into a status a report
prints.
"""
from __future__ import annotations

from app.modules.incentives.v2_contracts import (
    CALCULATION_STATUSES,
    INPUT_STATUSES,
)


class VocabularyError(ValueError):
    """A value has no counterpart in the other vocabulary."""


#: Reference provenance -> ours. The reference calls a supplied, non-assumed
#: figure "confirmed"; the ingestion contract calls it "known". They mean the same
#: thing, and neither implies the awarding authority has confirmed anything.
PROVENANCE_FROM_REFERENCE: dict[str, str] = {
    "confirmed": "known",
    "planning_assumption": "planning_assumption",
    "unknown": "unknown",
}
PROVENANCE_TO_REFERENCE: dict[str, str] = {
    ours: theirs for theirs, ours in PROVENANCE_FROM_REFERENCE.items()
}

#: Reference status -> ours. The reference's "calculated" is our "ESTIMATED":
#: every required input present and the formula run.
STATUS_FROM_REFERENCE: dict[str, str] = {
    "calculated": "ESTIMATED",
    "conditional": "CONDITIONAL",
    "requires_cost_breakdown": "REQUIRES_COST_BREAKDOWN",
    "not_eligible": "NOT_ELIGIBLE",
    "programme_unverified": "PROGRAMME_UNVERIFIED",
}

#: Ours -> reference. The three statuses the reference has no member for all
#: collapse to programme_unverified, which is the closest honest equivalent: no
#: figure, excluded from ranking. The collapse is lossy in that direction only,
#: and is why we do not adopt the reference's shorter set.
STATUS_TO_REFERENCE: dict[str, str] = {
    "ESTIMATED": "calculated",
    "CONDITIONAL": "conditional",
    "REQUIRES_COST_BREAKDOWN": "requires_cost_breakdown",
    "NOT_ELIGIBLE": "not_eligible",
    "PROGRAMME_UNVERIFIED": "programme_unverified",
    "BLOCKED": "programme_unverified",
    "SUSPENDED": "programme_unverified",
    "NO_PROGRAMME": "programme_unverified",
}

#: Statuses that lose information when exported to the reference vocabulary.
#: Callers exporting these should carry the original alongside, so a blocked
#: programme is not later re-imported as merely unverified.
LOSSY_ON_EXPORT: frozenset[str] = frozenset({"BLOCKED", "SUSPENDED", "NO_PROGRAMME"})

#: Narrative claim permitted for each of our statuses, from the reference's
#: ``allowed_claim`` contract. This is what stops the narrative layer describing
#: a conditional figure as a calculated one.
ALLOWED_CLAIM: dict[str, str] = {
    "ESTIMATED": "estimated_calculated_amount",
    "CONDITIONAL": "potential_modelled_amount",
    "REQUIRES_COST_BREAKDOWN": "no_project_amount",
    "NOT_ELIGIBLE": "no_project_amount",
    "PROGRAMME_UNVERIFIED": "no_project_amount",
    "BLOCKED": "no_project_amount",
    "SUSPENDED": "no_project_amount",
    "NO_PROGRAMME": "no_project_amount",
}


def provenance_from_reference(value: str) -> str:
    try:
        return PROVENANCE_FROM_REFERENCE[str(value).strip().lower()]
    except KeyError:
        raise VocabularyError(
            f"{value!r} is not a reference provenance value. Expected one of: "
            + ", ".join(sorted(PROVENANCE_FROM_REFERENCE))
        ) from None


def provenance_to_reference(value: str) -> str:
    try:
        return PROVENANCE_TO_REFERENCE[str(value).strip().lower()]
    except KeyError:
        raise VocabularyError(
            f"{value!r} is not an input status. Expected one of: "
            + ", ".join(sorted(INPUT_STATUSES))
        ) from None


def status_from_reference(value: str) -> str:
    try:
        return STATUS_FROM_REFERENCE[str(value).strip().lower()]
    except KeyError:
        raise VocabularyError(
            f"{value!r} is not a reference calculation status. Expected one of: "
            + ", ".join(sorted(STATUS_FROM_REFERENCE))
        ) from None


def status_to_reference(value: str) -> str:
    key = str(value).strip().upper()
    try:
        return STATUS_TO_REFERENCE[key]
    except KeyError:
        raise VocabularyError(
            f"{value!r} is not a calculation status. Expected one of: "
            + ", ".join(sorted(CALCULATION_STATUSES))
        ) from None


def allowed_claim(status: str) -> str:
    """What the narrative layer may assert for this status."""
    key = str(status).strip().upper()
    try:
        return ALLOWED_CLAIM[key]
    except KeyError:
        raise VocabularyError(
            f"{status!r} has no permitted narrative claim. Every status must "
            f"declare one, or the narrative layer has no constraint to obey."
        ) from None
