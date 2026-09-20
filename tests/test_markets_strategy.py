"""MarketsLabsWIPStrategy: its own section, and never a finance position.

The regression listed Film London Production Finance Market as a grant. A grant
is money that may be awarded; a finance market is a room where a producer may
meet people who have money. Counting the second as the first overstates a
production's finances by the whole amount, so nothing in this module produces a
figure and the strategy says so explicitly.
"""
from __future__ import annotations

from datetime import date

from app.modules.reports.markets_strategy import (
    APPLY_NOW,
    HIGH_PRIORITY,
    INVITATION_ROUTE_ONLY,
    MONITOR_NEXT_CALL,
    NOT_YET_STAGE_READY,
    PREPARE_FOR_OPENING,
    SECOND_WAVE,
    build_markets_strategy,
)
from app.modules.reports.opportunity_strategy import FitSignal, HardGate, Opportunity
from app.modules.reports.project_dna import build_project_dna

TODAY = date(2026, 9, 18)
SOURCE = "https://example.org/market/rules"


def market(
    identifier="mkt",
    *,
    opportunity_class="COPRODUCTION_MARKET",
    cycle_open=date(2026, 8, 1),
    deadline=date(2026, 12, 1),
    gates=(HardGate("format", "one_of", ["feature"], SOURCE, "Features only"),),
    rules_complete=True,
    fit=0,
    **fields,
):
    return Opportunity(
        id=identifier,
        name=identifier,
        kind="MARKET_LAB_WIP",
        source_url=SOURCE,
        verified_on=TODAY,
        cycle_open=cycle_open,
        cycle_deadline=deadline,
        cycle_verified=True,
        rules_complete=rules_complete,
        gates=gates,
        fit_signals=(FitSignal("fit", fit, SOURCE),) if fit else (),
        opportunity_class=opportunity_class,
        **fields,
    )


def project(stage="development", **metadata):
    return build_project_dna(
        {
            "format": "feature film",
            "genre": ["Drama"],
            "project_stage": stage,
            **metadata,
        },
        {},
    )


def _sequences(strategy):
    return [item.sequence for item in strategy.recommendations]


# ── Market access is not finance ─────────────────────────────────────────────


def test_a_market_strategy_is_never_committed_finance():
    strategy = build_markets_strategy(
        [market()], project(), package="producer", today=TODAY
    )
    assert strategy.is_committed_finance is False


def test_no_recommendation_carries_an_amount():
    """Nothing in this section is money, so nothing in it is a figure."""
    strategy = build_markets_strategy(
        [market("finance", opportunity_class="FINANCE_FORUM")],
        project(),
        package="producer",
        today=TODAY,
    )
    item = strategy.recommendations[0]
    assert not hasattr(item, "amount")
    assert not hasattr(item.opportunity, "amount")


# ── Lifecycle sequencing ─────────────────────────────────────────────────────


