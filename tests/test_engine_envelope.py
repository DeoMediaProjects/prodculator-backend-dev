"""One provenance envelope, and a consistency check that cannot be outgrown.

The check this replaces looked for four named keys. That is a convention, and
the failure mode of a convention is the next engine: someone adds a result to
the report, forgets to add its key to the list, and the check goes on passing
while no longer covering the thing it exists for. These tests are mostly about
the keys nobody has written yet.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.modules.reports.engine_envelope import (
    InconsistentInputVersion,
    assert_consistent,
    assert_strategies_consistent,
    engine_results,
    looks_like_an_engine_result,
    stamped,
)


@dataclass(frozen=True)
class _Snapshot:
    snapshot_id: str = "snap-a"
    version: str = "1"


def _report(**extra):
    base = {
        "projectFactsSnapshotId": "snap-a",
        "projectFactsVersion": "1",
    }
    base.update(extra)
    return base


def _result(snapshot_id="snap-a", version="1", **extra):
    base = {
        "recommendations": [],
        "projectfacts_snapshot_id": snapshot_id,
        "projectfacts_version": version,
    }
    base.update(extra)
    return base


# ── Stamping ─────────────────────────────────────────────────────────────────


def test_stamping_writes_every_provenance_field():
    result = stamped(
        {"recommendations": []}, _Snapshot(), engine_name="grants", engine_version="2.0"
    )
    assert result["projectfacts_snapshot_id"] == "snap-a"
    assert result["projectfacts_version"] == "1"
    assert result["engine_name"] == "grants"
    assert result["engine_version"] == "2.0"


def test_stamping_does_not_mutate_the_payload_it_was_given():
    payload = {"recommendations": []}
    stamped(payload, _Snapshot(), engine_name="grants", engine_version="2.0")
    assert "projectfacts_snapshot_id" not in payload


def test_a_stamped_result_passes_the_check():
    report = _report(
        grantsPayload=stamped(
            {"recommendations": []},
            _Snapshot(),
            engine_name="grants",
            engine_version="2.0",
        )
    )
    assert_consistent(report)


# ── Recognition by shape, not by name ────────────────────────────────────────


def test_an_engine_nobody_listed_is_still_checked():
    """The whole point. A key written next year is covered on the day."""
    report = _report(someFutureEngineStrategy=_result(snapshot_id="snap-b"))
    with pytest.raises(InconsistentInputVersion, match="someFutureEngineStrategy"):
        assert_consistent(report)


def test_a_universe_count_alone_identifies_an_engine_result():
    """An engine that returned nothing this run has still run."""
    assert looks_like_an_engine_result("x", {"eligible_universe_count": 0})


def test_recommendations_alone_identify_an_engine_result():
    assert looks_like_an_engine_result("x", {"recommendations": []})


def test_report_metadata_is_not_mistaken_for_an_engine_result():
    assert not looks_like_an_engine_result("projectFactsSnapshot", {"facts": {}})
    assert not looks_like_an_engine_result("executiveSummary", {"format": "feature"})


def test_a_plain_value_is_not_an_engine_result():
    assert not looks_like_an_engine_result("genre", "Drama")
    assert not looks_like_an_engine_result("score", 88)
    assert not looks_like_an_engine_result("rankings", [{"recommendations": []}])


def test_engine_results_finds_every_one_of_them():
    report = _report(grantsPayload=_result(), festivalStrategy=_result())
    assert {key for key, _ in engine_results(report)} == {
        "grantsPayload",
        "festivalStrategy",
    }


# ── What the check refuses ───────────────────────────────────────────────────


def test_a_different_snapshot_is_refused():
    with pytest.raises(InconsistentInputVersion, match="different ProjectFacts snapshot"):
        assert_consistent(_report(grantsPayload=_result(snapshot_id="snap-b")))


def test_a_different_version_is_refused():
    with pytest.raises(InconsistentInputVersion, match="different ProjectFacts version"):
        assert_consistent(_report(grantsPayload=_result(version="2")))


def test_an_unstamped_result_in_a_snapshot_bearing_report_is_refused():
    report = _report(grantsPayload={"recommendations": []})
    with pytest.raises(InconsistentInputVersion, match="lacks ProjectFacts provenance"):
        assert_consistent(report)


def test_a_legacy_report_with_no_snapshot_is_left_alone():
    """Reports predating the snapshot still validate; they are not retrofitted."""
    assert_consistent({"grantsPayload": {"recommendations": []}})


def test_a_report_with_no_engine_results_passes():
    assert_consistent(_report())


# ── Strategy objects ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Strategy:
    projectfacts_snapshot_id: str | None = "snap-a"
    projectfacts_version: str | None = "1"


def test_matching_strategies_pass():
    assert_strategies_consistent(
        [_Strategy(), _Strategy()], snapshot_id="snap-a", version="1"
    )


def test_a_strategy_from_another_snapshot_is_refused():
    with pytest.raises(InconsistentInputVersion, match="_Strategy"):
        assert_strategies_consistent(
            [_Strategy(projectfacts_snapshot_id="snap-b")],
            snapshot_id="snap-a",
            version="1",
        )


def test_an_untagged_strategy_is_refused_rather_than_assumed():
    """An untagged result is the one whose origin nobody can establish."""
    with pytest.raises(InconsistentInputVersion, match="carries no ProjectFacts"):
        assert_strategies_consistent(
            [_Strategy(projectfacts_snapshot_id=None)],
            snapshot_id="snap-a",
            version="1",
        )


def test_an_absent_strategy_is_skipped():
    """An engine that did not run is not an engine that ran against bad facts."""
    assert_strategies_consistent([None], snapshot_id="snap-a", version="1")


# ── Every canonical strategy object can carry provenance ─────────────────────


def test_each_canonical_strategy_accepts_the_envelope_fields():
    from app.modules.reports.commercial_strategy import (
        ComparableStrategy,
        SalesDistributionStrategy,
    )
    from app.modules.reports.festival_strategy import FestivalStrategy
    from app.modules.reports.markets_strategy import MarketsLabsWIPStrategy

    strategies = [
        ComparableStrategy(0, (), "snap-a", "1"),
        SalesDistributionStrategy(0, 0, (), "snap-a", "1"),
        FestivalStrategy(0, 0, 0, 0, (), "snap-a", "1"),
        MarketsLabsWIPStrategy(0, 0, 0, 0, (), "snap-a", "1"),
    ]
    assert_strategies_consistent(strategies, snapshot_id="snap-a", version="1")


def test_a_canonical_strategy_defaults_to_untagged_rather_than_to_this_run():
    from app.modules.reports.festival_strategy import FestivalStrategy

    with pytest.raises(InconsistentInputVersion):
        assert_strategies_consistent(
            [FestivalStrategy(0, 0, 0, 0, ())], snapshot_id="snap-a", version="1"
        )
