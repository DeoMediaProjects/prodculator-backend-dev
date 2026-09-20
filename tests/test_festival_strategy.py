"""FestivalStrategy: a premiere-aware sequence, not a ranked list.

The rule these guard inverts the intuitive default. A production whose premiere
history nobody has recorded looks, to a naive ranker, exactly like one with a
clean premiere record — both have nothing on file. Telling the first to submit
first is how a producer is sent to a festival already closed to them.
"""
from __future__ import annotations

from datetime import date

from app.modules.reports.festival_strategy import (
    HOLD_UNTIL_DECISION,
    NEEDS_CONFIRMATION,
    NOT_ELIGIBLE,
    PARALLEL_SAFE,
    SECOND_WAVE,
    SUBMIT_FIRST,
    build_festival_strategy,
)
from app.modules.reports.opportunity_strategy import HardGate, Opportunity
from app.modules.reports.project_dna import ProjectFact, build_project_dna

TODAY = date(2026, 9, 18)
SOURCE = "https://example.org/festival/rules"


def festival(
    identifier="fest",
    *,
    premiere=None,
    deadline=date(2026, 12, 1),
    gates=(),
    rules_complete=True,
    fit=0,
    **fields,
):
    from app.modules.reports.opportunity_strategy import FitSignal

    return Opportunity(
        id=identifier,
        name=identifier,
        kind="FESTIVAL",
        source_url=SOURCE,
        verified_on=TODAY,
        cycle_open=date(2026, 8, 1),
        cycle_deadline=deadline,
        cycle_verified=True,
        rules_complete=rules_complete,
        gates=gates,
        fit_signals=(FitSignal("fit", fit, SOURCE),) if fit else (),
        premiere_requirement=premiere,
        **fields,
    )


def project(**metadata):
    return build_project_dna(
        {"format": "feature film", "genre": ["Drama"], **metadata}, {}
    )


def confirmed_premiere_project(**metadata):
    """A production whose premiere position is actually established.

    Built by hand rather than through the intake, because the intake does not
    gather these facts and must not start: the implementation note forbids
    adding an engine-specific question to the user journey. Until they arrive
    from a legitimate source, every premiere-requiring festival sequences as
    NEEDS_CONFIRMATION in the live report — which is the honest answer, and is
    covered by the tests above.
    """
    dna = project(**metadata)
    facts = dict(dna.facts)
    facts["premiere_history"] = ProjectFact(
        "none", "producer_confirmed", None, confirmation_required=False
    )
    facts["public_online_availability"] = ProjectFact(
        "no", "producer_confirmed", None, confirmation_required=False
    )
    return dna.__class__(facts)


def _sequences(strategy):
    return [item.sequence for item in strategy.recommendations]


# ── Unknown premiere history suspends the sequence ───────────────────────────


def test_unknown_premiere_history_yields_needs_confirmation():
    """The Devil Wears Prada case. Nothing on file is not a clean record."""
    strategy = build_festival_strategy(
        [festival("a", premiere="WORLD")], project(), package="producer", today=TODAY
    )
    assert _sequences(strategy) == [NEEDS_CONFIRMATION]
    assert strategy.needs_premiere_confirmation


def test_the_reason_says_what_the_producer_must_establish():
    strategy = build_festival_strategy(
        [festival("a", premiere="WORLD")], project(), package="producer", today=TODAY
    )
    reason = strategy.recommendations[0].sequence_reason
    assert "not evidence that premiere status is intact" in reason


def test_unknown_premiere_history_never_produces_a_submit_first():
    strategy = build_festival_strategy(
        [
            festival("a", premiere="WORLD", fit=9),
            festival("b", premiere="INTERNATIONAL", fit=8),
        ],
        project(),
        package="producer",
        today=TODAY,
    )
    assert SUBMIT_FIRST not in _sequences(strategy)


def test_a_fact_needing_confirmation_counts_as_unknown():
    """It may well be true, and nobody has confirmed it."""
    dna = project(premiere_history="none")
    strategy = build_festival_strategy(
        [festival("a", premiere="WORLD")], dna, package="producer", today=TODAY
    )
    assert _sequences(strategy) == [NEEDS_CONFIRMATION]


# ── A known premiere position produces a real order ──────────────────────────


