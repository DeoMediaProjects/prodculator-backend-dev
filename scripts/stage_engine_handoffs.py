"""Stage reviewed festival and markets snapshots without changing live recommendations.

Run ``python scripts/stage_engine_handoffs.py`` for a read-only preflight; add
``--apply`` only after the additive migration is present. Existing staged records
with different content cause an error, and existing market rows are never edited.
The live ``film_festivals`` table is only inspected for ID reconciliation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data" / "handoff_snapshots"
FESTIVAL_VERSION = "festival_v2.1_2026-09-15"
MARKETS_VERSION = "markets_v1_2026-09-16"
#: Content hashes of the reviewed snapshots, over the exact bytes the
#: repository stores. `.gitattributes` marks these files `-text` so Git
#: never rewrites their line endings — without that they hash differently
#: on Windows and Linux, and the pinned value is only ever right on the
#: platform it was generated on.
SNAPSHOT_HASHES = {
    "festivals_v2_1_2026-09-15.json": "845838c861d1987189d977b98654c75d916277ddfd62951c3f9ccb2a271f87c3",
    "markets_labs_wip_v1_2026-09-16.json": "7569850b80d4d1e3d2dfa567cf9777a1039e9b86b245de4e0021ac64fb7132a3",
}


@dataclass(frozen=True)
class StageResult:
    festival_records: int
    market_records: int
    new_handoff_records: int
    new_market_tracks: int
    matched_live_festival_ids: int
    unmatched_live_festival_ids: int
    applied: bool


def _load_rows(filename: str, count: int) -> list[dict]:
    raw = (SNAPSHOTS / filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SNAPSHOT_HASHES[filename]:
        raise ValueError(f"{filename}: content hash differs from the reviewed snapshot")
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError(f"{filename}: expected {count} rows")
    ids = [row.get("id") for row in rows]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != count:
        raise ValueError(f"{filename}: missing or duplicated IDs")
    return rows


def _payload_hash(row: dict) -> str:
    encoded = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_records(festivals: list[dict], markets: list[dict], now: datetime) -> list[dict]:
    records = []
    for kind, version, rows in (
        ("FESTIVAL", FESTIVAL_VERSION, festivals),
        ("MARKET_LAB_WIP", MARKETS_VERSION, markets),
    ):
        for row in rows:
            records.append(
                {
                    "kind": kind,
                    "record_id": row["id"],
                    "source_version": version,
                    "name": row["name"],
                    "routing": row.get("routing") if kind == "FESTIVAL" else "MARKET_LAB_WIP",
                    "payload_hash": _payload_hash(row),
                    "payload": row,
                    "imported_at": now,
                }
            )
    return records


def _market_track(row: dict, now: datetime) -> dict:
    verified_raw = row.get("verified_on")
    verified = date.fromisoformat(str(verified_raw)[:10]) if verified_raw else None
    return {
        "id": row["id"],
        "name": row["name"],
        "programme_class": row.get("class"),
        "stage_notes": row.get("stages"),
        "format_notes": row.get("formats"),
        "status_snapshot": row.get("status"),
        "cycle_snapshot": row.get("cycle"),
        "source_url": row.get("source"),
        "verified_on_snapshot": verified,
        "hard_gates_prose": row.get("hard_gates"),
        "paid_safe_snapshot": row.get("paid_safe") is True,
        "source_version": MARKETS_VERSION,
        "created_at": now,
    }


def stage_handoffs(
    engine: sa.Engine,
    *,
    apply: bool = False,
    festival_rows: list[dict] | None = None,
    market_rows: list[dict] | None = None,
) -> StageResult:
    festivals = (
        festival_rows
        if festival_rows is not None
        else _load_rows("festivals_v2_1_2026-09-15.json", 380)
    )
    markets = (
        market_rows
        if market_rows is not None
        else _load_rows("markets_labs_wip_v1_2026-09-16.json", 203)
    )
    now = datetime.now(timezone.utc)
    incoming = _source_records(festivals, markets, now)

    with engine.begin() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        required = {"engine_handoff_records", "market_tracks"}
        if not required <= tables:
            raise RuntimeError("Apply the engine v2 staging migration before importing")
        handoff_table = sa.Table("engine_handoff_records", sa.MetaData(), autoload_with=conn)
        market_table = sa.Table("market_tracks", sa.MetaData(), autoload_with=conn)
        existing_hashes = {
            (row.kind, row.record_id): row.payload_hash
            for row in conn.execute(
                sa.select(
                    handoff_table.c.kind, handoff_table.c.record_id, handoff_table.c.payload_hash
                )
            )
        }
        for row in incoming:
            key = (row["kind"], row["record_id"])
            if key in existing_hashes and existing_hashes[key] != row["payload_hash"]:
                raise ValueError(f"Staged source record changed: {key}; review before replacing")
        new_records = [
            row for row in incoming if (row["kind"], row["record_id"]) not in existing_hashes
        ]
        existing_market_ids = set(conn.execute(sa.select(market_table.c.id)).scalars())
        new_markets = [
            _market_track(row, now) for row in markets if row["id"] not in existing_market_ids
        ]

        matched = unmatched = 0
        if "film_festivals" in tables:
            live = sa.Table("film_festivals", sa.MetaData(), autoload_with=conn)
            live_ids = set(conn.execute(sa.select(live.c.id)).scalars())
            snapshot_ids = {row["id"] for row in festivals}
            matched = len(live_ids & snapshot_ids)
            unmatched = len(live_ids - snapshot_ids)

        if apply:
            if new_records:
                conn.execute(handoff_table.insert(), new_records)
            if new_markets:
                conn.execute(market_table.insert(), new_markets)

    return StageResult(
        festival_records=len(festivals),
        market_records=len(markets),
        new_handoff_records=len(new_records),
        new_market_tracks=len(new_markets),
        matched_live_festival_ids=matched,
        unmatched_live_festival_ids=unmatched,
        applied=apply,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Insert missing staging rows")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from app.core.config import get_settings

    engine = sa.create_engine(get_settings().DB_URL)
    try:
        result = stage_handoffs(engine, apply=args.apply)
    except (RuntimeError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()
