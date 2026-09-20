"""The frozen commercial data stays a traceable staging snapshot."""

import json
from pathlib import Path

from scripts.prepare_commercial_handoff_snapshot import SOURCE_SHA256


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_commercial_snapshot_counts_and_sources():
    path = ROOT / "data/handoff_snapshots/sales_distribution_v1_2026-09-16.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source_sha256"] == SOURCE_SHA256
    assert len(payload["companies"]) == 101
    assert len({row["company_id"] for row in payload["companies"]}) == 101
    assert len(payload["relationships"]) == 205
    assert all(row["source_url"].startswith("https://") for row in payload["companies"])
    assert all(row["source_url"].startswith("https://") for row in payload["relationships"])


def test_frozen_commercial_snapshot_is_not_a_paid_ready_catalogue():
    path = ROOT / "data/handoff_snapshots/sales_distribution_v1_2026-09-16.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    access_states = {row["access_route_status"] for row in payload["companies"]}
    # These raw source values need review/mapping; they must not be silently
    # interpreted as a proven public-acquisitions or open-submissions route.
    assert {"DIRECT_OPEN", "REPRESENTATIVE_ONLY"} <= access_states
    assert any(row["verification_status"] != "VERIFIED" for row in payload["relationships"])
