"""A worked 13-section orchestration, built from the regression that caused it.

WHY THIS FIXTURE AND NOT A TIDY ONE
-----------------------------------
A sample whose every fact is known proves the happy path and nothing else. The
v2 rebuild exists because of a specific report — a $30m production with every
territory spend field blank that was nonetheless quoted New York, UK and France
rebate amounts, shown a finance market under Grants, and offered distributors no
matcher had returned.

So this fixture is that production. Every behaviour the rebuild added is visible
in the output rather than described in a comment:

- Territory spend is blank, so no section carries a rebate figure. The incentive
  programmes are still named, with what they need in order to be calculated.
- Film London Production Finance Market is claimed by both Grants and Markets,
  and routing sends it to Markets with a conflict record explaining the absence.
- A matched grant and a festival selection sit in pipeline and access buckets;
  committed finance is empty, because nothing here has award paperwork.
- Sales companies carry access routes, and the two with published contacts are
  not described as open.

It runs no matching. Every engine result here is fixture data standing in for a
canonical engine output, which is the point: the orchestrator's behaviour is
independent of what the engines decide, and a sample that called live matchers
would be testing them instead of it.

NOT A PAID REPORT
-----------------
Nothing here is advice and none of it describes a real production's prospects.
It exists so the 13-section flow can be reviewed before a producer sees one.
"""

from __future__ import annotations

from typing import Any

from app.modules.reports.orchestration import (
    EngineResult,
    OrchestrationResult,
    as_payload,
    assemble,
    resolve_routing,
)

__all__ = [
    "SAMPLE_FINANCE_ITEMS",
    "SAMPLE_PROJECT_FACTS",
    "SAMPLE_RUN_ID",
    "SAMPLE_SNAPSHOT_ID",
    "SAMPLE_SNAPSHOT_VERSION",
    "as_payload",
    "build_sample_orchestration",
    "sample_engine_results",
    "sample_routing_conflicts",
]

SAMPLE_RUN_ID = "sample-run-devil-wears-prada"
SAMPLE_SNAPSHOT_ID = "sample-projectfacts-2026-09-18"
SAMPLE_SNAPSHOT_VERSION = "1"

#: The regression's own inputs. Territory spend is absent rather than zero,
#: which is the distinction the whole rebuild turns on: the producer has not
#: told us what they will spend in New York, and that is not the same as telling
#: us they will spend nothing.
SAMPLE_PROJECT_FACTS: dict[str, Any] = {
    "project_title": "The Devil Wears Prada (regression fixture)",
    "facts": {
        "total_budget": {"value": 30_000_000, "source": "USER", "status": "KNOWN"},
        "project_currency": {"value": "USD", "source": "USER", "status": "KNOWN"},
        "territories_considering": {
            "value": ["New York", "United Kingdom", "France"],
            "source": "USER",
            "status": "KNOWN",
        },
        "expected_spend_by_territory": {
            "value": {"New York": None, "United Kingdom": None, "France": None},
            "source": "USER",
            "status": "UNKNOWN",
        },
        "premiere_history": {"value": None, "source": "USER", "status": "UNKNOWN"},
        "rights_status": {"value": None, "source": "USER", "status": "UNKNOWN"},
        "secured_finance": {"value": None, "source": "USER", "status": "UNKNOWN"},
    },
    "script_analysis": {
        "primary_format": {"value": "feature", "source": "SCRIPT", "status": "KNOWN"},
        "script_genres": {
            "value": ["Comedy", "Drama"],
            "source": "SCRIPT",
            "status": "KNOWN",
        },
        "tone": {
            "value": ["Sharp", "Aspirational", "Workplace"],
            "source": "SCRIPT",
            "status": "KNOWN",
        },
        "themes": {
            "value": ["Ambition", "Mentorship", "Self-definition"],
            "source": "SCRIPT",
            "status": "KNOWN",
        },
    },
    "unknown_required_facts": [
        "expected_spend_by_territory",
        "premiere_history",
        "rights_status",
        "secured_finance",
    ],
}

#: The record the regression found in the wrong section. It is deliberately
#: claimed by both engines here, because that is how it arrives in reality — the
#: source data tags it as a funding opportunity — and the orchestrator's routing
#: is what puts it in one place.
_FINANCE_MARKET = {
    "id": "film-london-pfm",
    "name": "Film London Production Finance Market",
}


def _incentive_results() -> tuple[dict[str, Any], ...]:
    """Programmes named, with no amount, because the spend base is unknown.

    This is the fixture's central assertion. A programme with no calculable
    figure is not hidden — a producer needs to know New York's credit exists and
    what it would take to model it — but it carries no number, and nothing
    downstream can total it.
    """
    return (
        {
            "programme": "New York State Film Tax Credit",
            "territory": "New York",
            "engine_state": "unverified",
            "calculation_status": "REQUIRES_COST_BREAKDOWN",
            "amount": None,
            "needed_to_calculate": ["eligible_local_spend"],
            "note": (
                "Qualifying spend for this programme has not been supplied. No "
                "rebate figure can be calculated from the total production budget."
            ),
        },
        {
            "programme": "UK Audio-Visual Expenditure Credit",
            "territory": "United Kingdom",
            "engine_state": "unverified",
            "calculation_status": "REQUIRES_COST_BREAKDOWN",
            "amount": None,
            "needed_to_calculate": [
                "local_core_expenditure",
                "global_core_expenditure",
            ],
            "note": (
                "AVEC is calculated on the lower of UK core expenditure and 80% "
                "of global core expenditure. Neither figure has been supplied."
            ),
        },
        {
            "programme": "France TRIP",
            "territory": "France",
            "engine_state": "unverified",
            "calculation_status": "REQUIRES_COST_BREAKDOWN",
            "amount": None,
            "needed_to_calculate": ["eligible_local_spend"],
            "note": "Qualifying French expenditure has not been supplied.",
        },
    )


