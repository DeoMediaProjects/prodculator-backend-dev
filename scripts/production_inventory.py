"""A read-only inventory of what production actually holds. Writes nothing.

Usage, on the production host, from the app directory::

    venv/bin/python scripts/production_inventory.py
    venv/bin/python scripts/production_inventory.py --json inventory.json

This is the first step of the v2 cutover and the cheapest: every gate after it
is sized by what it finds, and until it has run the plan is working from local
development counts that have no authority over production.

WHY IT USES THE APP'S OWN SETTINGS
----------------------------------
It reads ``DB_URL`` from the environment the application already runs in, and
takes no database argument. Passing a URL on the command line is how a
production command gets aimed at the wrong database — the host that owns the
data already knows its own connection string, and asking a human to retype it
adds a failure mode and no capability.

WHY IT CANNOT WRITE
-------------------
Every statement runs inside a transaction opened read-only at the database, so
the guarantee is enforced by Postgres rather than by this file being careful. A
bug here fails with a Postgres error instead of changing a row.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]

#: Tables the v2 cutover touches or reconciles against, with the column whose
#: distribution decides how much work a gate is.
_BREAKDOWNS: tuple[tuple[str, str | None], ...] = (
    ("incentive_programs", "qs_engine_type"),
    ("grant_opportunities", None),
    ("film_festivals", None),
    ("comparable_productions", None),
    ("distributors", None),
    ("reports", None),
)

#: Staging and ledger tables. Their absence is a finding rather than an error:
#: it tells the operator which migrations this database has not yet had.
_STAGING: tuple[str, ...] = (
    "engine_handoff_records",
    "market_tracks",
    "opportunity_cycles",
    "opportunity_rules",
    "commercial_company_profiles",
    "commercial_comparable_profiles",
    "commercial_comparable_relationships",
    "source_verifications",
)


def _scalar(conn: sa.Connection, sql: str) -> int:
    return int(conn.execute(sa.text(sql)).scalar() or 0)


def collect(engine: sa.Engine) -> dict:
    inventory: dict = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tables": {},
        "missing_tables": [],
        "staging": {},
        "alembic_head": None,
    }

    with engine.connect() as conn:
        # Read-only at the database, not by convention. A bug in this file then
        # fails with a Postgres error rather than changing a row.
        if conn.dialect.name == "postgresql":
            conn.execute(sa.text("SET TRANSACTION READ ONLY"))

        inspector = sa.inspect(conn)
        present = set(inspector.get_table_names())

        if "alembic_version" in present:
            inventory["alembic_head"] = conn.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar()

        for table, column in _BREAKDOWNS:
            if table not in present:
                inventory["missing_tables"].append(table)
                continue
            entry: dict = {"rows": _scalar(conn, f"SELECT COUNT(*) FROM {table}")}

            columns = {c["name"] for c in inspector.get_columns(table)}
            if "status" in columns:
                entry["by_status"] = {
                    str(row[0]): int(row[1])
                    for row in conn.execute(
                        sa.text(
                            f"SELECT COALESCE(status,'(null)'), COUNT(*) "
                            f"FROM {table} GROUP BY 1 ORDER BY 2 DESC"
                        )
                    )
                }
            if column and column in columns:
                entry[f"by_{column}"] = {
                    str(row[0]): int(row[1])
                    for row in conn.execute(
                        sa.text(
                            f"SELECT COALESCE({column},'(null)'), COUNT(*) "
                            f"FROM {table} GROUP BY 1 ORDER BY 2 DESC"
                        )
                    )
                }
            elif column:
                # The column the gate is sized by does not exist here, which
                # means this database predates the migration that adds it. That
                # is the finding, not an absence of data.
                entry[f"missing_column_{column}"] = True
            inventory["tables"][table] = entry

        for table in _STAGING:
            inventory["staging"][table] = (
                _scalar(conn, f"SELECT COUNT(*) FROM {table}")
                if table in present
                else None
            )

        # Live grant IDs, for reconciling against the freeze's 88 mappings. Only
        # identifiers: a reconciliation needs no grant terms, and this file gets
        # circulated for review.
        if "grant_opportunities" in present:
            inventory["grant_ids"] = [
                str(row[0])
                for row in conn.execute(sa.text("SELECT id FROM grant_opportunities"))
            ]
        if "film_festivals" in present:
            inventory["festival_ids"] = [
                str(row[0])
                for row in conn.execute(sa.text("SELECT id FROM film_festivals"))
            ]

    return inventory


def render(inventory: dict) -> str:
    lines = [
        "Production inventory — read-only, nothing was written",
        f"Collected {inventory['collected_at']}",
        f"Alembic head: {inventory['alembic_head'] or '(no alembic_version table)'}",
        "",
    ]

    for table, entry in inventory["tables"].items():
        lines.append(f"{table}: {entry['rows']} rows")
        for key, value in entry.items():
            if key == "rows":
                continue
            if key.startswith("missing_column_"):
                lines.append(
                    f"    {key.removeprefix('missing_column_')}: COLUMN ABSENT — "
                    "this database predates the migration that adds it"
                )
                continue
            lines.append(f"    {key.removeprefix('by_')}:")
            for name, count in value.items():
                lines.append(f"      {name:38} {count}")
        lines.append("")

    if inventory["missing_tables"]:
        lines.append("Tables absent entirely:")
        for table in inventory["missing_tables"]:
            lines.append(f"  {table}")
        lines.append("")

    lines.append("Staging and ledger tables:")
    for table, count in inventory["staging"].items():
        lines.append(
            f"  {table:42} {'NOT MIGRATED' if count is None else f'{count} rows'}"
        )
    lines.append("")

    engines = (
        inventory["tables"].get("incentive_programs", {}).get("by_qs_engine_type")
    )
    if engines:
        unclassified = engines.get("(null)", 0)
        total = inventory["tables"]["incentive_programs"]["rows"]
        lines.append(
            f"Gate 3 (incentive classification): {unclassified} of {total} "
            "programmes carry no statutory engine."
        )
    if "grant_ids" in inventory:
        lines.append(
            f"Gate 4 (grants migration): {len(inventory['grant_ids'])} live grant "
            "rows to reconcile against the freeze's 88 mappings."
        )
    if "festival_ids" in inventory:
        lines.append(
            f"Festivals: {len(inventory['festival_ids'])} live rows to reconcile "
            "against the 380-record master."
        )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", type=Path, help="Also write the full inventory here, for the record"
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from app.core.config import get_settings

    settings = get_settings()
    try:
        engine = sa.create_engine(settings.DB_URL)
        inventory = collect(engine)
    except Exception as exc:  # noqa: BLE001 — the operator needs the reason
        parser.exit(2, f"Inventory failed: {exc}\n")

    print(render(inventory))

    if args.json:
        args.json.write_text(
            json.dumps(inventory, indent=2, default=str) + "\n", encoding="utf-8"
        )
        print(f"\nFull inventory written to {args.json}")
        print("It carries row identifiers, not grant or festival terms.")


if __name__ == "__main__":
    main()
