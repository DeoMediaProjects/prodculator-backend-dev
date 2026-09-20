"""The 13-section report orchestrator. Assembles; never decides.

WHAT THIS IS FOR
----------------
The Devil Wears Prada regression found a report whose sections disagreed with
each other, because each one had reasoned independently. The tax-incentive
section said short-film eligibility was unverified while the ranking had already
counted that programme's rebate; a finance market appeared as a grant; a
narrative layer picked distributors nobody's matcher had returned.

Every one of those is the same defect: a section that computes rather than
reads. So this module has exactly one job — take one ProjectFacts snapshot and
the canonical engine results computed against it, and lay out thirteen sections
that consume them. It re-runs no matching, recalculates no rebate, selects no
festival and chooses no company. Where a section needs a fact, it reads the fact
another layer already settled.

That restraint is what makes the sections agree. Two sections quoting different
UK rates is impossible when neither one holds a rate.

THE SNAPSHOT RULE
-----------------
Every engine result carries the snapshot it was computed against. A result from
a different snapshot is two runs' facts in one report, which is invisible once
rendered: each section reads plausibly and only the numbers disagree. The run is
marked ``INCONSISTENT_INPUT_VERSION`` and refuses to assemble rather than
merging them.

WHAT IT MAY ADD
---------------
Three things, and they are all cross-engine, which is why no single engine could
own them:

Routing conflicts. The same source record reaching two engines is resolved by
the frozen rules — a finance market is not a grant however it was tagged.

Umbrella states. Six engines speak six vocabularies, and a reader needs one. The
engine's own state and reason codes travel alongside, so nothing is lost by the
translation.

Finance buckets. A matched grant, a festival selection and a distributor target
are opportunities, not money. Keeping them out of committed finance is a
cross-engine judgement because the temptation to total them only appears once
they are on the same page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

# ── The thirteen sections ────────────────────────────────────────────────────

#: Section number, key, title, and the engines it may consume. The engine list
#: is the section's whole licence: Section 08 naming only the grants engine is
#: what stops a finance market appearing there, enforced rather than described.
SECTIONS: tuple[tuple[int, str, str, tuple[str, ...]], ...] = (
    (1, "executive_summary", "Executive Summary", ()),
    (2, "script_intelligence", "Script Intelligence", ("script_analysis",)),
    (3, "production_location_strategy", "Production Location Strategy", ("location",)),
    (4, "territory_analysis", "Territory Analysis", ("location", "incentive")),
    (5, "financial_analysis", "Financial Analysis", ("incentive",)),
    (6, "weather_logistics", "Weather & Logistics", ("logistics",)),
    (7, "tax_incentive_analysis", "Tax Incentive Analysis", ("incentive",)),
    (8, "grant_funding_opportunities", "Grant & Funding Opportunities", ("grants",)),
    (
        9,
        "industry_development_market_strategy",
        "Industry Development & Market Strategy",
        ("markets_labs_wip",),
    ),
    (10, "comparable_productions", "Comparable Productions", ("comparables",)),
    (11, "festival_strategy", "Festival Strategy", ("festivals",)),
    (12, "sales_distribution_strategy", "Sales & Distribution Strategy", ("sales",)),
    (13, "next_steps", "Next Steps & Disclaimers", ()),
)

#: Sections that summarise other sections' conclusions and own no calculation of
#: their own. The implementation note is explicit that these two are downstream
#: consumers: an Executive Summary that independently picks a territory is a
#: second decision engine, and the first thing a reader notices is that it
#: disagrees with section 04.
DOWNSTREAM_SECTIONS: frozenset[str] = frozenset({"executive_summary", "next_steps"})

# ── Umbrella states ──────────────────────────────────────────────────────────

ACTIONABLE_CONFIRMED = "ACTIONABLE_CONFIRMED"
ACTIONABLE_WITH_CONDITIONS = "ACTIONABLE_WITH_CONDITIONS"
WATCH_UPCOMING = "WATCH_UPCOMING"
NOT_ACTIONABLE = "NOT_ACTIONABLE"
INELIGIBLE = "INELIGIBLE"
UNKNOWN_OR_NEEDS_CONFIRMATION = "UNKNOWN_OR_NEEDS_CONFIRMATION"

UMBRELLA_STATES: frozenset[str] = frozenset(
    {
        ACTIONABLE_CONFIRMED,
        ACTIONABLE_WITH_CONDITIONS,
        WATCH_UPCOMING,
        NOT_ACTIONABLE,
        INELIGIBLE,
        UNKNOWN_OR_NEEDS_CONFIRMATION,
    }
)

#: Engine vocabularies mapped to the six report-safe states. Every engine state
#: that exists is listed; an unlisted one falls to
#: UNKNOWN_OR_NEEDS_CONFIRMATION rather than to something actionable, because a
#: state nobody mapped is a state nobody has reasoned about.
_STATE_MAP: dict[str, str] = {
    # Festival Engine v2.1
    "ELIGIBLE_CONFIRMED": ACTIONABLE_CONFIRMED,
    "POTENTIALLY_ELIGIBLE": ACTIONABLE_WITH_CONDITIONS,
    "INELIGIBLE_CONFIRMED": INELIGIBLE,
    "NOT_ACTIONABLE": NOT_ACTIONABLE,
    # Opportunity kernel
    "CONFIRMED": ACTIONABLE_CONFIRMED,
    "POTENTIAL": ACTIONABLE_WITH_CONDITIONS,
    "UPCOMING": WATCH_UPCOMING,
    "EXPIRED": NOT_ACTIONABLE,
    # Incentive engine
    "eligible": ACTIONABLE_CONFIRMED,
    "conditional": ACTIONABLE_WITH_CONDITIONS,
    "unverified": UNKNOWN_OR_NEEDS_CONFIRMATION,
    "ineligible": INELIGIBLE,
    # Sales / distribution
    "STRATEGIC_MATCH": ACTIONABLE_CONFIRMED,
    "POTENTIAL_MATCH_NEEDS_CONFIRMATION": ACTIONABLE_WITH_CONDITIONS,
    "ACCESS_ROUTE_UNKNOWN": UNKNOWN_OR_NEEDS_CONFIRMATION,
    "NOT_SUITABLE": INELIGIBLE,
    # Commercial matcher
    "FIT_CONFIRMED": ACTIONABLE_CONFIRMED,
    "POTENTIAL_FIT": ACTIONABLE_WITH_CONDITIONS,
    "NOT_A_FIT": INELIGIBLE,
}


def umbrella_state(engine_state: Any) -> str:
    """One reader-facing state for any engine's own vocabulary.

    The engine's original state is never replaced by this, only accompanied by
    it. A reader needs one vocabulary; a developer chasing why a festival was
    dropped needs the engine's own word for it.
    """
    key = str(engine_state or "").strip()
    return _STATE_MAP.get(key, UNKNOWN_OR_NEEDS_CONFIRMATION)


# ── Finance buckets ──────────────────────────────────────────────────────────

DOCUMENTED_COMMITTED_FINANCE = "documented_committed_finance"
CONDITIONAL_STATUTORY_BENEFITS = "conditional_statutory_benefits"
SELECTIVE_PIPELINE_OPPORTUNITIES = "selective_pipeline_opportunities"
STRATEGIC_ACCESS_OPPORTUNITIES = "strategic_access_opportunities"

FINANCE_BUCKETS: tuple[str, ...] = (
    DOCUMENTED_COMMITTED_FINANCE,
    CONDITIONAL_STATUTORY_BENEFITS,
    SELECTIVE_PIPELINE_OPPORTUNITIES,
    STRATEGIC_ACCESS_OPPORTUNITIES,
)

#: Which bucket each kind of finance item belongs in. Nothing reaches bucket 1
#: from an engine: a matched grant, a selected festival, a market invitation and
#: a distributor target are all opportunities, and only documented award or
#: contract evidence moves money into committed finance. That evidence does not
#: come from a matcher, so no matcher can produce it.
_BUCKET_BY_KIND: dict[str, str] = {
    "incentive": CONDITIONAL_STATUTORY_BENEFITS,
    "grant_match": SELECTIVE_PIPELINE_OPPORTUNITIES,
    "market_opportunity": STRATEGIC_ACCESS_OPPORTUNITIES,
    "lab_opportunity": STRATEGIC_ACCESS_OPPORTUNITIES,
    "festival_selection": STRATEGIC_ACCESS_OPPORTUNITIES,
    "sales_route": STRATEGIC_ACCESS_OPPORTUNITIES,
    "distribution_route": STRATEGIC_ACCESS_OPPORTUNITIES,
}


def finance_bucket(kind: str, *, has_documented_award: bool = False) -> str:
    """Which finance bucket one item belongs in.

    ``has_documented_award`` is the only route into committed finance, and it is
    a fact about paperwork rather than about matching. A grant the production has
    actually been awarded is committed; the same grant as a match is pipeline,
    and the difference is a signed document nobody's matcher can see.
    """
    normalised = str(kind or "").strip().lower()
    if has_documented_award:
        if normalised in {"grant_match", "incentive"}:
            return DOCUMENTED_COMMITTED_FINANCE
        # A festival selection or a distributor meeting is not money however
        # well documented it is, so documentation does not promote it.
        return _BUCKET_BY_KIND.get(normalised, STRATEGIC_ACCESS_OPPORTUNITIES)
    return _BUCKET_BY_KIND.get(normalised, STRATEGIC_ACCESS_OPPORTUNITIES)


# ── Routing conflicts ────────────────────────────────────────────────────────

ROUTING_PRECEDENCE: tuple[str, ...] = (
    "incentive",
    "markets_labs_wip",
    "festivals",
    "grants",
)


@dataclass(frozen=True)
class RoutingConflict:
    subject_id: str
    subject_name: str
    claimed_by: tuple[str, ...]
    routed_to: str
    suppressed_from: tuple[str, ...]
    rule: str


def resolve_routing(
    claims: dict[str, Iterable[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], list[RoutingConflict]]:
    """Decide which engine owns a record claimed by more than one.

    ``claims`` maps engine name to the records it returned. A record's identity
    is its ``id``. The frozen rules say routing wins over presence: a finance
    market tagged as a grant is a market, and Film London Production Finance
    Market appearing under Grants is the regression this enforces.

    Returns the filtered claims plus one conflict record per suppression, so the
    report can explain a missing entry rather than silently dropping it.
    """
    owners: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for engine, records in claims.items():
        for record in records:
            subject = str(record.get("id") or "").strip()
            if not subject:
                continue
            owners.setdefault(subject, []).append(engine)
            names.setdefault(subject, str(record.get("name") or ""))

    conflicts: list[RoutingConflict] = []
    suppressed: dict[str, set[str]] = {}
    for subject, engines in owners.items():
        if len(set(engines)) < 2:
            continue
        winner = min(
            set(engines),
            key=lambda e: ROUTING_PRECEDENCE.index(e)
            if e in ROUTING_PRECEDENCE
            else len(ROUTING_PRECEDENCE),
        )
        losers = tuple(sorted(set(engines) - {winner}))
        for loser in losers:
            suppressed.setdefault(loser, set()).add(subject)
        conflicts.append(
            RoutingConflict(
                subject_id=subject,
                subject_name=names.get(subject, ""),
                claimed_by=tuple(sorted(set(engines))),
                routed_to=winner,
                suppressed_from=losers,
                rule=(
                    f"Routing wins over presence: this record is owned by "
                    f"{winner} and is suppressed from {', '.join(losers)}."
                ),
            )
        )

    filtered = {
        engine: [
            record
            for record in records
            if str(record.get("id") or "") not in suppressed.get(engine, set())
        ]
        for engine, records in claims.items()
    }
    return filtered, conflicts


# ── Engine results and the run ───────────────────────────────────────────────


@dataclass(frozen=True)
class EngineResult:
    """One specialist engine's canonical output for one report run."""

    engine_name: str
    engine_version: str
    projectfacts_snapshot_id: str
    projectfacts_version: str
    #: The full ranked universe size, kept apart from what is displayed. A
    #: reader told "10 shown" must not infer that only 10 were searched, and the
    #: flat list of ten cannot carry that number.
    eligible_universe_count: int = 0
    recommendations: tuple[Any, ...] = ()
    reason_codes: tuple[str, ...] = ()
    next_steps: tuple[dict[str, Any], ...] = ()
    generated_at: str | None = None


