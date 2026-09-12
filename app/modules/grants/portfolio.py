"""Strategic portfolio selection: what to show, in what order, and why not by score.

Developer Guide §10: "Do not simply take the top N by raw score. After scoring,
create a portfolio that avoids near-duplicate opportunities and, where project stage
allows, balances development/production/completion/distribution/co-production routes.
A slightly lower-scoring complementary route can outrank a duplicate route for
presentation. Preserve raw score and portfolio rank separately."

WHY A PURE SORT IS THE WRONG ANSWER
-----------------------------------
Score is dominated by territory: a fund whose territory is both a ranked territory
and the script origin earns +6 before any other signal. A national body usually runs
several strands, so a pure sort returns five strands of the same funder in the same
country — technically the five best-scoring records, and useless as a funding
strategy. Diversification is what turns a ranked list into a plan.

WHY IT IS RANKED TO EXHAUSTION
------------------------------
Every eligible result gets a rank, not just the entitled few. That is what makes the
Single 5 a strict prefix of the Producer 10 and satisfies acceptance_tests.md: "Same
project on Single and Producer tiers produces the same eligible-match universe and
scores; only visible result count differs." Slicing happens afterwards, elsewhere.

DETERMINISM
-----------
Two runs of the same report must be byte-identical. Every ordering input here is a
total order over unique opportunity ids, every table is a fixed literal, and no set
is ever iterated. ``raw_score`` is never modified.
"""
from __future__ import annotations

from app.modules.grants.schemas_v2 import MatchResult

#: An opportunity's funding route, derived from its type. First match wins, and the
#: ORDER MATTERS: "coproduction_development_fund" is a co-production route, not a
#: development one, so the co-production test has to run before the development test.
_ROUTE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("coproduction", "co_production", "co-production", "minority"), "co_production"),
    (("completion", "post_production", "post-production", "post"), "completion"),
    (("distribution", "festival_launch", "promotion", "sales", "impact", "exhibition"),
     "distribution"),
    (("development", "slate", "writing", "research", "talent"), "development"),
    (("production",), "production"),
)

#: Which routes to reach for first, given where the project actually is. A project in
#: production is not helped by five development funds it can no longer apply to.
_BUCKET_ORDER: dict[str, tuple[str, ...]] = {
    "development": ("development", "co_production", "production", "completion",
                    "distribution", "other"),
    "production": ("production", "co_production", "completion", "development",
                   "distribution", "other"),
    "completion": ("completion", "distribution", "co_production", "production",
                   "development", "other"),
    "distribution": ("distribution", "completion", "co_production", "production",
                     "development", "other"),
    "promotion": ("distribution", "completion", "co_production", "production",
                  "development", "other"),
}

#: Used when the project's stage is unknown, which is the common case — no intake
#: field asks for it.
_DEFAULT_BUCKET_ORDER: tuple[str, ...] = (
    "development", "production", "co_production", "completion", "distribution", "other",
)

#: At most this many opportunities from any one funding body before the
#: diversification pass stops admitting more. Two lets a body's development and
#: production strands both appear; three would let one national body own half a
#: five-slot list.
_MAX_PER_FUNDING_BODY = 2

#: The widest entitlement (Producer/Studio show 10), so route balance is enforced
#: exactly across what a reader can actually see and never distorts the tail.
_BALANCED_WINDOW = 10

#: At most this many of one route inside that window, so a producer is not handed ten
#: development funds and no production or co-production route.
_MAX_PER_ROUTE_IN_WINDOW = 4


def route_class(result: MatchResult) -> str:
    """Which funding route this opportunity represents."""
    haystack = " ".join(filter(None, [
        (result.display_fields.opportunity_type or "").lower(),
        " ".join(result.display_fields.production_stage),
    ]))
    for needles, route in _ROUTE_RULES:
        if any(needle in haystack for needle in needles):
            return route
    return "other"


