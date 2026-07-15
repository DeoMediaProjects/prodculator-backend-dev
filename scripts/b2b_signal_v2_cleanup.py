"""One-time production_signals v2 data cleanup: dedupe + vocabulary normalisation.

Per the implementation plan (Section 3), this runs as a DRY RUN by default and
prints the affected-rows report that goes to the client before anything executes
for real. Nothing is written without --apply.

What it does:
  1. Dedupe: legacy rows sharing a script_id are reduced to one (latest wins,
     by updated_at then created_at). Needed before the unique index on
     script_id can be enforced on a database that predates it.
  2. Normalise: format -> canonical_format(), genres -> canonical_genres(),
     so legacy display-cased values ("Feature Film") land in the same segments
     as new canonical writes ("feature"). Rows already canonical are untouched.
  3. Ensure the uq_production_signals_script_id unique index exists (the v2
     migration skips it when duplicates block creation).

Usage (venv active, DB_URL in .env decides the target database):
    python scripts/b2b_signal_v2_cleanup.py           # dry run, prints report
    python scripts/b2b_signal_v2_cleanup.py --apply   # execute
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from app.modules.b2b.signal_normalise import canonical_format, canonical_genres


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="execute changes (default: dry run)")
    args = parser.parse_args()

    load_dotenv()
    db_url = os.environ["DB_URL"]
    engine = create_engine(db_url)
    mode = "APPLY" if args.apply else "DRY RUN"
    host = db_url.split("@")[-1]
    print(f"=== production_signals v2 cleanup — {mode} — target: {host} ===\n")

    with engine.begin() as conn:
        # --- 1. Dedupe per script_id (latest wins) ---
        dupes = conn.execute(text(
            """
            SELECT script_id, count(*) AS n
            FROM production_signals
            WHERE script_id IS NOT NULL
            GROUP BY script_id HAVING count(*) > 1
            ORDER BY n DESC
            """
        )).fetchall()
        print(f"1. Duplicate script_ids: {len(dupes)}")
        doomed_total = 0
        for script_id, n in dupes:
            rows = conn.execute(text(
                """
                SELECT id, updated_at, created_at FROM production_signals
                WHERE script_id = :sid
                ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                """
            ), {"sid": script_id}).fetchall()
            keep, doomed = rows[0], rows[1:]
            doomed_total += len(doomed)
            print(f"   script_id={script_id}: {n} rows -> keep {keep.id}, remove {[r.id for r in doomed]}")
            if args.apply:
                conn.execute(
                    text("DELETE FROM production_signals WHERE script_id = :sid AND id != :keep"),
                    {"sid": script_id, "keep": keep.id},
                )
        print(f"   rows to remove: {doomed_total}\n")

        # --- 2. Vocabulary normalisation (format + genres) ---
        rows = conn.execute(text(
            "SELECT id, format, genres FROM production_signals"
        )).fetchall()
        changed = 0
        for row in rows:
            new_fmt = canonical_format(row.format) if row.format else row.format
            raw_genres = row.genres
            if isinstance(raw_genres, str):
                try:
                    raw_genres = json.loads(raw_genres)
                except ValueError:
                    raw_genres = None
            new_genres = canonical_genres(raw_genres) if raw_genres else raw_genres
            if new_fmt == row.format and new_genres == raw_genres:
                continue
            changed += 1
            print(f"   id={row.id}: format {row.format!r} -> {new_fmt!r}, genres {raw_genres!r} -> {new_genres!r}")
            if args.apply:
                conn.execute(
                    text("UPDATE production_signals SET format = :f, genres = :g WHERE id = :id"),
                    {"f": new_fmt, "g": json.dumps(new_genres) if new_genres is not None else None, "id": row.id},
                )
        print(f"2. Rows needing vocabulary normalisation: {changed}\n")

        # --- 3. Unique index on script_id ---
        idx = conn.execute(text(
            "SELECT 1 FROM pg_indexes WHERE tablename='production_signals' "
            "AND indexname='uq_production_signals_script_id'"
        )).fetchone()
        if idx:
            print("3. Unique index uq_production_signals_script_id: already present")
        else:
            print("3. Unique index uq_production_signals_script_id: MISSING" + (" -> creating" if args.apply else " (would create)"))
            if args.apply:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_production_signals_script_id ON production_signals (script_id)"
                ))

    print(f"\n=== {mode} complete ===")
    if not args.apply:
        print("No changes were written. Re-run with --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
