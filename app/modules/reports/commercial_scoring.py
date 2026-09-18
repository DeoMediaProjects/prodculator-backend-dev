"""The frozen 100-point strategic fit score, and the portfolio it selects.

WHAT A SCORE HERE MEANS
-----------------------
It is a modelled strategic fit and nothing else. The implementation note says so
twice, and the distinction is the whole reason this module is careful: a high
score means this company's sourced profile lines up with this production's
sourced profile, not that it will buy the film, not that it is likely to
acquire, and not that it is scouting. There is no probability anywhere in here
to mistake for one.

THE EIGHT COMPONENTS
--------------------
The weights are frozen by the v2 implementation note, section 8, and total 100::

    commercial comparable evidence   25
    genre / content / audience fit   20
    territory / rights fit           15
    format / scale fit               10
    festival / market intersection   10
    current / recent slate similarity 10
    lifecycle / financing-stage fit    5
    access-path quality                5

THE UNKNOWN RULE
----------------
An unknown component scores zero for that component. It is never inferred, never
averaged away, and never renormalised out of the denominator.

That last one matters more than it looks. Scoring a company on the five
components we happen to hold and rescaling to 100 would let a company with two
sourced facts outrank one with eight, purely by having less known about it. The
denominator stays at 100 and a thin profile scores low, which is the honest
answer: we do not know enough about this company to rank it highly.

Because of that, ``components_known`` travels with every score. A 40 built from
eight known components and a 40 built from two are different claims, and a
report that shows only the number cannot tell them apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from app.modules.reports.commercial_freeze import (
    ACCESS_NO_UNSOLICITED,
    ACCESS_QUALITY,
    ACCESS_UNKNOWN,
)

# ── The frozen weights ───────────────────────────────────────────────────────

COMPONENT_WEIGHTS: dict[str, int] = {
    "commercial_comparable_evidence": 25,
    "genre_content_audience_fit": 20,
    "territory_rights_fit": 15,
    "format_scale_fit": 10,
    "festival_market_intersection": 10,
    "slate_similarity": 10,
    "lifecycle_stage_fit": 5,
    "access_path_quality": 5,
}

MAX_SCORE = sum(COMPONENT_WEIGHTS.values())

# ── Canonical match states ───────────────────────────────────────────────────

#: Sourced evidence supports this company as a strategic fit, and a producer can
#: see how to approach it.
STRATEGIC_MATCH = "STRATEGIC_MATCH"
#: The fit is supported but something material is unconfirmed.
POTENTIAL_MATCH_NEEDS_CONFIRMATION = "POTENTIAL_MATCH_NEEDS_CONFIRMATION"
#: The commercial fit holds and the way in does not. Its own state, because the
#: producer's next action is entirely different: research a route, not reconsider
#: the company.
ACCESS_ROUTE_UNKNOWN = "ACCESS_ROUTE_UNKNOWN"
#: A hard gate failed, or nothing sourced supports a fit.
NOT_SUITABLE = "NOT_SUITABLE"

MATCH_STATE_LABELS: dict[str, str] = {
    STRATEGIC_MATCH: "Strategic match",
    POTENTIAL_MATCH_NEEDS_CONFIRMATION: "Potential match — confirmation needed",
    ACCESS_ROUTE_UNKNOWN: "Commercially relevant — access route not established",
    NOT_SUITABLE: "Not suitable for this production",
}

#: States that may occupy a paid package slot, in the order they rank.
_RANKABLE_STATES: tuple[str, ...] = (
    STRATEGIC_MATCH,
    POTENTIAL_MATCH_NEEDS_CONFIRMATION,
    ACCESS_ROUTE_UNKNOWN,
)

#: Minimum score below which a company is not offered at all, whatever its
#: state. A company scoring under a fifth of the available points is being
#: recommended on almost nothing, and filling a slot with it is worse than
#: returning fewer results.
_MINIMUM_OFFERABLE_SCORE = 20


@dataclass(frozen=True)
class ComponentScore:
    key: str
    weight: int
    #: None when the component is unknown. Zero means known and no overlap,
    #: which is a different fact and ranks the same but explains differently.
    fraction: float | None
    evidence: tuple[str, ...] = ()

    @property
    def points(self) -> float:
        return 0.0 if self.fraction is None else self.weight * self.fraction

    @property
    def is_known(self) -> bool:
        return self.fraction is not None


@dataclass(frozen=True)
class StrategicFit:
    """One company's modelled fit. Never an acquisition probability."""

    score: int
    components: tuple[ComponentScore, ...]

    @property
    def components_known(self) -> int:
        return sum(component.is_known for component in self.components)

    @property
    def unknown_components(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.components if not c.is_known)

    @property
    def evidence(self) -> tuple[str, ...]:
        return tuple(item for component in self.components for item in component.evidence)


def score_strategic_fit(components: dict[str, ComponentScore]) -> StrategicFit:
    """Total the eight frozen components.

    A component the caller did not supply is unknown, not absent: the weight
    stays in the denominator and contributes nothing. There is no path through
    this function that produces a score out of anything other than 100.
    """
    # Checked against the supplied keys, not the frozen ones: building the tuple
    # from COMPONENT_WEIGHTS alone would drop an invented component silently,
    # and a caller scoring against a component nobody froze needs to hear so.
    for key in components:
        if key not in COMPONENT_WEIGHTS:
            raise ValueError(f"{key} is not a frozen score component")

    ordered = tuple(
        components.get(key) or ComponentScore(key, weight, None)
        for key, weight in COMPONENT_WEIGHTS.items()
    )
    for component in ordered:
        expected = COMPONENT_WEIGHTS.get(component.key)
        if expected is None:
            raise ValueError(f"{component.key} is not a frozen score component")
        if component.weight != expected:
            raise ValueError(
                f"{component.key} weighted {component.weight}, frozen at {expected}"
            )
        if component.fraction is not None and not 0.0 <= component.fraction <= 1.0:
            raise ValueError(f"{component.key} fraction is not a share of its weight")
    return StrategicFit(
        score=round(sum(component.points for component in ordered)),
        components=ordered,
    )


