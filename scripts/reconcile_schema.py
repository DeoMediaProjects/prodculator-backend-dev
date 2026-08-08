"""Schema reconciliation: create tables and add columns the models declare but
the database is missing.

Background: this codebase historically built its schema via SQLModel
create_all (AUTO_CREATE_DB_SCHEMA), and only *some* changes were captured in
Alembic migrations. A database provisioned either way can therefore drift from
the models in two ways, and this script closes both:

  - Missing tables. Created with create_all, restricted to the absent tables.
    This previously printed "run alembic upgrade head" and stopped, which is not
    a usable instruction against a create_all-provisioned database: the chain is
    120-plus revisions replaying ALTERs for columns create_all already added, so
    it fails partway and leaves the schema half-migrated. Emitting CREATE TABLE
    for exactly the missing tables reaches the same end state without touching
    anything that already exists. This is how admin_audit_logs came to be absent
    in production while the audit reader expected it.
  - Missing columns. Added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

Both passes are:
  - additive only (never drops, retypes or reorders anything),
  - nullable-only for columns (safe on tables that already hold rows),
  - idempotent (safe to run repeatedly).

It does not stamp the Alembic version table. A database reconciled here is in
the right *shape* but its migration history is still whatever it was, so decide
that separately rather than assuming this script settled it.

Usage (venv active; DB_URL points at the target database):
    python scripts/reconcile_schema.py            # dry run — lists what it would do
    python scripts/reconcile_schema.py --apply     # execute
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
    parser.add_argument("--apply", action="store_true", help="execute the changes (default: dry run)")
    args = parser.parse_args()

    load_dotenv()
    db_url = os.environ["DB_URL"]
    engine = create_engine(db_url)
    insp = inspect(engine)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== schema reconcile [{mode}] target: {db_url.split('@')[-1]} ===\n")

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

    if not missing_tables:
        print("No missing tables.")
    else:
        print(f"{len(missing_tables)} missing table(s):")
        for t in sorted(missing_tables):
            print(f"  - {t}")
        if args.apply:
            # checkfirst leaves any table that appeared between the inspection
            # and now alone, so a concurrent deploy cannot turn this into an
            # error. Restricted to the missing tables so nothing existing is
            # considered at all.
            SQLModel.metadata.create_all(
                engine,
                tables=[SQLModel.metadata.tables[t] for t in missing_tables],
                checkfirst=True,
            )
            print("  created.\n")
        else:
            print("  (dry run)\n")

    if not to_add:
        print("No missing columns. Schema matches the models.")
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
            print("  added.")
        else:
            print("  (dry run)")

    if not args.apply and (missing_tables or to_add):
        print("\nDry run — re-run with --apply to make these changes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
