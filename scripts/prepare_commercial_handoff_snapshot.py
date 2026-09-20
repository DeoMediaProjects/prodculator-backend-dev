"""Validate and snapshot the frozen commercial workbook without DB writes.

Usage: python scripts/prepare_commercial_handoff_snapshot.py PATH_TO_XLSX

This is a faithful source snapshot, not a paid-match catalogue.  The workbook
contains prose scopes and some non-canonical access labels, so import and
matching require a separate field-level review.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "handoff_snapshots" / "sales_distribution_v1_2026-09-16.json"
SOURCE_SHA256 = "f110ba8a89653e387939e983452342327b58a01092157227316fb52bf1448bdc"


def _rows(workbook, name: str) -> list[dict]:
    values = workbook[name].values
    headers = next(values)
    if not all(isinstance(item, str) and item for item in headers):
        raise ValueError(f"Malformed header in {name}")
    return [dict(zip(headers, row, strict=True)) for row in values]


def read_handoff(path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("Commercial workbook SHA-256 differs from reviewed freeze")
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        companies = _rows(workbook, "Master")
        relationships = _rows(workbook, "Comparable Relationships")
    finally:
        workbook.close()
    ids = [row["company_id"] for row in companies]
    names = {row["company_name"] for row in companies}
    if len(companies) != 101 or len(set(ids)) != 101:
        raise ValueError("Expected 101 unique frozen commercial companies")
    if len(relationships) != 205:
        raise ValueError("Expected 205 frozen title-company relationships")
    if any(not row.get("source_url") for row in companies + relationships):
        raise ValueError("Commercial row lacks source URL")
    if any(row.get("company_name") not in names for row in relationships):
        raise ValueError("Commercial relationship has no company endpoint")
    verified_states = {"VERIFIED", "VERIFIED_DIRECTORY_2026"}
    if any(row.get("verification_status") not in verified_states for row in relationships):
        raise ValueError("Commercial relationship is not verified")
    return {
        "source_sha256": digest,
        "source_date": "2026-09-16",
        "companies": companies,
        "relationships": relationships,
    }


def main(path: Path) -> None:
    snapshot = read_handoff(path)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print("Commercial freeze: 101 companies, 205 sourced relationships")
    print(f"Source SHA-256: {snapshot['source_sha256']}")
    print("No database or paid report changes made")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: prepare_commercial_handoff_snapshot.py PATH_TO_XLSX")
    main(Path(sys.argv[1]))