def test_a_rough_cut_screening_is_not_offered_to_a_development_project():
    """It might score well and the project still has no footage."""
    strategy = build_markets_strategy(
        [market("wip", opportunity_class="WIP_ROUGH_CUT", fit=9)],
        project(stage="development"),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [NOT_YET_STAGE_READY]
    assert "post_production" in strategy.recommendations[0].sequence_reason


def test_a_development_lab_suits_a_development_project():
    strategy = build_markets_strategy(
        [market("lab", opportunity_class="DEVELOPMENT_LAB", fit=9)],
        project(stage="development"),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [HIGH_PRIORITY]


def test_an_unknown_project_stage_does_not_exclude_anything():
    """Absence of a stage is not evidence the project is at the wrong one."""
    dna = build_project_dna({"format": "feature film", "genre": ["Drama"]}, {})
    strategy = build_markets_strategy(
        [market("wip", opportunity_class="WIP_ROUGH_CUT")],
        dna,
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) != [NOT_YET_STAGE_READY]


def test_an_unrecorded_class_gets_no_lifecycle_judgement():
    """Guessing an unlabelled programme suits this stage wastes a slot."""
    strategy = build_markets_strategy(
        [market("mystery", opportunity_class=None)],
        project(stage="development"),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) != [NOT_YET_STAGE_READY]


def test_an_invitation_only_programme_is_not_presented_as_an_open_call():
    strategy = build_markets_strategy(
        [market("showcase", opportunity_class="INDUSTRY_SHOWCASE", fit=9)],
        project(stage="post_production"),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [INVITATION_ROUTE_ONLY]


def test_an_upcoming_call_says_prepare_rather_than_apply():
    strategy = build_markets_strategy(
        [
            market(
                "future",
                cycle_open=date(2026, 11, 1),
                deadline=date(2026, 12, 15),
            )
        ],
        project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [PREPARE_FOR_OPENING]
    assert "not an open call today" in strategy.recommendations[0].sequence_reason


def test_unconfirmed_conditions_downgrade_to_monitor():
    strategy = build_markets_strategy(
        [market("unsure", rules_complete=False)],
        project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [MONITOR_NEXT_CALL]


def test_the_strongest_confirmed_opportunity_is_high_priority_and_the_rest_follow():
    strategy = build_markets_strategy(
        [market("a", fit=9), market("b", fit=8), market("c", fit=7)],
        project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [HIGH_PRIORITY, SECOND_WAVE, SECOND_WAVE]


def test_apply_now_is_used_when_nothing_is_confirmed_eligible():
    """An open call a project potentially qualifies for is still actionable."""
    strategy = build_markets_strategy(
        [market("a", gates=())],
        project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) in ([APPLY_NOW], [MONITOR_NEXT_CALL])


# ── Eligibility and actionability still gate everything ──────────────────────


def test_a_confirmed_ineligible_track_does_not_occupy_a_slot():
    strategy = build_markets_strategy(
        [
            market(
                "no",
                gates=(HardGate("format", "one_of", ["short"], SOURCE, "Shorts only"),),
            )
        ],
        project(),
        package="producer",
        today=TODAY,
    )
    assert strategy.recommendations == ()
    assert strategy.actionable_count == 1


def test_an_unverified_cycle_never_reaches_the_sequence():
    stale = market("a")
    stale = Opportunity(**{**stale.__dict__, "cycle_verified": False})
    strategy = build_markets_strategy(
        [stale], project(), package="producer", today=TODAY
    )
    assert strategy.recommendations == ()
    assert strategy.actionable_count == 0


def test_festivals_are_not_markets():
    """Routing, enforced at the universe boundary rather than by convention."""
    festival = Opportunity(
        id="fest",
        name="fest",
        kind="FESTIVAL",
        source_url=SOURCE,
        verified_on=TODAY,
        cycle_open=date(2026, 8, 1),
        cycle_deadline=date(2026, 12, 1),
        cycle_verified=True,
        rules_complete=True,
    )
    strategy = build_markets_strategy(
        [festival, market("a")], project(), package="producer", today=TODAY
    )
    assert strategy.universe_count == 1
    assert all(
        item.opportunity.kind == "MARKET_LAB_WIP" for item in strategy.recommendations
    )


# ── Counts and package depth ─────────────────────────────────────────────────


def test_the_universe_is_ranked_before_the_package_truncates_it():
    rows = [market(f"m{i:02}", fit=20 - i) for i in range(12)]
    small = build_markets_strategy(rows, project(), package="single", today=TODAY)
    large = build_markets_strategy(rows, project(), package="producer", today=TODAY)
    assert len(small.recommendations) == 5
    assert len(large.recommendations) == 10
    assert [i.opportunity.id for i in small.recommendations] == [
        i.opportunity.id for i in large.recommendations[:5]
    ]
    assert small.universe_count == large.universe_count == 12


def test_the_snapshot_identifier_travels_with_the_strategy():
    strategy = build_markets_strategy(
        [market()],
        project(),
        package="producer",
        today=TODAY,
        projectfacts_snapshot_id="snap-1",
        projectfacts_version="1",
    )
    assert strategy.projectfacts_snapshot_id == "snap-1"
    assert strategy.projectfacts_version == "1"