def overlap_component(
    key: str,
    project_values: Iterable[str] | None,
    company_values: Iterable[str] | None,
    *,
    evidence_prefix: str,
) -> ComponentScore:
    """A component scored on how much of the company's scope this production meets.

    Either side being None means unknown. An empty company scope is also unknown
    rather than a universal match: a blank column says the workbook did not
    record what this company acquires, which is not the same as "anything".
    """
    weight = COMPONENT_WEIGHTS[key]
    if project_values is None or company_values is None:
        return ComponentScore(key, weight, None)
    project = {str(v).strip().casefold() for v in project_values if str(v).strip()}
    company = {str(v).strip().casefold() for v in company_values if str(v).strip()}
    if not project or not company:
        return ComponentScore(key, weight, None)
    shared = project & company
    if not shared:
        return ComponentScore(key, weight, 0.0)
    return ComponentScore(
        key,
        weight,
        len(shared) / len(project),
        (f"{evidence_prefix}: {', '.join(sorted(shared))}",),
    )


def access_component(access_route: str) -> ComponentScore:
    """The access-path component, which is known whenever the route is stated.

    An unknown route scores zero as an unknown component. A stated refusal also
    scores zero, but as a KNOWN component — the producer has been told something
    definite, and the report must be able to say so rather than presenting it as
    an open question.
    """
    weight = COMPONENT_WEIGHTS["access_path_quality"]
    if access_route == ACCESS_UNKNOWN:
        return ComponentScore("access_path_quality", weight, None)
    return ComponentScore(
        "access_path_quality",
        weight,
        ACCESS_QUALITY.get(access_route, 0.0),
        (f"Access route: {access_route}",),
    )


def resolve_match_state(
    fit: StrategicFit,
    *,
    access_route: str,
    hard_gate_failed: bool,
    conditions_to_confirm: Sequence[str],
) -> str:
    """The canonical state for one company, in a fixed precedence.

    A hard failure outranks everything: no amount of strategic fit makes a
    company that cannot acquire this production suitable. An unestablished access
    route is reported as its own state rather than folded into "needs
    confirmation", because the two send a producer to different work.
    """
    if hard_gate_failed or fit.score < _MINIMUM_OFFERABLE_SCORE:
        return NOT_SUITABLE
    if access_route in (ACCESS_UNKNOWN, ACCESS_NO_UNSOLICITED):
        return ACCESS_ROUTE_UNKNOWN
    if conditions_to_confirm or fit.unknown_components:
        return POTENTIAL_MATCH_NEEDS_CONFIRMATION
    return STRATEGIC_MATCH


# ── Portfolio selection ──────────────────────────────────────────────────────


def select_portfolio(
    candidates: Sequence[Any],
    *,
    entitlement: int,
    state_of: Callable[[Any], str],
    score_of: Callable[[Any], int],
    group_of: Callable[[Any], str],
    route_of: Callable[[Any], str],
    name_of: Callable[[Any], str],
) -> tuple[Any, ...]:
    """Rank the full universe, then fill the package slots.

    Two rules shape what comes out, and both exist because a list of the highest
    scores alone is a worse answer than it looks.

    Parent/label dedupe: a parent and its own label are one commercial route. A
    list holding both has spent two of a producer's five slots reaching the same
    company, so the higher-scoring row takes the group's slot and the other is
    held back.

    Route diversification: once every distinct access route is represented, near
    identical companies stop displacing different ones. A producer holding five
    names they cannot approach has five names and no options. This only ever
    reorders candidates that were already eligible — it never promotes a company
    over a materially better-scoring one, because the first pass takes the best
    of each route in rank order before any backfill happens.
    """
    if entitlement <= 0:
        return ()

    ranked = sorted(
        (item for item in candidates if state_of(item) in _RANKABLE_STATES),
        key=lambda item: (
            _RANKABLE_STATES.index(state_of(item)),
            -score_of(item),
            name_of(item).casefold(),
        ),
    )

    # One row per commercial group, best first.
    best_of_group: dict[str, Any] = {}
    for item in ranked:
        best_of_group.setdefault(group_of(item), item)
    deduped = [item for item in ranked if best_of_group.get(group_of(item)) is item]

    # First pass: the best candidate on each distinct access route, in rank
    # order, so the strongest overall is always among them.
    selected: list[Any] = []
    seen_routes: set[str] = set()
    for item in deduped:
        if len(selected) >= entitlement:
            break
        route = route_of(item)
        if route not in seen_routes:
            seen_routes.add(route)
            selected.append(item)

    # Backfill strictly by rank.
    chosen = set(id(item) for item in selected)
    for item in deduped:
        if len(selected) >= entitlement:
            break
        if id(item) not in chosen:
            selected.append(item)
            chosen.add(id(item))

    # Present in rank order regardless of the order they were picked, so the
    # list a producer reads still descends by strength.
    selected.sort(
        key=lambda item: (
            _RANKABLE_STATES.index(state_of(item)),
            -score_of(item),
            name_of(item).casefold(),
        )
    )
    return tuple(selected)
