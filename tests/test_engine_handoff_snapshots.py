"""Handoff snapshot checks before any database cutover or engine wiring."""

from __future__ import annotations

import json
import runpy
import uuid
from pathlib import Path

import pytest

from scripts.prepare_engine_handoff_snapshots import _bool


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data" / "handoff_snapshots"


def _rows(name: str) -> list[dict]:
    return json.loads((SNAPSHOTS / name).read_text(encoding="utf-8"))


def test_festival_snapshot_is_distinct_from_current_paid_universe():
    rows = _rows("festivals_v2_1_2026-09-15.json")
    paid = [row for row in rows if row["paid_match_eligible_v2"]]
    assert len(rows) == 380
    assert len({row["id"] for row in rows}) == 380
    assert len(paid) == 76
    assert all(
        row["routing"] == "FESTIVAL"
        and row["identity_verified"]
        and row["current_cycle_verified"]
        and row["eligibility_verified"]
        for row in paid
    )
    assert any(row["routing"] == "MARKETS_LABS_WIP" for row in rows)
    assert any(row["record_verification_status"] == "ARCHIVED" for row in rows)


def test_festival_snapshot_preserves_all_legacy_seed_ids():
    legacy = runpy.run_path(
        str(ROOT / "alembic" / "versions" / "ad5e6f708192_festivals_v2_refresh.py")
    )["_SOURCE_ROWS"]
    legacy_ids = {
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"prodculator:festival:{row['name']}")) for row in legacy
    }
    new_ids = {row["id"] for row in _rows("festivals_v2_1_2026-09-15.json")}
    assert len(legacy_ids) == 177
    assert legacy_ids <= new_ids


def test_paid_festival_snapshot_is_not_a_structured_cycle_feed():
    paid = [row for row in _rows("festivals_v2_1_2026-09-15.json") if row["paid_match_eligible_v2"]]
    # The static handoff flag must not silently become a runtime paid feed.
    # A prose deadline cannot safely supply a section-specific date boundary.
    assert all(row.get("deadlines") is None for row in paid)
    assert all(not isinstance(row.get("eligible_formats"), list) for row in paid)


def test_markets_snapshot_has_complete_paid_safe_provenance():
    rows = _rows("markets_labs_wip_v1_2026-09-16.json")
    paid = [row for row in rows if row["paid_safe"]]
    assert len(rows) == 203
    assert len({row["id"] for row in rows}) == 203
    assert len(paid) == 43
    assert all(row["status"] in {"OPEN", "UPCOMING"} for row in paid)
    assert all(row["source"] and row["verified_on"] for row in paid)
    assert all(isinstance(row["paid_safe"], bool) for row in rows)


def test_markets_hard_gates_are_not_machine_readable_yet():
    rows = _rows("markets_labs_wip_v1_2026-09-16.json")
    assert all(isinstance(row["hard_gates"], str) for row in rows)
    assert all(not isinstance(row["hard_gates"], list) for row in rows)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("TRUE", True), ("FALSE", False)],
)
def test_excel_paid_safe_normalisation(value, expected):
    assert _bool(value) is expected


def test_excel_paid_safe_rejects_ambiguous_values():
    with pytest.raises(ValueError):
        _bool(None)
