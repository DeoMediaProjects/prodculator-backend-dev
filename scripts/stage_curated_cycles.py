"""Preflight or stage officially checked, rules-incomplete opportunity cycles.

This script does not alter the live report or replace an existing curator/admin row.
Use without arguments for a read-only diff; ``--apply`` inserts missing rows only
after the base handoff snapshot has been staged and reviewed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
CURATED = ROOT / "data/curated_opportunities/2026-09-17_initial_cycles.json"
CURATED_SHA256 = "345f2dd13494564fd956a3c435083e650a1c49d01035d28634309f17b7c251e8"


@dataclass(frozen=True)
class CuratedStageResult:
    checked_cycles: int
    checked_rules: int
    new_cycles: int
    new_rules: int
    applied: bool


def _cycle_record(raw: dict, opportunity) -> dict:
    return {
        "id": opportunity.id,
        "kind": opportunity.kind,
        "record_id": raw["record_id"],
        "section_name": raw["section_name"],
        "cycle_open": opportunity.cycle_open,
        "cycle_deadline": opportunity.cycle_deadline,
        "cycle_verified": opportunity.cycle_verified,
        "rules_complete": opportunity.rules_complete,
        "source_url": opportunity.source_url,
        "verified_on": opportunity.verified_on,
    }


def _rule_records(opportunity) -> list[dict]:
    return [
        {
            "id": str(
                uuid5(NAMESPACE_URL, f"{opportunity.id}:{index}:{gate.field}:{gate.condition}")
            ),
            "cycle_id": opportunity.id,
            "project_field": gate.field,
            "operator": gate.operator,
            "expected": gate.expected,
            "condition": gate.condition,
            "source_url": gate.source_url,
            "verified_on": opportunity.verified_on,
        }
        for index, gate in enumerate(opportunity.gates)
    ]


def _check_existing(existing: dict, incoming: list[dict], label: str) -> list[dict]:
    new = []
    for row in incoming:
        previous = existing.get(row["id"])
        if previous is None:
            new.append(row)
        elif any(previous.get(key) != value for key, value in row.items()):
            raise ValueError(f"Existing {label} differs from source review: {row['id']}")
    return new


def stage_curated_cycles(
    engine: sa.Engine,
    *,
    apply: bool = False,
    source_path: Path = CURATED,
    today: date | None = None,
) -> CuratedStageResult:
    from app.modules.reports.curated_opportunities import parse_curated_cycles

    source_bytes = source_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != CURATED_SHA256:
        raise ValueError("Curated cycle file differs from its reviewed SHA-256")
    payload = json.loads(source_bytes)
    opportunities = parse_curated_cycles(payload, today=today or date.today())
    cycles = [
        _cycle_record(raw, opportunity)
        for raw, opportunity in zip(payload["cycles"], opportunities, strict=True)
    ]
    rules = [rule for opportunity in opportunities for rule in _rule_records(opportunity)]

    with engine.begin() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        if not {"engine_handoff_records", "opportunity_cycles", "opportunity_rules"} <= tables:
            raise RuntimeError("Apply the engine v2 staging migration first")
        handoff = sa.Table("engine_handoff_records", sa.MetaData(), autoload_with=conn)
        cycle_table = sa.Table("opportunity_cycles", sa.MetaData(), autoload_with=conn)
        rule_table = sa.Table("opportunity_rules", sa.MetaData(), autoload_with=conn)
        source_ids = {
            (row.kind, row.record_id)
            for row in conn.execute(sa.select(handoff.c.kind, handoff.c.record_id))
        }
        for raw in payload["cycles"]:
            if (raw["kind"], raw["record_id"]) not in source_ids:
                raise ValueError(f"Handoff record not staged: {raw['kind']} {raw['record_id']}")
        existing_cycles = {
            row["id"]: row for row in conn.execute(sa.select(cycle_table)).mappings()
        }
        existing_rules = {row["id"]: row for row in conn.execute(sa.select(rule_table)).mappings()}
        new_cycles = _check_existing(existing_cycles, cycles, "cycle")
        new_rules = _check_existing(existing_rules, rules, "rule")
        if apply:
            if new_cycles:
                conn.execute(cycle_table.insert(), new_cycles)
            if new_rules:
                conn.execute(rule_table.insert(), new_rules)

    return CuratedStageResult(
        checked_cycles=len(cycles),
        checked_rules=len(rules),
        new_cycles=len(new_cycles),
        new_rules=len(new_rules),
        applied=apply,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Insert missing checked cycles and rules"
    )
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from app.core.config import get_settings

    engine = sa.create_engine(get_settings().DB_URL)
    try:
        result = stage_curated_cycles(engine, apply=args.apply)
    except (RuntimeError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()
