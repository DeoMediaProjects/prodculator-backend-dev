from dataclasses import replace
from datetime import date

from app.modules.reports.opportunity_strategy import (
    FitSignal,
    HardGate,
    Opportunity,
    build_opportunity_strategy,
    evaluate_opportunity,
)
from app.modules.reports.project_dna import build_project_dna


TODAY = date(2026, 9, 16)
SOURCE = "https://example.org/official-rules"


def candidate(name="Example", *, kind="FESTIVAL", gates=(), **kwargs):
    defaults = dict(
        id=name,
        name=name,
        kind=kind,
        source_url=SOURCE,
        verified_on=TODAY,
        cycle_open=date(2026, 9, 1),
        cycle_deadline=date(2026, 10, 1),
        cycle_verified=True,
        rules_complete=True,
        gates=gates,
    )
    defaults.update(kwargs)
    return Opportunity(**defaults)


def test_known_failure_precedes_fit_score():
    dna = build_project_dna({"format": "short film"}, {})
    item = candidate(
        gates=(HardGate("format", "equals", "feature", SOURCE, "Feature only"),),
        fit_signals=(FitSignal("Genre", 100, SOURCE),),
    )
    result = evaluate_opportunity(item, dna, today=TODAY)
    assert result.eligibility == "INELIGIBLE_CONFIRMED"
    assert result.gate_results == (("Feature only", "FAIL"),)


def test_unknown_premiere_does_not_pass_or_fail():
    dna = build_project_dna({"format": "feature film"}, {})
    item = candidate(
        gates=(
            HardGate("format", "equals", "feature", SOURCE, "Feature only"),
            HardGate("premiere_history", "equals", "world", SOURCE, "World premiere required"),
        )
    )
    result = evaluate_opportunity(item, dna, today=TODAY)
    assert result.eligibility == "POTENTIALLY_ELIGIBLE"
    assert result.conditions_to_confirm == ("World premiere required",)


def test_unstructured_rules_and_missing_cycle_cannot_confirm():
    dna = build_project_dna({"format": "feature film"}, {})
    gate = HardGate("format", "equals", "feature", SOURCE, "Feature only")
    item = candidate(gates=(gate,), rules_complete=False)
    assert evaluate_opportunity(item, dna, today=TODAY).eligibility == "POTENTIALLY_ELIGIBLE"
    assert (
        evaluate_opportunity(replace(item, cycle_deadline=None), dna, today=TODAY).eligibility
        == "NOT_ACTIONABLE"
    )
    assert (
        evaluate_opportunity(replace(item, cycle_verified=False), dna, today=TODAY).eligibility
        == "NOT_ACTIONABLE"
    )
    assert (
        evaluate_opportunity(
            replace(item, verified_on=date(2026, 9, 17)), dna, today=TODAY
        ).eligibility
        == "NOT_ACTIONABLE"
    )
    assert (
        evaluate_opportunity(
            replace(item, cycle_deadline=date(2026, 9, 15)), dna, today=TODAY
        ).eligibility
        == "NOT_ACTIONABLE"
    )


def test_verified_future_window_is_labeled_upcoming_not_closed():
    dna = build_project_dna({"format": "feature film"}, {})
    gate = HardGate("format", "equals", "feature", SOURCE, "Feature only")
    item = candidate(
        gates=(gate,),
        cycle_open=date(2026, 9, 20),
        cycle_deadline=date(2026, 10, 1),
    )
    result = evaluate_opportunity(item, dna, today=TODAY)
    assert result.eligibility == "ELIGIBLE_CONFIRMED"
    assert result.application_status == "UPCOMING"
    strategy = build_opportunity_strategy(
        [item], dna, kind="FESTIVAL", package="single", today=TODAY
    )
    assert strategy.actionable_count == 1
    assert strategy.recommendations == (result,)


def test_missing_rule_source_and_empty_gate_set_are_not_confirmed():
    dna = build_project_dna({"format": "feature film"}, {})
    no_source = candidate(gates=(HardGate("format", "equals", "feature", "", "Feature only"),))
    assert evaluate_opportunity(no_source, dna, today=TODAY).eligibility == "POTENTIALLY_ELIGIBLE"
    assert evaluate_opportunity(candidate(), dna, today=TODAY).eligibility == "POTENTIALLY_ELIGIBLE"


def test_full_universe_ranked_before_five_or_ten_entitlement():
    dna = build_project_dna({"format": "feature film"}, {})
    gate = HardGate("format", "equals", "feature", SOURCE, "Feature only")
    rows = [
        candidate(
            f"confirmed-{i:02}",
            gates=(gate,),
            fit_signals=(FitSignal("Fit", i, SOURCE),),
        )
        for i in range(11)
    ]
    rows += [
        candidate(
            "potential",
            gates=(gate,),
            rules_complete=False,
            fit_signals=(FitSignal("Fit", 1000, SOURCE),),
        )
    ]
    rows += [candidate("other-engine", kind="MARKET_LAB_WIP", gates=(gate,))]
    basic = build_opportunity_strategy(rows, dna, kind="FESTIVAL", package="single", today=TODAY)
    premium = build_opportunity_strategy(
        rows, dna, kind="FESTIVAL", package="producer", today=TODAY
    )
    assert basic.universe_count == premium.universe_count == 12
    assert basic.eligible_count == 11
    assert basic.potential_count == 1
    assert [r.opportunity.name for r in basic.recommendations] == [
        f"confirmed-{i:02}" for i in range(10, 5, -1)
    ]
    assert len(premium.recommendations) == 10
    assert all(r.eligibility == "ELIGIBLE_CONFIRMED" for r in premium.recommendations)


def test_market_stage_unknown_is_potential_and_finance_remains_unknown():
    dna = build_project_dna({"format": "feature film"}, {})
    item = candidate(
        kind="MARKET_LAB_WIP",
        gates=(HardGate("stage", "one_of", ["financing"], SOURCE, "Financing stage"),),
    )
    result = evaluate_opportunity(item, dna, today=TODAY)
    assert result.eligibility == "POTENTIALLY_ELIGIBLE"
    assert dna.get("secured_finance").state == "UNKNOWN"