class InconsistentInputVersion(ValueError):
    """Engine results from more than one ProjectFacts snapshot."""


@dataclass
class OrchestrationResult:
    report_run_id: str
    projectfacts_snapshot_id: str
    projectfacts_version: str
    engine_versions: dict[str, str]
    sections: list[dict[str, Any]]
    financial_readiness: dict[str, list[dict[str, Any]]]
    cross_engine_conflicts: list[dict[str, Any]] = field(default_factory=list)
    next_steps: list[dict[str, Any]] = field(default_factory=list)
    qa: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""


# ── Next Steps ───────────────────────────────────────────────────────────────

#: How urgently an action needs doing. Ordered, because a sequencer that sorted
#: these strings alphabetically would put "high" after "blocking" and before
#: "low" by luck rather than by meaning.
_URGENCY_ORDER: dict[str, int] = {"blocking": 0, "high": 1, "medium": 2, "low": 3}

#: Actions that unblock other actions, and so come first whatever their own
#: stated urgency. Supplying a qualifying-spend figure is the example the
#: regression makes obvious: until it exists no incentive can be calculated, so
#: every financial action behind it is waiting on this one. An action nobody
#: marked as unblocking is not demoted — it simply is not promoted.
_UNBLOCKING_MARKERS: tuple[str, ...] = (
    "qualifying spend",
    "cost breakdown",
    "premiere",
    "rights",
)