def funder_key(result: MatchResult) -> str:
    """Who administers this opportunity, for de-duplication only.

    89 of the 253 master records carry no ``funding_body`` at all, and without a
    fallback every one of them looks like a different funder — so "Northern Ireland
    Screen — Project Development", "— Script Development" and "— Slate Development"
    would all reach a five-slot list as if they were unrelated.

    The fallback reads the title's own prefix, which those titles already use as the
    body name. It is used for grouping and never written back to the record: the
    missing ``funding_body`` stays missing and stays an admin data gap.
    """
    body = (result.display_fields.funding_body or "").strip().lower()
    if body:
        return body
    title = (result.display_fields.title or "").strip()
    for separator in ("—", " - ", ":"):
        if separator in title:
            return title.split(separator)[0].strip().lower()
    return title.lower() or result.opportunity_id


def _sort_key(result: MatchResult, route_priority: dict[str, int]) -> tuple:
    """A total order: score, then stage relevance, then stable text, then the id.

    Stage enters here and nowhere else. Among opportunities that scored the SAME, a
    development-stage project should see development money first — but stage never
    moves a lower score above a higher one, because the score is the evidence and the
    stage preference is only a preference.

    The id is what makes the order total — no two results can tie, so there is never
    an arbitrary choice and two runs of the same report cannot differ.
    """
    return (
        -result.raw_score,
        route_priority.get(route_class(result), len(route_priority)),
        (result.display_fields.funding_body or "").lower(),
        (result.display_fields.title or "").lower(),
        result.opportunity_id,
    )


def select_portfolio(results: list[MatchResult], *,
                     project_stage: str | None = None) -> list[MatchResult]:
    """Assign ``portfolio_rank`` across the whole eligible set.

    Returns the results in presentation order. ``raw_score`` is untouched, so a
    consumer can always see that a lower-ranked opportunity scored higher and know
    diversification is why.
    """
    if not results:
        return []

    stage = (project_stage or "").strip().lower()
    bucket_order = _BUCKET_ORDER.get(stage, _DEFAULT_BUCKET_ORDER)
    route_priority = {route: index for index, route in enumerate(bucket_order)}

    ordered = sorted(results, key=lambda r: _sort_key(r, route_priority))

    selected: list[MatchResult] = []
    taken: set[str] = set()
    used_dup_keys: set[tuple[str, str]] = set()
    body_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}

    def dup_key(result: MatchResult) -> tuple[str, str]:
        return (funder_key(result), route_class(result))

    def admit(result: MatchResult) -> None:
        selected.append(result)
        taken.add(result.opportunity_id)
        used_dup_keys.add(dup_key(result))
        body_counts[funder_key(result)] = body_counts.get(funder_key(result), 0) + 1
        route_counts[route_class(result)] = route_counts.get(route_class(result), 0) + 1

    # Score order is the spine: diversification DEFERS near-duplicates, it does not
    # reshuffle the list. An earlier version rotated strictly between routes and put a
    # 1.0-scoring fund above a 6.0-scoring one, which is not "a slightly lower-scoring
    # complementary route" — it is the algorithm overruling the evidence.
    #
    # So a candidate is passed over only when taking it would make the visible list
    # repetitive, and everything passed over is picked up by the backfill below with
    # its score order intact.
    for candidate in ordered:
        if candidate.opportunity_id in taken:
            continue
        body = funder_key(candidate)
        route = route_class(candidate)
        # Same funder, same kind of money: a near-duplicate of something already shown.
        if dup_key(candidate) in used_dup_keys:
            continue
        if body_counts.get(body, 0) >= _MAX_PER_FUNDING_BODY:
            continue
        # Route concentration, enforced only across the widest entitlement window.
        # Beyond it the list is backfill and balance no longer buys the reader
        # anything.
        if len(selected) < _BALANCED_WINDOW and \
                route_counts.get(route, 0) >= _MAX_PER_ROUTE_IN_WINDOW:
            continue
        admit(candidate)

    # Backfill in widening relaxations so nothing is lost — the list is ranked to
    # exhaustion, and only the display layer truncates.
    for allow_repeat_body in (False, True):
        for candidate in ordered:
            if candidate.opportunity_id in taken:
                continue
            if not allow_repeat_body and \
                    body_counts.get(funder_key(candidate), 0) >= _MAX_PER_FUNDING_BODY:
                continue
            admit(candidate)

    for position, result in enumerate(selected, start=1):
        result.portfolio_rank = position
    return selected
