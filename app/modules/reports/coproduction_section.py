"""The co-production structure section.

WHY THIS IS NOT A RANKING
-------------------------
Every other territory section in this report ranks: it asks which territory is
the better place to shoot and orders them. A co-production is the opposite
question. The partners are not competing for the production, they are each
holding a share of it, and ordering them "best first" states something false
about the structure. France is not beating Germany when both are financing the
same film.

So this section reconciles instead of ranking. It reports whether the shares add
up to the budget, what each partner brings, and where the structure is exposed.

WHY COMBINED SUPPORT IS NEVER A TOTAL
-------------------------------------
Adding each partner's incentive gives a number that looks like available finance
and is not. Cumulation ceilings, treaty caps and the intensity limits of public
support all bite on the total rather than on each part, and none of that has been
assessed here. So the parts are listed and their sum is deliberately withheld,
with the reason stated rather than left as an omission a reader might take for an
oversight.
"""
from __future__ import annotations

from typing import Any

from app.modules.incentives.v2_contracts import (
    MULTILATERAL_MINIMUM_PARTNERS,
    reconcile_allocations,
)

#: How the reconciliation reads on the page. Under-allocation is ordinary while a
#: structure is being assembled; over-allocation is a structure that cannot be
#: financed as described, and the two must not share a wording.
RECONCILIATION_COPY: dict[str, str] = {
    "reconciled": "Partner allocations account for the whole budget.",
    "under_allocated": (
        "Part of the budget is not yet allocated to a partner or recorded as "
        "spend earning nothing locally. Ordinary while a structure is being "
        "assembled."
    ),
    "over_allocated": (
        "Partner allocations exceed the production budget. A structure cannot "
        "spend more than it is financed for, so one of the figures needs "
        "revisiting."
    ),
    "not_assessable": (
        "Not every partner has an allocation recorded, so the shares cannot be "
        "checked against the budget. A missing allocation is treated as unknown "
        "rather than as zero, which would report a shortfall that may not exist."
    ),
}

_STATUS_LABEL: dict[str, str] = {
    "reconciled": "Reconciled",
    "under_allocated": "Under allocated",
    "over_allocated": "Over allocated",
    "not_assessable": "Not assessable",
}


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _partner(
    scenario: dict[str, Any],
    estimate: dict[str, Any] | None,
) -> dict[str, Any]:
    """One partner row: what they hold, and what their territory can contribute.

    The incentive figure is copied from the estimate rather than recalculated, so
    this section and the incentive section cannot state different numbers for the
    same programme.
    """
    return {
        "territory": scenario.get("territory"),
        "territoryId": scenario.get("territory_id"),
        "subdivisionId": scenario.get("subdivision_id"),
        "allocatedSpend": _to_float(scenario.get("scenario_spend")),
        "currency": scenario.get("scenario_currency"),
        "participationPercent": _to_float(scenario.get("participation_percent")),
        #: candidate or confirmed. Neither implies competent-authority approval,
        #: which is a separate matter the treaty layer tracks.
        "partnerStatus": scenario.get("partner_status") or "candidate",
        "programme": (estimate or {}).get("program"),
        #: Read straight from the estimate, including its status, so a partner
        #: whose figure is withheld is shown as withheld here too rather than as
        #: a blank that reads like nil.
        "incentive": (estimate or {}).get("confirmedIncentive"),
        "calculationStatus": (estimate or {}).get("calculationStatus"),
        "calculationStatusLabel": (estimate or {}).get("calculationStatusLabel"),
    }


def build_coproduction_structure(
    *,
    mode: str,
    scenarios: dict[str, dict[str, Any]],
    estimates: list[dict[str, Any]],
    budget: float | None,
    currency: str | None,
    unallocated_spend: Any = None,
    route: str | None = None,
    supranational_interest: str | None = None,
) -> dict[str, Any] | None:
    """The section, or None when the production is not a co-production.

    Returns None for a comparison, rather than an empty structure. A section that
    renders with nothing in it invites the reader to wonder what went missing.
    """
    if mode != "coproduction" or not scenarios:
        return None

    by_territory = {
        e.get("territory"): e for e in estimates if e.get("territory")
    }
    partners = [
        _partner(scenario, by_territory.get(scenario.get("territory")))
        for scenario in scenarios.values()
    ]
    # Ordered by allocation size, largest first, purely so the majority partner
    # reads first. Explicitly NOT a ranking: see the module docstring. An unknown
    # allocation sorts last because it cannot be placed, not because it is small.
    partners.sort(
        key=lambda p: (p["allocatedSpend"] is None, -(p["allocatedSpend"] or 0.0)),
    )

    unallocated = _to_float(unallocated_spend)
    status, remaining = reconcile_allocations(
        budget,
        [p["allocatedSpend"] for p in partners],
        unallocated,
    )

    notes: list[str] = []
    confirmed = [p for p in partners if p["partnerStatus"] == "confirmed"]
    if len(confirmed) < len(partners):
        notes.append(
            f"{len(partners) - len(confirmed)} of {len(partners)} partners are "
            f"candidates rather than confirmed, so the structure is provisional."
        )
    if route and "Council of Europe" in route and len(partners) < (
        MULTILATERAL_MINIMUM_PARTNERS
    ):
        # Stated rather than enforced. The producer may be mid-way through
        # assembling the structure, and refusing the report would not help them.
        notes.append(
            f"The Council of Europe route requires at least "
            f"{MULTILATERAL_MINIMUM_PARTNERS} co-producers; "
            f"{len(partners)} are recorded."
        )
    if unallocated:
        notes.append(
            "Spend recorded as earning nothing in any partner territory does not "
            "attract an incentive anywhere, so it raises the effective cost of "
            "the structure."
        )

    return {
        "mode": mode,
        "partners": partners,
        "partnerCount": len(partners),
        "currency": currency,
        "budget": budget,
        "unallocatedSpend": unallocated,
        "reconciliationStatus": status,
        "reconciliationLabel": _STATUS_LABEL[status],
        "reconciliationExplanation": RECONCILIATION_COPY[status],
        #: Signed: positive is under-allocated, negative is over-allocated. None
        #: when the shares cannot be assessed at all.
        "reconciliationRemaining": remaining,
        "route": route,
        "supranationalInterest": supranational_interest,
        "structureNotes": notes,
        #: Read by every surface so none of them decides for itself whether to add
        #: the partner incentives up. See the module docstring.
        "partnersAreRanked": False,
        "combinedIncentiveWithheld": True,
        "combinedIncentiveReason": (
            "Partner incentives are listed separately and deliberately not summed. "
            "Cumulation ceilings, treaty caps and public-support intensity limits "
            "apply to the combined total rather than to each part, and none has "
            "been assessed for this structure. A sum here would read as available "
            "finance."
        ),
    }