def _is_unblocking(step: Mapping[str, Any]) -> bool:
    text = f"{step.get('action', '')} {step.get('why', '')}".casefold()
    return any(marker in text for marker in _UNBLOCKING_MARKERS)


def sequence_next_steps(
    engine_results: Sequence["EngineResult"],
) -> list[dict[str, Any]]:
    """Order every engine's actions into one plan.

    Section 13 is the only place in the report where cross-engine sequencing
    happens, and it is sequencing rather than deciding: the frozen spec is
    explicit that this section orders actions and does not change any
    underlying eligibility. Nothing here promotes an opportunity, alters a
    state, or adds an action no engine asked for.

    The order is dependency first, then urgency, then the engine's own order.
    Dependency leads because an action that unblocks others is worth doing
    before a more urgent action that cannot proceed without it — a producer
    told to approach three distributors this week, with "supply your qualifying
    spend" ranked below, has been given the list in the wrong order.
    """
    collected: list[tuple[int, int, int, dict[str, Any]]] = []
    for engine_index, result in enumerate(engine_results):
        for step_index, step in enumerate(result.next_steps):
            entry = dict(step, engine=result.engine_name)
            urgency = str(entry.get("urgency") or "medium").strip().casefold()
            collected.append(
                (
                    0 if _is_unblocking(entry) else 1,
                    _URGENCY_ORDER.get(urgency, _URGENCY_ORDER["medium"]),
                    # Engine order then step order, so an engine's own sequence
                    # survives inside its urgency band rather than being
                    # reshuffled by a sort that has nothing left to compare.
                    engine_index * 1000 + step_index,
                    entry,
                )
            )

    collected.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        dict(entry, sequence_position=position)
        for position, (_, _, _, entry) in enumerate(collected, start=1)
    ]


