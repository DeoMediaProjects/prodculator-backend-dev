"""Validate and snapshot the frozen Grants v2 master and its migration maps.

Usage::

    python scripts/prepare_grants_handoff_snapshot.py PATH_TO_CORRECT_FILES

``PATH_TO_CORRECT_FILES`` is the handoff root holding ``01_DATA`` and
``02_MIGRATION``. Nothing is written to any database and no live grant row is
read; this produces one reviewed JSON snapshot for the reconciliation step to
work against.

The validation here is deliberately loud about what the source does NOT settle.
A snapshot that quietly normalised the master's duplicate canonical titles, or
dropped the record still routed to the Incentive Engine, would present a
migration as ready when a human has not yet ruled on those rows. Every such gap
is counted and carried into the snapshot so the reconciliation report can name
it, which is the difference between a dry run and a rehearsal of a decision
nobody made.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "handoff_snapshots" / "grants_v2_2026-09-04.json"

#: The reviewed freeze. A changed master is a different migration and must be
#: reviewed again rather than snapshotted over the top of this one.
MASTER_SHA256 = "7c5bc7576aae435fbcc6926d7cb9997e8dcf0c9edf5a02ab84dfb89a95091e1a"

MASTER_RELATIVE = Path("01_DATA") / "prodculator_grants_master_v2.json"
MIGRATION_RELATIVE = Path("02_MIGRATION")

#: The five maps the handoff's migration instructions name, and the row count
#: each held at the freeze. Counted rather than trusted: a map that gained or
#: lost rows since review changes which live records survive.
EXPECTED_MAP_ROWS: dict[str, int] = {
    "legacy_id_mapping": 88,
    "reclassified_records": 19,
    "archived_records": 23,
    "split_parent_records": 32,
    "labs_routed": 5,
}

EXPECTED_RECORD_COUNT = 253
EXPECTED_PAID_MATCH_ELIGIBLE = 204


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_handoff(root: Path) -> dict:
    master_path = root / MASTER_RELATIVE
    digest = hashlib.sha256(master_path.read_bytes()).hexdigest()
    if digest != MASTER_SHA256:
        raise ValueError("Grants master SHA-256 differs from the reviewed freeze")

    master = json.loads(master_path.read_text(encoding="utf-8"))
    records = master.get("records") or []
    if len(records) != EXPECTED_RECORD_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_RECORD_COUNT} frozen grant records, "
            f"found {len(records)}"
        )
    if master.get("record_count") != EXPECTED_RECORD_COUNT:
        raise ValueError("Master record_count disagrees with its own records")

    paid = sum(
        1 for row in records if str(row.get("paid_match_eligible")).lower() == "true"
    )
    if paid != EXPECTED_PAID_MATCH_ELIGIBLE:
        raise ValueError(
            f"Expected {EXPECTED_PAID_MATCH_ELIGIBLE} paid-match-eligible records, "
            f"found {paid}"
        )

    maps: dict[str, list[dict]] = {}
    for name, expected in EXPECTED_MAP_ROWS.items():
        rows = _read_csv(root / MIGRATION_RELATIVE / f"{name}.csv")
        if len(rows) != expected:
            raise ValueError(
                f"{name}.csv holds {len(rows)} rows, expected the reviewed {expected}"
            )
        maps[name] = rows

    # ── What the source leaves for a human ───────────────────────────────────
    # None of these raises. Each is a real decision the freeze did not make, and
    # recording it is the point: the reconciliation report reads these counts and
    # refuses to call a migration ready while any of them stands.
    titles = Counter(row.get("canonical_title") for row in records)
    duplicate_titles = sorted(
        title for title, count in titles.items() if title and count > 1
    )
    misrouted = [
        row.get("canonical_title")
        for row in records
        if (row.get("routing") or "").strip().upper() != "GRANTS_FUNDS"
    ]
    unresolved_mappings = [
        row.get("existing_id")
        for row in maps["legacy_id_mapping"]
        if not (row.get("resolved_live_id") or "").strip()
    ]

    return {
        "source_sha256": digest,
        "source_date": master.get("frozen_at"),
        "version": master.get("version"),
        "records": records,
        "migration_maps": maps,
        "open_questions": {
            # Title-string dedup is not sufficient, which the handoff's own
            # instructions say. These are candidates for semantic review, not
            # rows to merge.
            "duplicate_canonical_titles": duplicate_titles,
            # A record whose routing is not GRANTS_FUNDS does not belong in the
            # grants table at all, and importing it would recreate the Film
            # London Production Finance Market defect in a new place.
            "records_routed_elsewhere": misrouted,
            # A mapping with no resolved live ID cannot preserve a legacy
            # reference, so the live row it names has no stated destination.
            "mappings_without_resolved_live_id": unresolved_mappings,
        },
    }


def main(root: Path) -> None:
    snapshot = read_handoff(root)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    questions = snapshot["open_questions"]
    print(f"Grants v2 freeze: {len(snapshot['records'])} records")
    for name, rows in snapshot["migration_maps"].items():
        print(f"  {name}: {len(rows)} rows")
    print(f"Source SHA-256: {snapshot['source_sha256']}")
    print("Open questions the source does not settle:")
    print(
        f"  duplicate canonical titles: "
        f"{len(questions['duplicate_canonical_titles'])}"
    )
    print(f"  records routed elsewhere: {len(questions['records_routed_elsewhere'])}")
    print(
        f"  mappings without a resolved live ID: "
        f"{len(questions['mappings_without_resolved_live_id'])}"
    )
    print("No database or paid report changes made")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: prepare_grants_handoff_snapshot.py PATH_TO_CORRECT_FILES"
        )
    main(Path(sys.argv[1]))
