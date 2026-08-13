"""Can THIS producer access this programme: the country constraint, asked once.

Every other dimension of eligibility had a gate. This one — the blunt question of
whether the company behind the production is allowed to claim at all — did not,
even though ``project_incentive``'s own docstring lists it first among the four
dimensions it exists to combine. It combined two.

What that meant in practice, and it is worth stating plainly because it is the
kind of error that costs a producer money rather than confusing them:

    Canada's CPTC is restricted to Canadian-controlled corporations and states no
    SPV route. Asked about it on behalf of a London-based production, the engine
    returned ``eligible``, ``canAffectRanking: True``, ``canBeRecommended: True``
    and no reasons at all. The rate then fed the ranking that recommended Canada.

The one place nationality WAS compared, ``builder._apply_eligibility``, could
never fire either: it reads ``datasets["_producer_country"]``, which is populated
from a ``producer_country`` request field that no client has ever sent. So it took
its "no producer country" branch every time and emitted a generic note advising the
reader to verify the jurisdiction themselves. Meanwhile intake had been collecting
Production Country all along.

Three verdicts, same shape as the other gates so the statuses combine by taking
the worst:

    QUALIFIES     the producer's country satisfies the requirement outright
    ROUTED        it does not, but the programme states a route (local SPV, or a
                  co-production treaty) that this production could take
    EXCLUDED      it does not, and the programme states no route
    UNKNOWN       the programme states a requirement and we do not hold the
                  producer's country, so it cannot be tested

UNKNOWN IS NOT QUALIFIES. A requirement we could not test is the reason to ask the
question in the report, not a reason to answer it favourably.
"""
from __future__ import annotations

import json as _json
from typing import Any

from app.core.territories import EU_MEMBER_ISOS, producer_iso

QUALIFIES = "qualifies"
ROUTED = "routed"
EXCLUDED = "excluded"
UNKNOWN = "unknown"

LABELS = {
    QUALIFIES: "Producer qualifies directly",
    ROUTED: "Qualifies via a structure not yet in place",
    EXCLUDED: "Not open to this producer",
    UNKNOWN: "Producer eligibility untested",
}

# Worst-wins ordering, matching programme_eligibility and format_eligibility so a
# combined status is only ever as strong as its weakest dimension.
_RANK = {EXCLUDED: 0, UNKNOWN: 1, ROUTED: 2, QUALIFIES: 3}


def verdict_rank(verdict: str | None) -> int:
    return _RANK.get(verdict or UNKNOWN, 1)


def _requirements(row: dict) -> list[str]:
    """The programme's stated nationality requirement, as ISO codes.

    Stored as a JSON array on the row, sometimes already decoded by the driver.
    An unparseable value yields no requirement rather than a crash — but see
    ``evaluate_producer_eligibility``, which does not read that as "open to all".
    """
    raw = row.get("nationality_requirements")
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [str(code).strip().upper() for code in raw if str(code).strip()]


def _satisfies(producer: str, required: list[str]) -> bool:
    """Whether *producer* (ISO) meets any one of the *required* codes.

    ``EU`` is a membership test rather than a country match, so a French producer
    satisfies Malta's ``["MT","EU"]`` without France appearing in the list.
    """
    if producer in required:
        return True
    return "EU" in required and producer in EU_MEMBER_ISOS


def evaluate_producer_eligibility(
    row: dict,
    project: dict | None = None,
) -> dict[str, Any]:
    """Whether the production company behind *project* can claim under *row*.

    ``project`` supplies ``producer_iso`` (or ``producer_country`` / ``country``,
    resolved the same way) and ``co_production_intent``.
    """
    project = project or {}
    required = _requirements(row)

    if not required:
        # The overwhelming majority of rows. No stated restriction is a real
        # answer, not an untested one: there is nothing here to fail.
        return _result(QUALIFIES, None, required, None)

    producer = (
        project.get("producer_iso")
        or producer_iso(project.get("producer_country"))
        or producer_iso(project.get("country"))
    )
    stated = ", ".join(required)

    if not producer:
        return _result(
            UNKNOWN,
            f"This programme is restricted to producers established in {stated}. "
            f"No production-company jurisdiction is held for this project, so "
            f"whether it qualifies could not be tested.",
            required,
            None,
        )

    if _satisfies(producer, required):
        return _result(
            QUALIFIES,
            f"A {producer} entity meets this programme's {stated} requirement "
            f"directly.",
            required,
            producer,
        )

    # Does not qualify directly. The programme may still state a route in.
    spv_ok = row.get("spv_eligible") is True
    co_prod_ok = row.get("co_production_eligible") is True
    # "no" is a decision, and it closes the treaty route. "undecided" leaves it
    # open, which is the honest reading of a producer who has not chosen yet.
    intent = (project.get("co_production_intent") or "").strip().lower()
    treaty_open = co_prod_ok and intent != "no"

    routes: list[str] = []
    if spv_ok:
        routes.append("establishing a qualifying local entity (SPV)")
    if treaty_open:
        routes.append("qualifying through an official co-production treaty")

    if routes:
        detail = (
            f"This programme is restricted to producers established in {stated}, "
            f"and this production is a {producer} entity. It states a route in: "
            f"{'; or '.join(routes)}. The rebate is only claimable once that "
            f"structure exists, so it is shown as contingent rather than counted."
        )
        return _result(ROUTED, detail, required, producer, routes=routes)

    closed = ""
    if co_prod_ok and intent == "no":
        closed = (
            " The programme allows an official co-production route, which you "
            "ruled out at intake; reopening it would change this answer."
        )
    return _result(
        EXCLUDED,
        f"This programme is restricted to producers established in {stated}, and "
        f"this production is a {producer} entity. It states no local-entity or "
        f"co-production route, so it is not available to this production as "
        f"structured.{closed}",
        required,
        producer,
    )


def _result(
    verdict: str,
    explanation: str | None,
    required: list[str],
    producer: str | None,
    routes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "label": LABELS[verdict],
        "qualifies": verdict == QUALIFIES,
        "explanation": explanation,
        "requiredNationalities": required,
        "producerIso": producer,
        "routes": routes or [],
    }


# Legacy status vocabulary, kept because readiness, the estimate cards and stored
# reports all speak it. Mapped in one place so the two vocabularies cannot drift.
_LEGACY_STATUS = {
    QUALIFIES: "qualified",
    EXCLUDED: "ineligible",
    UNKNOWN: "unknown",
}


def legacy_status(result: dict) -> str:
    """The ``eligibilityStatus`` value the rest of the report already reads."""
    verdict = result.get("verdict")
    if verdict == ROUTED:
        # Which route it is matters downstream: readiness treats both as
        # contingent, but the producer reads a different next action.
        routes = result.get("routes") or []
        if any("SPV" in r for r in routes):
            return "requires_spv"
        return "requires_co_production"
    return _LEGACY_STATUS.get(verdict, "unknown")
