"""Validate the supplied festival and markets handoffs without touching the DB.

Usage:
    python scripts/prepare_engine_handoff_snapshots.py FESTIVAL_ZIP MARKETS_ZIP

The two JSON outputs are faithful snapshots for the later migration/engine work.
Only Excel's mixed TRUE/FALSE cell representations are normalised to JSON booleans.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "handoff_snapshots"
FESTIVAL_MEMBER = (
    "PRODCULATOR_FESTIVAL_ENGINE_V2_1_FINAL_DEV_HANDOFF/01_DATA/"
    "prodculator_festivals_master_v2_FINAL_VERIFIED_2026-09-15.json"
)
MARKETS_MEMBER = (
    "data/Prodculator_Markets_Labs_WIP_Master_v1_FINAL_LOGIC_INTEGRATED_2026-09-16.xlsx"
)
FESTIVAL_SHA256 = "d5f134fa9b7c99b2b56ab47c9578a3ac25fdd2245764d8377011b03ed58446f0"
MARKETS_SHA256 = "fbd0a1cb9b42d8523bfc3f43b3e8de25ecff5322fec964b677aa4cd204a050df"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.upper() in {"TRUE", "FALSE"}:
        return value.upper() == "TRUE"
    raise ValueError(f"Unexpected paid_safe value: {value!r}")


def _festival_rows(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        rows = json.loads(archive.read(FESTIVAL_MEMBER))
    if not isinstance(rows, list) or len(rows) != 380:
        raise ValueError("Festival handoff must contain exactly 380 records")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != 380:
        raise ValueError("Festival IDs are not unique")
    paid = [row for row in rows if row.get("paid_match_eligible_v2") is True]
    if len(paid) != 76:
        raise ValueError("Expected 76 paid-safe festival snapshot records")
    for row in paid:
        if not all(
            (
                row.get("routing") == "FESTIVAL",
                row.get("identity_verified") is True,
                row.get("current_cycle_verified") is True,
                row.get("eligibility_verified") is True,
                bool(row.get("website_url")),
            )
        ):
            raise ValueError(f"Paid-safe festival lacks a required gate: {row['id']}")
    return rows


def _markets_rows(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        workbook = load_workbook(
            io.BytesIO(archive.read(MARKETS_MEMBER)), read_only=True, data_only=True
        )
        try:
            values = workbook["Master"].values
            headers = next(values)
            rows = [dict(zip(headers, values_row, strict=True)) for values_row in values]
        finally:
            workbook.close()
    if len(rows) != 203:
        raise ValueError("Markets handoff must contain exactly 203 tracks")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != 203:
        raise ValueError("Markets track IDs are not unique")
    for row in rows:
        row["paid_safe"] = _bool(row["paid_safe"])
    paid = [row for row in rows if row["paid_safe"]]
    if len(paid) != 43:
        raise ValueError("Expected 43 paid-safe markets snapshot tracks")
    for row in paid:
        if row.get("status") not in {"OPEN", "UPCOMING"}:
            raise ValueError(f"Paid-safe markets track is not actionable: {row['id']}")
        if not row.get("source") or not row.get("verified_on"):
            raise ValueError(f"Paid-safe markets track lacks provenance: {row['id']}")
    return rows


def main(festival_zip: Path, markets_zip: Path) -> None:
    festival_hash = _sha256(festival_zip)
    markets_hash = _sha256(markets_zip)
    if festival_hash != FESTIVAL_SHA256 or markets_hash != MARKETS_SHA256:
        raise ValueError("Archive SHA-256 differs from the reviewed handoff")
    festivals = _festival_rows(festival_zip)
    markets = _markets_rows(markets_zip)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "festivals_v2_1_2026-09-15.json").write_text(
        json.dumps(festivals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "markets_labs_wip_v1_2026-09-16.json").write_text(
        json.dumps(markets, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"Festivals: {len(festivals)} records, 76 paid-safe snapshot")
    print(f"Markets/Labs/WIP: {len(markets)} tracks, 43 paid-safe snapshot")
    print(f"Festival archive SHA-256: {festival_hash}")
    print(f"Markets archive SHA-256: {markets_hash}")
    print("No database changes made. Snapshot paid-safe flags are not runtime actionability.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: prepare_engine_handoff_snapshots.py FESTIVAL_ZIP MARKETS_ZIP")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