def package_entitlement(package: str) -> int:
    """Display depth. Never a search depth.

    Every package runs the same universe and the same ranking; this truncates
    the result. Five and ten are the same list, which is asserted rather than
    trusted because the two drifting apart would mean a cheaper package
    silently getting different advice rather than less of it.
    """
    return 10 if str(package or "").strip().lower() in {"producer", "studio"} else 5


def assemble(
    *,
    report_run_id: str,
    projectfacts_snapshot_id: str,
    projectfacts_version: str,
    engine_results: Sequence[EngineResult],
    package: str,
    finance_items: Sequence[dict[str, Any]] = (),
    generated_at: str | None = None,
) -> OrchestrationResult:
    """Lay out thirteen sections from canonical engine results.

    Raises ``InconsistentInputVersion`` when any result was computed against a
    different snapshot. Merging them would produce a report where every section
    reads plausibly and only the numbers disagree, which is the hardest kind of
    wrong to notice.
    """
    for result in engine_results:
        if (
            result.projectfacts_snapshot_id != projectfacts_snapshot_id
            or result.projectfacts_version != projectfacts_version
        ):
            raise InconsistentInputVersion(
                f"INCONSISTENT_INPUT_VERSION: {result.engine_name} was computed "
                f"against ProjectFacts {result.projectfacts_snapshot_id}"
                f"/{result.projectfacts_version}, not "
                f"{projectfacts_snapshot_id}/{projectfacts_version}"
            )

    by_engine = {result.engine_name: result for result in engine_results}
    entitlement = package_entitlement(package)

    sections: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for number, key, title, engines in SECTIONS:
        available = [name for name in engines if name in by_engine]
        blocks: list[dict[str, Any]] = []
        reason_codes: list[str] = []
        for name in available:
            result = by_engine[name]
            shown = tuple(result.recommendations[:entitlement])
            blocks.append(
                {
                    "block_type": f"{name}_recommendations",
                    "data": {
                        "recommendations": list(shown),
                        # Both counts, always. "10 shown from 23 eligible" needs
                        # the eligible total, and the list of ten cannot carry it.
                        "eligible_universe_count": result.eligible_universe_count,
                        "displayed_count": len(shown),
                        "package_entitlement": entitlement,
                    },
                    "provenance": ["DATABASE"],
                    "warnings": [],
                }
            )
            reason_codes.extend(result.reason_codes)

        sections.append(
            {
                "section_number": number,
                "section_key": key,
                "title": title,
                # A section with no engine still declares its source, because
                # "orchestrator" is a real answer and an empty list fails the
                # frozen schema's minItems.
                "source_engines": list(engines) or ["orchestrator"],
                "summary": None,
                "blocks": blocks,
                "engine_reason_codes": sorted(set(reason_codes)),
                "owns_no_calculation": key in DOWNSTREAM_SECTIONS,
            }
        )

    checks.append(
        {
            "check": "section_count",
            "status": "PASS" if len(sections) == 13 else "FAIL",
            "detail": f"{len(sections)} sections assembled",
        }
    )
    checks.append(
        {
            "check": "single_projectfacts_snapshot",
            "status": "PASS",
            "detail": f"All {len(engine_results)} engine results share one snapshot",
        }
    )

    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in FINANCE_BUCKETS}
    for item in finance_items:
        bucket = finance_bucket(
            str(item.get("kind") or ""),
            has_documented_award=bool(item.get("has_documented_award")),
        )
        buckets[bucket].append(dict(item))
    checks.append(
        {
            "check": "pipeline_is_not_committed_finance",
            "status": "PASS"
            if all(
                entry.get("has_documented_award")
                for entry in buckets[DOCUMENTED_COMMITTED_FINANCE]
            )
            else "FAIL",
            "detail": (
                f"{len(buckets[DOCUMENTED_COMMITTED_FINANCE])} items in committed "
                f"finance, all with documented award evidence"
            ),
        }
    )

    next_steps = sequence_next_steps(engine_results)

    failed = [check for check in checks if check["status"] == "FAIL"]
    return OrchestrationResult(
        report_run_id=report_run_id,
        projectfacts_snapshot_id=projectfacts_snapshot_id,
        projectfacts_version=projectfacts_version,
        engine_versions={
            result.engine_name: result.engine_version for result in engine_results
        },
        sections=sections,
        financial_readiness=buckets,
        next_steps=next_steps,
        qa={"status": "FAIL" if failed else "PASS", "checks": checks},
        generated_at=generated_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def as_payload(result: OrchestrationResult) -> dict[str, Any]:
    """The result in the frozen schema's shape, for storage or rendering.

    Lives here rather than beside the sample, because the live builder and the
    sample must serialise identically. Two serialisers would mean the payload a
    reviewer approves and the payload a report stores could differ in ways the
    review was meant to catch.
    """
    return {
        "report_run_id": result.report_run_id,
        "projectfacts_snapshot_id": result.projectfacts_snapshot_id,
        "projectfacts_version": result.projectfacts_version,
        "generated_at": result.generated_at,
        "engine_versions": result.engine_versions,
        "sections": result.sections,
        "financial_readiness": result.financial_readiness,
        "cross_engine_conflicts": result.cross_engine_conflicts,
        "next_steps": result.next_steps,
        "qa": result.qa,
    }
