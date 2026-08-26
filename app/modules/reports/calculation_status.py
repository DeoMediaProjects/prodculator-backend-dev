"""One canonical calculation status per programme, for the report contract.

WHY THIS IS ONE FUNCTION AND NOT A FIELD PER QUESTION
-----------------------------------------------------
An estimate already carries ``incentiveIsConfirmed``, ``rebateIsConfirmed``,
``incentiveEligibilityStatus``, ``programmeEligibility.available`` and
``bankabilityLabel``. Each answers a real question, and between them a reader has
to work out whether the figure in front of them is a number this production can
rely on. They have disagreed before, which is what the cross-section validator
exists to catch.

So this module adds no new judgement. It reads the answers already resolved and
states the single conclusion the v2 contract defines, which is what a reader
actually needs: may this figure be relied on, and if not, what would change it.

TWO GATES, NOT ONE
------------------
The v2 specification is explicit that calculation verification is a separate
gate from source verification, and says so twice because it expects the mistake.
This module keeps them separate too:

``calculationStatus``
    What the engine can say from the project facts and the statutory inputs
    held. ESTIMATED, CONDITIONAL, REQUIRES_COST_BREAKDOWN, NOT_ELIGIBLE,
    SUSPENDED or NO_PROGRAMME.

``calculationVerification``
    Whether an administrator has approved this programme's formula for use:
    ready, conditional or blocked. Independent of everything above. A programme
    can hold every input it needs and still be unapproved, and a programme can
    be approved and still lack the inputs.

Collapsing the two would mean either presenting an unapproved formula's output
as final, or reporting "blocked" at a producer who would read it as a fact about
their project rather than about our verification queue.
"""
from __future__ import annotations

from typing import Any

from app.modules.incentives.v2_contracts import (
    CALCULATION_STATUSES,
    CALCULATION_READY,
    NUMERIC_STATUSES,
    missing_required_inputs,
)
from app.modules.reports.helpers import (
    NON_ENTITLEMENT_ENGINES,
    mechanism_no_figure_reason,
)

#: Reader-facing label per status. Written for a producer rather than an
#: engineer: the status name is a contract term, the label is what appears on the
#: page, and neither is derived from the other by string munging.
STATUS_LABELS: dict[str, str] = {
    "ESTIMATED": "Calculated",
    "CONDITIONAL": "Conditional",
    "REQUIRES_COST_BREAKDOWN": "Needs a cost breakdown",
    "NOT_ELIGIBLE": "Not available to this production",
    "PROGRAMME_UNVERIFIED": "Rules not confirmed",
    "BLOCKED": "Awaiting internal approval",
    "SUSPENDED": "Programme not running",
    "NO_PROGRAMME": "No programme recorded",
}

VERIFICATION_LABELS: dict[str, str] = {
    "ready": "Formula approved",
    "conditional": "Formula approved with conditions",
    "blocked": "Formula awaiting approval",
}

#: What the reader would have to do, or wait for, to move off this status. Absent
#: for the two terminal answers, where there is no next step to offer.
STATUS_NEXT_STEP: dict[str, str] = {
    "CONDITIONAL": (
        "Confirm the assumptions listed against this programme to move from a "
        "scenario to a calculation."
    ),
    "REQUIRES_COST_BREAKDOWN": (
        "Supply the statutory cost figures this programme calculates from. "
        "Until then no amount is shown, because one would have to be invented."
    ),
    "PROGRAMME_UNVERIFIED": (
        "We are resolving conflicting or incomplete official sources for this "
        "programme."
    ),
    "SUSPENDED": (
        "The territory remains viable for location, crew and currency reasons. "
        "The incentive returns to the analysis if the programme reopens."
    ),
}


def _is_suspended(db_row: dict[str, Any]) -> bool:
    status = str(db_row.get("status") or "").strip().lower()
    return status in {"suspended", "paused", "closed", "expired", "inactive"}


def _engine_of(db_row: dict[str, Any]) -> str:
    return str(db_row.get("qs_engine_type") or "").strip().upper()


def _supplied_inputs(scenario: dict[str, Any] | None) -> dict[str, Any]:
    """Statutory amounts the producer supplied for this territory.

    Keyed by canonical input key. An absent key and an explicit null both mean
    unknown; zero means the producer told us it is nil. Preserving that split is
    the whole point, so nothing here defaults a missing key to anything.
    """
    if not scenario:
        return {}
    inputs = scenario.get("calculation_inputs") or scenario.get("calculationInputs")
    if not inputs:
        return {}
    supplied: dict[str, Any] = {}
    for entry in inputs:
        key = entry.get("input_key") or entry.get("inputKey")
        if key:
            supplied[key] = entry.get("amount")
    return supplied


def _provenance(scenario: dict[str, Any] | None) -> dict[str, str]:
    if not scenario:
        return {}
    inputs = scenario.get("calculation_inputs") or scenario.get("calculationInputs") or []
    provenance: dict[str, str] = {}
    for entry in inputs:
        key = entry.get("input_key") or entry.get("inputKey")
        status = entry.get("input_status") or entry.get("inputStatus")
        if key and status:
            provenance[key] = str(status)
    return provenance


