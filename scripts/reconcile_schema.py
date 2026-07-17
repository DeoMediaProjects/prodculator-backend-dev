"""One-shot schema reconciliation: add model columns missing from the DB.

Background: this codebase historically built its schema via SQLModel
create_all (AUTO_CREATE_DB_SCHEMA), and only *some* changes were captured in
Alembic migrations. A pure migration build therefore produces a schema that is
missing columns the current models declare (e.g. subscriptions.past_due_since).
create_all cannot fix this — it only creates missing *tables*, never adds
columns to existing ones.

This script inspects every SQLModel table, compares its declared columns to
the live database, and issues `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for any
that are missing. It is:
  - additive only (never drops or retypes a column),
  - nullable-only (safe on tables that already hold rows),
  - idempotent (safe to run repeatedly).

Usage (venv active; DB_URL points at the target database):
    python scripts/reconcile_schema.py            # dry run — lists what it would add
    python scripts/reconcile_schema.py --apply     # execute the ALTERs
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import app.models.sql_models  # noqa: F401  (registers every table on SQLModel.metadata)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="execute the ALTERs (default: dry run)")
    args = parser.parse_args()

    load_dotenv()
    db_url = os.environ["DB_URL"]
    engine = create_engine(db_url)
    insp = inspect(engine)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== schema reconcile — {mode} — target: {db_url.split('@')[-1]} ===\n")

    db_tables = set(insp.get_table_names())
    missing_tables: list[str] = []
    to_add: list[tuple[str, str, str]] = []

    for table_name, table in SQLModel.metadata.tables.items():
        if table_name not in db_tables:
            missing_tables.append(table_name)
            continue
        db_cols = {c["name"] for c in insp.get_columns(table_name)}
        for col in table.columns:
            if col.name not in db_cols:
                coltype = col.type.compile(engine.dialect)
                to_add.append((table_name, col.name, coltype))

    if missing_tables:
        print("MISSING TABLES (run `alembic upgrade head` — not handled here):")
        for t in sorted(missing_tables):
            print(f"  - {t}")
        print()

    if not to_add:
        print("No missing columns — schema matches the models.")
    else:
        print(f"{len(to_add)} missing column(s):")
        for table_name, col_name, coltype in to_add:
            print(f"  {table_name}.{col_name}  {coltype}")
        if args.apply:
            with engine.begin() as conn:
                for table_name, col_name, coltype in to_add:
                    conn.execute(text(
                        f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{col_name}" {coltype}'
                    ))
            print("\nApplied.")
        else:
            print("\nDry run — re-run with --apply to add these columns.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
