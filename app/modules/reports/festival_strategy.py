"""FestivalStrategy: the canonical Section 11 output, with premiere sequencing.

WHAT THIS ADDS TO THE KERNEL
----------------------------
``opportunity_strategy`` already answers whether a festival's cycle is current
and whether this production clears its gates. That produces a ranked list, and a
ranked list is not a festival strategy.

The missing piece is sequence. Festivals compete with each other in a way grants
do not: a world premiere can only happen once, so submitting to two festivals
that both require one is not twice the chance, it is a conflict the producer has
to resolve. The implementation note is explicit that recommendations must form a
premiere-aware sequence rather than a similarity list.

THE RULE THAT SHAPES THE SEQUENCE
---------------------------------
Unknown premiere history is not proof that premiere status is intact.

That is the Devil Wears Prada case and it inverts the intuitive default. A
production whose premiere history we have not been told about looks, to a naive
ranker, exactly like one with a clean premiere record — both have nothing on
file. Treating them the same would tell a producer to submit first to a festival
that may already be closed to them.

So an unknown premiere fact does not produce a submission order at all. It
produces ``NEEDS_CONFIRMATION``, which names the one thing the producer must
establish before any of this sequencing means anything.

A premiere requirement is read as typed data or not at all. The freeze carries
prose on 34 of 380 records and nothing on the rest; prose is not a requirement
this engine can act on, and parsing it into one would manufacture a constraint
nobody verified.
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

#: The frozen sequencing vocabulary, from the Festival Engine v2.1 runtime
#: contract.
SUBMIT_FIRST = "SUBMIT_FIRST"
HOLD_UNTIL_DECISION = "HOLD_UNTIL_DECISION"
PARALLEL_SAFE = "PARALLEL_SAFE"
SECOND_WAVE = "SECOND_WAVE"
NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
NOT_ELIGIBLE = "NOT_ELIGIBLE"

FestivalSequence = Literal[
    "SUBMIT_FIRST",
    "HOLD_UNTIL_DECISION",
    "PARALLEL_SAFE",
    "SECOND_WAVE",
    "NEEDS_CONFIRMATION",
    "NOT_ELIGIBLE",
]

SEQUENCE_LABELS: dict[str, str] = {
    SUBMIT_FIRST: "Submit first",
    HOLD_UNTIL_DECISION: "Hold until the first decision",
    PARALLEL_SAFE: "Can be submitted in parallel",
    SECOND_WAVE: "Second wave",
    NEEDS_CONFIRMATION: "Confirm premiere status first",
    NOT_ELIGIBLE: "Not eligible",
}

#: Typed premiere requirements. ``NONE`` is a positive statement that the
#: festival imposes none, which is different from having no record either way —
#: the latter is ``None`` and reads as unknown.
PREMIERE_WORLD = "WORLD"
PREMIERE_INTERNATIONAL = "INTERNATIONAL"
PREMIERE_NATIONAL = "NATIONAL"
PREMIERE_NONE = "NONE"

#: Requirements a competing submission can consume. A national premiere is
#: included: two festivals in the same country both requiring one conflict just
#: as surely as two world premieres do.
_EXCLUSIVE_REQUIREMENTS: frozenset[str] = frozenset(
    {PREMIERE_WORLD, PREMIERE_INTERNATIONAL, PREMIERE_NATIONAL}
)

#: Project facts that decide whether a premiere-requiring festival is reachable.
#: Both must be established; either being unknown suspends the sequence.
_PREMIERE_FACTS: tuple[str, ...] = ("premiere_history", "public_online_availability")


@dataclass(frozen=True)
class FestivalRecommendation:
    recommendation: Recommendation
    sequence: FestivalSequence
    sequence_label: str
    sequence_reason: str
    premiere_requirement: str | None

    @property
    def opportunity(self) -> Opportunity:
        return self.recommendation.opportunity

    @property
    def eligibility(self) -> str:
        return self.recommendation.eligibility


@dataclass(frozen=True)
class FestivalStrategy:
    universe_count: int
    actionable_count: int
    eligible_count: int
    potential_count: int
    recommendations: tuple[FestivalRecommendation, ...]
    #: The one input state this result was computed against. Populated by the
    #: builder; a result whose snapshot differs from the report's is refused
    #: rather than merged.
    projectfacts_snapshot_id: str | None = None
    projectfacts_version: str | None = None

    @property
    def needs_premiere_confirmation(self) -> bool:
        return any(
            item.sequence == NEEDS_CONFIRMATION for item in self.recommendations
        )


def _premiere_status_known(dna: ProjectDNA) -> bool:
    """Whether the production's premiere position is actually established.

    A fact flagged as needing confirmation counts as unknown. It may well be
    correct, and nobody has confirmed it, which is the same thing to a sequence
    that would otherwise tell a producer where to submit first.
    """
    for field in _PREMIERE_FACTS:
        fact = dna.get(field)
        if fact.state == "UNKNOWN" or fact.confirmation_required:
            return False
    return True


def _requirement_of(opportunity: Opportunity) -> str | None:
    requirement = getattr(opportunity, "premiere_requirement", None)
    if requirement is None:
        return None
    normalised = str(requirement).strip().upper()
    if normalised in {
        PREMIERE_WORLD,
        PREMIERE_INTERNATIONAL,
        PREMIERE_NATIONAL,
        PREMIERE_NONE,
    }:
        return normalised
    # Prose, or a label nobody typed. Not a requirement this engine can act on.
    return None


def sequence_festivals(
    ranked: list[Recommendation], dna: ProjectDNA
) -> tuple[FestivalRecommendation, ...]:
    """Turn a ranked list into a premiere-aware submission sequence.

    ``ranked`` must already be in the kernel's ranking order; this assigns a
    position in the plan without reordering. Rank answers "which festival is the
    better fit"; sequence answers "which one can be submitted to first without
    foreclosing the others", and they are different questions.
    """
    premiere_known = _premiere_status_known(dna)
    sequenced: list[FestivalRecommendation] = []
    exclusive_claimed = False

    for item in ranked:
        requirement = _requirement_of(item.opportunity)

        if item.eligibility == "INELIGIBLE_CONFIRMED":
            sequenced.append(
                FestivalRecommendation(
                    item,
                    NOT_ELIGIBLE,
                    SEQUENCE_LABELS[NOT_ELIGIBLE],
                    "A verified eligibility rule excludes this production.",
                    requirement,
                )
            )
            continue

        if requirement is None:
            # No typed requirement on record. The festival may impose one; we
            # have not verified that it does not, and a sequence built on that
            # silence would be a guess with a submission deadline attached.
            sequenced.append(
                FestivalRecommendation(
                    item,
                    NEEDS_CONFIRMATION,
                    SEQUENCE_LABELS[NEEDS_CONFIRMATION],
                    "This festival's premiere requirement is not recorded as a "
                    "verified rule, so its place in a submission order cannot be "
                    "established.",
                    requirement,
                )
            )
            continue

        if requirement == PREMIERE_NONE:
            sequenced.append(
                FestivalRecommendation(
                    item,
                    PARALLEL_SAFE,
                    SEQUENCE_LABELS[PARALLEL_SAFE],
                    "This festival states no premiere requirement, so submitting "
                    "here does not foreclose another festival.",
                    requirement,
                )
            )
            continue

        # From here the festival requires a premiere this production can only
        # spend once.
        if not premiere_known:
            sequenced.append(
                FestivalRecommendation(
                    item,
                    NEEDS_CONFIRMATION,
                    SEQUENCE_LABELS[NEEDS_CONFIRMATION],
                    "This festival requires a premiere, and this production's "
                    "premiere history is not established. An unknown premiere "
                    "history is not evidence that premiere status is intact.",
                    requirement,
                )
            )
            continue

        if requirement in _EXCLUSIVE_REQUIREMENTS and not exclusive_claimed:
            exclusive_claimed = True
            sequenced.append(
                FestivalRecommendation(
                    item,
                    SUBMIT_FIRST,
                    SEQUENCE_LABELS[SUBMIT_FIRST],
                    "The strongest-ranked festival requiring a premiere this "
                    "production still holds.",
                    requirement,
                )
            )
            continue

        sequenced.append(
            FestivalRecommendation(
                item,
                HOLD_UNTIL_DECISION,
                SEQUENCE_LABELS[HOLD_UNTIL_DECISION],
                "Also requires a premiere already committed to an earlier "
                "submission. Hold until that decision is known.",
                requirement,
            )
        )

    # Anything parallel-safe below the first exclusive claim is a second wave
    # rather than a simultaneous submission: it is still reachable, and doing it
    # first spends attention the primary submission needs.
    if exclusive_claimed:
        promoted = False
        result: list[FestivalRecommendation] = []
        for item in sequenced:
            if item.sequence == SUBMIT_FIRST:
                promoted = True
                result.append(item)
            elif promoted and item.sequence == PARALLEL_SAFE:
                result.append(
                    FestivalRecommendation(
                        item.recommendation,
                        SECOND_WAVE,
                        SEQUENCE_LABELS[SECOND_WAVE],
                        "Reachable after the premiere submission above has been "
                        "decided.",
                        item.premiere_requirement,
                    )
                )
            else:
                result.append(item)
        sequenced = result

    return tuple(sequenced)


def build_festival_strategy(
    opportunities: list[Opportunity],
    dna: ProjectDNA,
    *,
    package: str,
    today: date,
    projectfacts_snapshot_id: str | None = None,
    projectfacts_version: str | None = None,
) -> FestivalStrategy:
    """Evaluate, rank the full universe, sequence, then apply package depth.

    Sequencing happens before truncation so the plan a producer sees is the top
    of one coherent order, not a coherent order over an arbitrary five.
    """
    universe = [item for item in opportunities if item.kind == "FESTIVAL"]
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

    # Several sections of one festival are alternatives, not separate slots.
    distinct: list[Recommendation] = []
    seen: set[str] = set()
    for item in rankable:
        identity = item.opportunity.record_id or item.opportunity.id
        if identity in seen:
            continue
        seen.add(identity)
        distinct.append(item)

    sequenced = sequence_festivals(distinct, dna)
    entitlement = 10 if str(package).strip().lower() in {"producer", "studio"} else 5
    return FestivalStrategy(
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
