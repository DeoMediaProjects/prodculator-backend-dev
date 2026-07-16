"""Parity tests for the festival/distributor matcher.

These are the B2C handoff pack's smoke tests (festival_distributor_matcher.py
Tests 0-5) folded into pytest and pointed at the REPO's engine
(app.modules.reports.matching) instead of the pack copy — proving the ported
module behaves identically to the delivered reference, including the
baseline-unchanged regression (Test 5).

Datasets: tests/data/*.json are byte-for-byte copies of the handoff pack's
canonical festivals (177) / distributors (57) files.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.modules.reports.matching import (
    estimate_completion_date,
    festival_window,
    match_distributors,
    match_festivals,
)

DATA_DIR = Path(__file__).resolve().parent / "data"


def _records(filename: str) -> list[dict]:
    payload = json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))
    return payload["records"]


@pytest.fixture(scope="module")
def festivals() -> list[dict]:
    return _records("festivals_database.json")


@pytest.fixture(scope="module")
def distributors() -> list[dict]:
    return _records("distributors_database.json")


def test_dataset_counts_match_handoff(festivals, distributors):
    assert len(festivals) == 177
    assert len(distributors) == 57


def test_completion_date_and_window_spec():
    """Pack Test 0: Jan 1 start + 6 weeks duration; window opens 6mo after
    completion and stays open 12mo."""
    completion, shoot_end, source = estimate_completion_date(
        date(2026, 1, 1), filming_duration_weeks=6
    )
    assert shoot_end == date(2026, 2, 12)
    assert "user-declared" in source
    w_start, w_end = festival_window(completion)
    assert w_start > completion
    # ~6 months after completion, ~12 months long (30.44-day months)
    assert 175 <= (w_start - completion).days <= 190
    assert 355 <= (w_end - w_start).days <= 375


def test_festivals_match_without_representation(festivals):
    """Pack Test 1: baseline drama/crime feature, no representation opt-in."""
    matches = match_festivals(
        festivals,
        genres=["drama", "crime"],
        representation_gender=None,
        representation_minority=[],
        production_format="feature",
        completion_date=date(2026, 3, 1),
        comparable_production_festivals=["London Film Festival (BFI LFF)"],
    )
    assert matches, "baseline production must match festivals"
    # No representation-targeted festival may surface without the opt-in
    for m in matches:
        assert m.festival.get("representation_focus", ["general"]) == ["general"] or any(
            "target audience" in r.lower() for r in m.reasons
        ), f"{m.festival['name']} surfaced without opt-in or declared audience"
    # Comparable boost applied
    lff = [m for m in matches if "London Film Festival" in m.festival["name"]]
    assert lff and any("comparable" in r.lower() for r in lff[0].reasons)


def test_representation_opt_in_surfaces_targeted_festivals(festivals):
    """Pack Test 2: Woman + LGBTQ+ opt-in surfaces representation festivals."""
    matches = match_festivals(
        festivals,
        genres=["drama", "crime"],
        representation_gender="Woman",
        representation_minority=["LGBTQ+"],
        production_format="feature",
        completion_date=date(2026, 3, 1),
    )
    assert any(
        m.festival.get("representation_focus", ["general"]) != ["general"] for m in matches
    ), "representation opt-in should surface targeted festivals"


def test_distributor_scouting_linkage(festivals, distributors):
    """Pack Test 3: distributors matched AFTER festivals get the scouts boost."""
    fest_matches = match_festivals(
        festivals,
        genres=["drama", "crime"],
        representation_gender="Woman",
        representation_minority=["LGBTQ+"],
        production_format="feature",
        completion_date=date(2026, 3, 1),
    )
    matched_names = [m.festival["name"] for m in fest_matches]
    dist_matches = match_distributors(
        distributors,
        genres=["drama", "crime"],
        representation_gender="Woman",
        representation_minority=["LGBTQ+"],
        matched_festival_names=matched_names,
        budget_tier="mid_indie",
    )
    assert dist_matches
    assert any(
        "scouts" in r.lower() for m in dist_matches for r in m.reasons
    ), "festival-scouting linkage (+4) missing — distributors must run after festivals"


def test_declared_audience_without_representation(festivals, distributors):
    """Pack Test 4: declared LGBTQ+ TARGET AUDIENCE (no representation opt-in)
    must surface Frameline/Outfest and boost Breaking Glass."""
    fest_matches = match_festivals(
        festivals,
        genres=["drama", "crime"],
        representation_gender=None,
        representation_minority=[],
        production_format="feature",
        completion_date=date(2026, 3, 1),
        target_audience=["adults_25_plus"],
        audience_segments=["lgbtq_audience"],
    )
    names = [m.festival["name"] for m in fest_matches]
    assert any("Frameline" in n for n in names), "Frameline not surfaced by declared audience"
    assert any("Outfest" in n for n in names), "Outfest not surfaced by declared audience"

    dist_matches = match_distributors(
        distributors,
        genres=["drama", "crime"],
        representation_gender=None,
        representation_minority=[],
        matched_festival_names=names,
        budget_tier="mid_indie",
        target_audience=["adults_25_plus"],
        audience_segments=["lgbtq_audience"],
        audience_skew="female_leaning",
    )
    bg = [m for m in dist_matches if m.distributor["name"] == "Breaking Glass Pictures"]
    assert bg and any(
        "audience you declared" in r for r in bg[0].reasons
    ), "Breaking Glass declared-audience boost missing"


def test_baseline_regression_audience_fields_change_nothing_when_absent(festivals):
    """Pack Test 5: results with NO audience declared must be IDENTICAL to the
    baseline — adding the audience feature must not shift existing scores."""
    kwargs = dict(
        genres=["drama", "crime"],
        representation_gender=None,
        representation_minority=[],
        production_format="feature",
        completion_date=date(2026, 3, 1),
        comparable_production_festivals=["London Film Festival (BFI LFF)"],
    )
    baseline = match_festivals(festivals, **kwargs)
    with_empty_audience = match_festivals(
        festivals, **kwargs, target_audience=[], audience_segments=[]
    )
    assert [(m.festival["name"], m.score) for m in baseline] == [
        (m.festival["name"], m.score) for m in with_empty_audience
    ], "regression: audience plumbing altered baseline results"