def resolve_calculation_status(
    est: dict[str, Any],
    db_row: dict[str, Any],
    *,
    scenario: dict[str, Any] | None = None,
    declared_inputs: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """The status of this programme's figure, and whether it may carry one.

    Precedence runs from facts about the programme's existence down to facts
    about our own inputs, because that is the order in which an answer stops
    being about the producer's project:

    1. ``NO_PROGRAMME``  there is nothing to calculate.
    2. ``SUSPENDED``     the programme is not running, so nothing can be claimed.
    3. ``NOT_ELIGIBLE``  a researched project fact fails a mandatory rule.
    4. ``REQUIRES_COST_BREAKDOWN`` the formula needs a statutory figure we do
       not hold. Below eligibility on purpose: telling a producer to prepare a
       cost breakdown for a programme their project cannot use wastes their time.
    5. ``CONDITIONAL``   a figure can be shown but rests on an assumption, an
       approval, or a mechanism that is not an entitlement.
    6. ``ESTIMATED``     every input the deterministic formula needs is present.

    ``PROGRAMME_UNVERIFIED`` and ``BLOCKED`` are deliberately absent. Both are
    facts about our verification queue rather than about the production, and both
    are reported through ``calculationVerification`` instead. See the module
    docstring.
    """
    engine = _engine_of(db_row)
    reasons: list[str] = []

    def result(status: str, reason: str | None = None) -> dict[str, Any]:
        if reason:
            reasons.append(reason)
        spec = CALCULATION_STATUSES[status]
        verification = str(
            db_row.get("calculation_verification_status") or "blocked"
        ).strip().lower()
        return {
            "calculationStatus": status,
            "calculationStatusLabel": STATUS_LABELS[status],
            "calculationStatusMeaning": spec["meaning"],
            "calculationStatusReasons": reasons,
            "calculationStatusNextStep": STATUS_NEXT_STEP.get(status),
            #: Whether this status permits a figure at all. Consumers must read
            #: this rather than testing for the presence of an amount: an
            #: illustrative figure and a relied-upon one look identical.
            "calculationCarriesFigure": status in NUMERIC_STATUSES,
            "calculationInRanking": spec["in_ranking"],
            #: The separate governance gate. Never folded into the status above.
            "calculationVerification": verification,
            "calculationVerificationLabel": VERIFICATION_LABELS.get(
                verification, VERIFICATION_LABELS["blocked"],
            ),
            "calculationIsApproved": verification == CALCULATION_READY,
        }

    # 1. Nothing to calculate.
    if not db_row or engine == "NO_PROGRAMME" or not db_row.get("program"):
        return result(
            "NO_PROGRAMME",
            "No claimable incentive programme is recorded for this territory.",
        )

    # 2. Not running. Checked before eligibility because a closed programme
    #    cannot be failed on eligibility grounds; there is nothing to qualify for.
    if _is_suspended(db_row):
        return result(
            "SUSPENDED",
            f"{db_row.get('program')} is not currently accepting claims.",
        )

    # 3. A researched project fact fails a mandatory rule. Read from the already
    #    resolved verdicts rather than re-derived, because a second opinion here
    #    is how the report came to contradict itself.
    availability = est.get("programmeEligibility") or {}
    if availability.get("available") is False:
        for reason in availability.get("reasons") or []:
            text = reason.get("detail") if isinstance(reason, dict) else reason
            if text:
                reasons.append(str(text))
        return result("NOT_ELIGIBLE")

    if est.get("incentiveEligibilityStatus") == "ineligible":
        for reason in est.get("incentiveEligibilityReasons") or []:
            reasons.append(str(reason))
        return result(
            "NOT_ELIGIBLE",
            None if reasons else "This production does not meet a mandatory rule.",
        )

    # 4. A mechanism that is not an entitlement. Its own status, before inputs,
    #    because no quantity of supplied cost detail turns a competitive grant
    #    into an amount a production can count on.
    if engine in NON_ENTITLEMENT_ENGINES:
        return result("CONDITIONAL", mechanism_no_figure_reason(db_row))

    # 5. The formula needs a statutory figure we do not hold.
    # ``declared_inputs`` comes from ``programme_required_inputs``, which the
    # caller loads once per report rather than once per programme. Passing None
    # falls back to the engine's default input list, which is right for a
    # programme that declares nothing unusual.
    supplied = _supplied_inputs(scenario)
    missing = (
        missing_required_inputs(engine, supplied, declared_inputs) if engine else []
    )
    if missing:
        named = ", ".join(key.replace("_", " ") for key in missing)
        return result("REQUIRES_COST_BREAKDOWN", f"Not yet supplied: {named}.")

    # 6. Held, but resting on something unresolved.
    provenance = _provenance(scenario)
    if any(v == "planning_assumption" for v in provenance.values()):
        return result(
            "CONDITIONAL",
            "One or more figures are planning assumptions rather than confirmed "
            "costs.",
        )
    if est.get("incentiveIsConfirmed") is False:
        for reason in est.get("incentiveEligibilityReasons") or []:
            reasons.append(str(reason))
        return result(
            "CONDITIONAL",
            None if reasons else "Eligibility for this programme is unresolved.",
        )

    if not engine:
        # No v2 engine recorded, so there is no declared input list to check
        # against. The figure comes from the legacy path and is a scenario until
        # the programme is migrated; saying so is better than implying the
        # deterministic formula ran.
        return result(
            "CONDITIONAL",
            "This programme has not yet been migrated to a statutory engine, so "
            "its figure is indicative.",
        )

    return result("ESTIMATED")
