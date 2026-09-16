"""Conservative eligibility and ranking kernel for v2 opportunity engines.

Only structured, source-backed cycles and rules belong here. A handoff spreadsheet's
``paid_safe`` flag or prose ``hard_gates`` is not a substitute for live eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from app.modules.reports.project_dna import ProjectDNA

GateResult = Literal["PASS", "FAIL", "UNKNOWN"]
Eligibility = Literal[
    "ELIGIBLE_CONFIRMED", "POTENTIALLY_ELIGIBLE", "INELIGIBLE_CONFIRMED", "NOT_ACTIONABLE"
]


@dataclass(frozen=True)
class HardGate:
    field: str
    operator: Literal["equals", "one_of", "at_least", "at_most", "overlaps"]
    expected: Any
    source_url: str
    condition: str


@dataclass(frozen=True)
class FitSignal:
    label: str
    points: int
    source_url: str


@dataclass(frozen=True)
class Opportunity:
    id: str
    name: str
    kind: Literal["FESTIVAL", "MARKET_LAB_WIP"]
    source_url: str
    verified_on: date
    cycle_open: date | None
    cycle_deadline: date | None
    cycle_verified: bool
    rules_complete: bool
    gates: tuple[HardGate, ...] = ()
    fit_signals: tuple[FitSignal, ...] = ()


@dataclass(frozen=True)
class Recommendation:
    opportunity: Opportunity
    eligibility: Eligibility
    score: int
    conditions_to_confirm: tuple[str, ...]
    gate_results: tuple[tuple[str, GateResult], ...]


@dataclass(frozen=True)
class OpportunityStrategy:
    kind: str
    universe_count: int
    actionable_count: int
    eligible_count: int
    potential_count: int
    recommendations: tuple[Recommendation, ...]


def _evaluate_gate(gate: HardGate, dna: ProjectDNA) -> GateResult:
    if not gate.source_url:
        return "UNKNOWN"
    fact = dna.get(gate.field)
    if fact.state == "UNKNOWN" or fact.confirmation_required:
        return "UNKNOWN"
    value = fact.value
    expected = gate.expected
    try:
        if gate.operator == "equals":
            passed = str(value).casefold() == str(expected).casefold()
        elif gate.operator == "one_of":
            passed = str(value).casefold() in {str(x).casefold() for x in expected}
        elif gate.operator == "overlaps":
            passed = bool(
                {str(x).casefold() for x in value} & {str(x).casefold() for x in expected}
            )
        elif gate.operator == "at_least":
            passed = float(value) >= float(expected)
        elif gate.operator == "at_most":
            passed = float(value) <= float(expected)
        else:
            return "UNKNOWN"
    except (TypeError, ValueError):
        return "UNKNOWN"
    return "PASS" if passed else "FAIL"


def evaluate_opportunity(
    opportunity: Opportunity, dna: ProjectDNA, *, today: date
) -> Recommendation:
    # No structured current-cycle boundary means no claim that applications are
    # actionable today. A verified historical listing is still historical.
    actionable = (
        opportunity.cycle_verified
        and opportunity.verified_on <= today
        and opportunity.cycle_open is not None
        and opportunity.cycle_deadline is not None
        and opportunity.cycle_open <= today <= opportunity.cycle_deadline
        and bool(opportunity.source_url)
    )
    if not actionable:
        return Recommendation(opportunity, "NOT_ACTIONABLE", 0, (), ())

    results = tuple((gate.condition, _evaluate_gate(gate, dna)) for gate in opportunity.gates)
    if any(result == "FAIL" for _, result in results):
        eligibility: Eligibility = "INELIGIBLE_CONFIRMED"
    elif (
        not opportunity.rules_complete
        or not opportunity.gates
        or any(result == "UNKNOWN" for _, result in results)
    ):
        eligibility = "POTENTIALLY_ELIGIBLE"
    else:
        eligibility = "ELIGIBLE_CONFIRMED"

    conditions = tuple(condition for condition, result in results if result == "UNKNOWN")
    if not opportunity.rules_complete:
        conditions += ("Material eligibility rules have not been fully structured and verified.",)
    elif not opportunity.gates:
        conditions += ("No source-linked eligibility gates have been encoded.",)
    score = sum(
        signal.points
        for signal in opportunity.fit_signals
        if signal.source_url and signal.points > 0
    )
    return Recommendation(opportunity, eligibility, score, conditions, results)


def build_opportunity_strategy(
    opportunities: list[Opportunity],
    dna: ProjectDNA,
    *,
    kind: Literal["FESTIVAL", "MARKET_LAB_WIP"],
    package: str,
    today: date,
) -> OpportunityStrategy:
    universe = [item for item in opportunities if item.kind == kind]
    evaluated = [evaluate_opportunity(item, dna, today=today) for item in universe]
    actionable = [item for item in evaluated if item.eligibility != "NOT_ACTIONABLE"]
    rankable = [
        item
        for item in actionable
        if item.eligibility not in {"INELIGIBLE_CONFIRMED", "NOT_ACTIONABLE"}
    ]
    rankable.sort(
        key=lambda item: (
            item.eligibility != "ELIGIBLE_CONFIRMED",
            -item.score,
            item.opportunity.cycle_deadline or date.max,
            item.opportunity.id,
        )
    )
    # Match the existing Single/Professional/Producer/Studio report packages.
    # Unknown identifiers receive the smaller entitlement.
    entitlement = 10 if package.strip().lower() in {"producer", "studio"} else 5
    return OpportunityStrategy(
        kind=kind,
        universe_count=len(universe),
        actionable_count=len(actionable),
        eligible_count=sum(item.eligibility == "ELIGIBLE_CONFIRMED" for item in actionable),
        potential_count=sum(item.eligibility == "POTENTIALLY_ELIGIBLE" for item in actionable),
        recommendations=tuple(rankable[:entitlement]),
    )