def _sales_results() -> tuple[dict[str, Any], ...]:
    """Companies with explicit access routes and no implied buyer intent.

    The two carrying a published contact are described as exactly that. The
    implementation note forbids reading a contact page as permission to submit,
    and this is where that rule becomes visible to a reader.
    """
    return (
        {
            "company": "A specialty distributor",
            "match_state": "POTENTIAL_MATCH_NEEDS_CONFIRMATION",
            "strategic_fit_score": 62,
            "components_known": 6,
            "access_route": "CONTACT_PUBLISHED",
            "access_note": (
                "Acquisitions contact published — this is not confirmation that "
                "unsolicited submissions are accepted"
            ),
        },
        {
            "company": "An international sales agent",
            "match_state": "STRATEGIC_MATCH",
            "strategic_fit_score": 71,
            "components_known": 8,
            "access_route": "DIRECT_OPEN",
            "access_note": "Accepts direct submissions",
        },
        {
            "company": "A territorial distributor",
            "match_state": "ACCESS_ROUTE_UNKNOWN",
            "strategic_fit_score": 55,
            "components_known": 5,
            "access_route": "ACCESS_ROUTE_UNKNOWN",
            "access_note": "Access route not established",
        },
    )


def sample_engine_results() -> list[EngineResult]:
    """Canonical engine outputs for the fixture run, all on one snapshot."""

    def result(name: str, version: str, universe: int, recs, **extra) -> EngineResult:
        return EngineResult(
            engine_name=name,
            engine_version=version,
            projectfacts_snapshot_id=SAMPLE_SNAPSHOT_ID,
            projectfacts_version=SAMPLE_SNAPSHOT_VERSION,
            eligible_universe_count=universe,
            recommendations=tuple(recs),
            **extra,
        )

    grants, markets = _routed_opportunities()

    return [
        result("incentive", "2.0", 3, _incentive_results()),
        result(
            "grants",
            "2.0",
            18,
            grants,
            reason_codes=("GRANT_CYCLE_OPEN", "GRANT_FORMAT_MATCH"),
            next_steps=(
                {
                    "action": "Supply qualifying spend per territory",
                    "urgency": "high",
                    "why": "No incentive figure can be calculated without it",
                },
            ),
        ),
        result(
            "markets_labs_wip",
            "1.0",
            12,
            markets,
            reason_codes=("MARKET_CYCLE_UPCOMING",),
        ),
        result(
            "festivals",
            "2.1",
            41,
            (
                {
                    "festival": "A major autumn festival",
                    "engine_state": "POTENTIALLY_ELIGIBLE",
                    "sequence": "NEEDS_CONFIRMATION",
                    "conditions_to_confirm": ["Premiere history not established"],
                },
            ),
            next_steps=(
                {
                    "action": "Confirm premiere status before submitting",
                    "urgency": "high",
                    "why": "Unknown premiere history is not proof of premiere safety",
                },
            ),
        ),
        result("comparables", "1.0", 9, ()),
        result("sales", "1.0", 27, _sales_results()),
    ]


def _routed_opportunities() -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    """The finance market claimed by two engines, resolved by routing."""
    filtered, _ = resolve_routing(
        {
            "grants": [
                {"id": "bfi-fund", "name": "A national production fund"},
                dict(_FINANCE_MARKET),
            ],
            "markets_labs_wip": [dict(_FINANCE_MARKET)],
        }
    )
    return tuple(filtered["grants"]), tuple(filtered["markets_labs_wip"])


def sample_routing_conflicts() -> list[dict[str, Any]]:
    _, conflicts = resolve_routing(
        {
            "grants": [
                {"id": "bfi-fund", "name": "A national production fund"},
                dict(_FINANCE_MARKET),
            ],
            "markets_labs_wip": [dict(_FINANCE_MARKET)],
        }
    )
    return [
        {
            "subject_id": conflict.subject_id,
            "subject_name": conflict.subject_name,
            "claimed_by": list(conflict.claimed_by),
            "routed_to": conflict.routed_to,
            "suppressed_from": list(conflict.suppressed_from),
            "rule": conflict.rule,
        }
        for conflict in conflicts
    ]


#: Finance items for the readiness buckets. Nothing carries documented award
#: evidence, so committed finance is empty — which is the correct state for a
#: production that has matched opportunities and secured none of them.
SAMPLE_FINANCE_ITEMS: tuple[dict[str, Any], ...] = (
    {"kind": "incentive", "label": "UK AVEC", "amount": None},
    {"kind": "grant_match", "label": "A national production fund", "amount": None},
    {"kind": "market_opportunity", "label": "Film London Production Finance Market"},
    {"kind": "festival_selection", "label": "A major autumn festival"},
    {"kind": "sales_route", "label": "An international sales agent"},
)


def build_sample_orchestration(package: str = "producer") -> OrchestrationResult:
    """One assembled 13-section result for the fixture production."""
    out = assemble(
        report_run_id=SAMPLE_RUN_ID,
        projectfacts_snapshot_id=SAMPLE_SNAPSHOT_ID,
        projectfacts_version=SAMPLE_SNAPSHOT_VERSION,
        engine_results=sample_engine_results(),
        package=package,
        finance_items=SAMPLE_FINANCE_ITEMS,
        generated_at="2026-09-18T00:00:00+00:00",
    )
    out.cross_engine_conflicts = sample_routing_conflicts()
    return out