def test_the_strongest_premiere_festival_is_submitted_first():
    strategy = build_festival_strategy(
        [festival("a", premiere="WORLD", fit=9)],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [SUBMIT_FIRST]


def test_a_competing_premiere_festival_is_held_until_the_decision():
    """A world premiere happens once; two submissions are a conflict."""
    strategy = build_festival_strategy(
        [
            festival("a", premiere="WORLD", fit=9),
            festival("b", premiere="WORLD", fit=8),
        ],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [SUBMIT_FIRST, HOLD_UNTIL_DECISION]


def test_a_national_premiere_also_conflicts():
    strategy = build_festival_strategy(
        [
            festival("a", premiere="NATIONAL", fit=9),
            festival("b", premiere="NATIONAL", fit=8),
        ],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [SUBMIT_FIRST, HOLD_UNTIL_DECISION]


def test_a_festival_stating_no_requirement_is_parallel_safe():
    strategy = build_festival_strategy(
        [festival("a", premiere="NONE", fit=9)],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [PARALLEL_SAFE]


def test_a_parallel_safe_festival_below_a_premiere_claim_is_second_wave():
    strategy = build_festival_strategy(
        [
            festival("a", premiere="WORLD", fit=9),
            festival("b", premiere="NONE", fit=8),
        ],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [SUBMIT_FIRST, SECOND_WAVE]


def test_a_parallel_safe_festival_above_a_premiere_claim_stays_parallel():
    strategy = build_festival_strategy(
        [
            festival("a", premiere="NONE", fit=9),
            festival("b", premiere="WORLD", fit=8),
        ],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [PARALLEL_SAFE, SUBMIT_FIRST]


# ── Untyped requirements are never parsed ────────────────────────────────────


def test_prose_is_not_a_premiere_requirement():
    """34 of 380 frozen records carry prose here; none of it is a rule."""
    strategy = build_festival_strategy(
        [festival("a", premiere="Swedish premiere required. Film must not...")],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [NEEDS_CONFIRMATION]


def test_an_absent_requirement_is_not_read_as_no_requirement():
    """346 of 380 records have nothing recorded, which is not the same as NONE."""
    strategy = build_festival_strategy(
        [festival("a", premiere=None)],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
    )
    assert _sequences(strategy) == [NEEDS_CONFIRMATION]


# ── Eligibility still outranks sequence ──────────────────────────────────────


def test_a_confirmed_ineligible_festival_does_not_occupy_a_slot():
    """It is not a recommendation, so it is not in the recommendations."""
    ineligible = festival(
        "a",
        premiere="NONE",
        gates=(HardGate("format", "one_of", ["short"], SOURCE, "Shorts only"),),
    )
    strategy = build_festival_strategy(
        [ineligible], confirmed_premiere_project(), package="producer", today=TODAY
    )
    assert strategy.recommendations == ()
    assert strategy.actionable_count == 1
    assert strategy.eligible_count == 0


def test_the_sequencer_labels_an_ineligible_festival_not_eligible():
    """The contract's vocabulary covers it, for any caller that shows one."""
    from app.modules.reports.festival_strategy import sequence_festivals
    from app.modules.reports.opportunity_strategy import evaluate_opportunity

    dna = confirmed_premiere_project()
    ineligible = festival(
        "a",
        premiere="NONE",
        gates=(HardGate("format", "one_of", ["short"], SOURCE, "Shorts only"),),
    )
    evaluated = [evaluate_opportunity(ineligible, dna, today=TODAY)]
    assert [item.sequence for item in sequence_festivals(evaluated, dna)] == [
        NOT_ELIGIBLE
    ]


def test_an_unverified_cycle_never_reaches_the_sequence_at_all():
    stale = festival("a", premiere="NONE")
    stale = Opportunity(**{**stale.__dict__, "cycle_verified": False})
    strategy = build_festival_strategy(
        [stale], confirmed_premiere_project(), package="producer", today=TODAY
    )
    assert strategy.recommendations == ()
    assert strategy.actionable_count == 0


# ── Counts and package depth ─────────────────────────────────────────────────


def test_sections_of_one_festival_take_one_slot():
    strategy = build_festival_strategy(
        [
            festival("a-feature", premiere="NONE", fit=9, record_id="fest-a"),
            festival("a-short", premiere="NONE", fit=8, record_id="fest-a"),
        ],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
    )
    assert len(strategy.recommendations) == 1


def test_the_universe_is_sequenced_before_the_package_truncates_it():
    """The plan a producer sees is the top of one order, not an order over five."""
    rows = [festival(f"f{i:02}", premiere="NONE", fit=20 - i) for i in range(12)]
    small = build_festival_strategy(
        rows, confirmed_premiere_project(), package="single", today=TODAY
    )
    large = build_festival_strategy(
        rows, confirmed_premiere_project(), package="producer", today=TODAY
    )
    assert len(small.recommendations) == 5
    assert len(large.recommendations) == 10
    assert [i.opportunity.id for i in small.recommendations] == [
        i.opportunity.id for i in large.recommendations[:5]
    ]
    assert small.universe_count == large.universe_count == 12


def test_the_snapshot_identifier_travels_with_the_strategy():
    strategy = build_festival_strategy(
        [festival("a", premiere="NONE")],
        confirmed_premiere_project(),
        package="producer",
        today=TODAY,
        projectfacts_snapshot_id="snap-1",
        projectfacts_version="1",
    )
    assert strategy.projectfacts_snapshot_id == "snap-1"
    assert strategy.projectfacts_version == "1"
