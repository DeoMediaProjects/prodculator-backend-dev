"""MarketsLabsWIPStrategy: the canonical Section 09 output, lifecycle-sequenced.

WHY THIS IS A SEPARATE SECTION AND NOT PART OF GRANTS
-----------------------------------------------------
The Devil Wears Prada regression found Film London Production Finance Market
listed as a grant. It is not one, and the distinction is not pedantry: a grant
is money that may be awarded, while a finance market is a room where a producer
may meet people who have money. Counting the second as the first overstates a
production's financial position by the whole amount.

So markets, labs, WIP screenings, pitching forums and finance forums live here,
in their own section, and nothing in this module produces a figure. Market
access is never secured finance.

LIFECYCLE RATHER THAN SCORE
---------------------------
A development lab and a work-in-progress screening are both good opportunities
and they are not alternatives — they belong to different points in a
production's life, and recommending a rough-cut screening to a project without
footage wastes the slot. Ranking alone cannot express that, because the
rough-cut screening might genuinely score higher.

So each recommendation carries a lifecycle sequence label from the frozen
vocabulary, saying whether this is something to act on now, prepare for, or
revisit once the project advances.

WHAT IT REFUSES
---------------
An opportunity whose class is not recorded gets no lifecycle judgement. Guessing
that an unlabelled programme suits the project's current stage is how a producer
ends up preparing materials for a call they cannot enter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.modules.reports.opportunity_strategy import (
    Opportunity,
    Recommendation,
    evaluate_opportunity,
)
from app.modules.reports.project_dna import ProjectDNA

#: The frozen sequence vocabulary, from markets_labs_wip_sequence_labels_v1.
APPLY_NOW = "APPLY_NOW"
PREPARE_FOR_OPENING = "PREPARE_FOR_OPENING"
HIGH_PRIORITY = "HIGH_PRIORITY"
PARALLEL_SAFE = "PARALLEL_SAFE"
SECOND_WAVE = "SECOND_WAVE"
MONITOR_NEXT_CALL = "MONITOR_NEXT_CALL"
INVITATION_ROUTE_ONLY = "INVITATION_ROUTE_ONLY"
NOT_YET_STAGE_READY = "NOT_YET_STAGE_READY"

MarketSequence = Literal[
    "APPLY_NOW",
    "PREPARE_FOR_OPENING",
    "HIGH_PRIORITY",
    "PARALLEL_SAFE",
    "SECOND_WAVE",
    "MONITOR_NEXT_CALL",
    "INVITATION_ROUTE_ONLY",
    "NOT_YET_STAGE_READY",
]

SEQUENCE_LABELS: dict[str, str] = {
    APPLY_NOW: "Apply now",
    PREPARE_FOR_OPENING: "Prepare before it opens",
    HIGH_PRIORITY: "High priority",
    PARALLEL_SAFE: "Can be pursued in parallel",
    SECOND_WAVE: "Second wave",
    MONITOR_NEXT_CALL: "Monitor for the next call",
    INVITATION_ROUTE_ONLY: "Invitation route only",
    NOT_YET_STAGE_READY: "Not yet at the required stage",
}

#: Which production stages each frozen class is built for. A class absent from
#: this map has no lifecycle judgement attached rather than a default one.
_CLASS_STAGES: dict[str, frozenset[str]] = {
    "DEVELOPMENT_LAB": frozenset({"development"}),
    "TALENT_LAB": frozenset({"development", "pre_production"}),
    "TALENT_LAB_FELLOWSHIP": frozenset({"development", "pre_production"}),
    "COPRODUCTION_MARKET": frozenset({"development", "pre_production", "financing"}),
    "PITCHING_FORUM": frozenset({"development", "pre_production", "financing"}),
    "FINANCE_FORUM": frozenset({"development", "pre_production", "financing"}),
    "WIP_ROUGH_CUT": frozenset({"post_production"}),
    "POST_PRODUCTION_LAB": frozenset({"post_production"}),
    "INDUSTRY_SHOWCASE": frozenset({"post_production", "completed"}),
}

#: Classes a producer cannot simply apply to. Presenting one as an open call
#: sends them to a door that does not open from the outside.
_INVITATION_ONLY_CLASSES: frozenset[str] = frozenset({"INDUSTRY_SHOWCASE"})


@dataclass(frozen=True)
class MarketRecommendation:
    recommendation: Recommendation
    sequence: MarketSequence
    sequence_label: str
    sequence_reason: str
    opportunity_class: str | None

    @property
    def opportunity(self) -> Opportunity:
        return self.recommendation.opportunity

    @property
    def eligibility(self) -> str:
        return self.recommendation.eligibility


@dataclass(frozen=True)
class MarketsLabsWIPStrategy:
    universe_count: int
    actionable_count: int
    eligible_count: int
    potential_count: int
    recommendations: tuple[MarketRecommendation, ...]
    projectfacts_snapshot_id: str | None = None
    projectfacts_version: str | None = None

    @property
    def is_committed_finance(self) -> bool:
        """Always false, and present so no caller has to decide for itself.

        Market access is an opportunity to meet financiers. A report that totals
        these into a finance position has overstated it by their whole value.
        """
        return False


def _project_stage(dna: ProjectDNA) -> str | None:
    fact = dna.get("stage")
    if fact.state == "UNKNOWN" or fact.confirmation_required:
        return None
    return str(fact.value).strip().lower() or None


def _class_of(opportunity: Opportunity) -> str | None:
    value = getattr(opportunity, "opportunity_class", None)
    if value is None:
        return None
    normalised = str(value).strip().upper()
    return normalised or None


def sequence_markets(
    ranked: list[Recommendation], dna: ProjectDNA, *, today: date
) -> tuple[MarketRecommendation, ...]:
    """Assign each recommendation its lifecycle position, without reordering."""
    stage = _project_stage(dna)
    sequenced: list[MarketRecommendation] = []
    high_priority_claimed = False

    for item in ranked:
        opportunity_class = _class_of(item.opportunity)

        def emit(sequence: str, reason: str) -> None:
            sequenced.append(
                MarketRecommendation(
                    item,
                    sequence,
                    SEQUENCE_LABELS[sequence],
                    reason,
                    opportunity_class,
                )
            )

        if opportunity_class in _INVITATION_ONLY_CLASSES:
            emit(
                INVITATION_ROUTE_ONLY,
                "This programme is entered by invitation or curation rather than "
                "open application.",
            )
            continue

        suits = _CLASS_STAGES.get(opportunity_class or "")
        if suits is not None and stage is not None and stage not in suits:
            emit(
                NOT_YET_STAGE_READY,
                f"This programme is built for projects at "
                f"{', '.join(sorted(suits))}; this production is at {stage}.",
            )
            continue

        if item.application_status == "UPCOMING":
            emit(
                PREPARE_FOR_OPENING,
                "A verified opening date is ahead. Prepare materials now; this is "
                "not an open call today.",
            )
            continue

        if item.eligibility == "POTENTIALLY_ELIGIBLE" and item.conditions_to_confirm:
            emit(
                MONITOR_NEXT_CALL,
                "Programme fit is relevant, but conditions remain unconfirmed.",
            )
            continue

        if not high_priority_claimed and item.eligibility == "ELIGIBLE_CONFIRMED":
            high_priority_claimed = True
            emit(
                HIGH_PRIORITY,
                "Open, confirmed eligible and the strongest lifecycle fit "
                "available.",
            )
            continue

        if high_priority_claimed:
            emit(
                SECOND_WAVE,
                "Useful after the higher-priority opportunity above, or once the "
                "project advances.",
            )
            continue

        emit(
            APPLY_NOW,
            "The call is open and this production is eligible or potentially "
            "eligible today.",
        )

    return tuple(sequenced)


def build_markets_strategy(
    opportunities: list[Opportunity],
    dna: ProjectDNA,
    *,
    package: str,
    today: date,
    projectfacts_snapshot_id: str | None = None,
    projectfacts_version: str | None = None,
) -> MarketsLabsWIPStrategy:
    """Evaluate, rank the full universe, sequence, then apply package depth."""
    universe = [item for item in opportunities if item.kind == "MARKET_LAB_WIP"]
    evaluated = [evaluate_opportunity(item, dna, today=today) for item in universe]
    actionable = [item for item in evaluated if item.eligibility != "NOT_ACTIONABLE"]

    rankable = [
        item for item in actionable if item.eligibility != "INELIGIBLE_CONFIRMED"
    ]
    rankable.sort(
        key=lambda item: (
            item.eligibility != "ELIGIBLE_CONFIRMED",
            -item.score,
            -sum(result == "PASS" for _, result in item.gate_results),
            sum(result == "UNKNOWN" for _, result in item.gate_results),
            item.opportunity.cycle_deadline or date.max,
            item.opportunity.id,
        )
    )

    sequenced = sequence_markets(rankable, dna, today=today)
    entitlement = 10 if str(package).strip().lower() in {"producer", "studio"} else 5
    return MarketsLabsWIPStrategy(
        universe_count=len(universe),
        actionable_count=len(actionable),
        eligible_count=sum(
            item.eligibility == "ELIGIBLE_CONFIRMED" for item in actionable
        ),
        potential_count=sum(
            item.eligibility == "POTENTIALLY_ELIGIBLE" for item in actionable
        ),
        recommendations=sequenced[:entitlement],
        projectfacts_snapshot_id=projectfacts_snapshot_id,
        projectfacts_version=projectfacts_version,
    )
