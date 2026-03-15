#!/usr/bin/env python3
"""Reset all scraped data and reseed scrape_sources from DEFAULT_SOURCES.

This script purges stale data that was scraped from old/third-party sources so
that a fresh scrape against the new official government sources can populate
the database cleanly.

Tables affected:
  TRUNCATED (all rows deleted):
    - pending_changes   — queued diffs awaiting admin approval
    - scrape_runs       — historical scrape run logs
    - scrape_sources    — reseeded from DEFAULT_SOURCES afterwards
    - sync_settings     — last-sync timestamps

  TRUNCATED (scraped data):
    - incentive_programs
    - grant_opportunities
    - film_festivals
    - crew_costs

  NOTE: incentive_programs and crew_costs also have baseline seed data from
  Alembic migrations (i3j4k5l6m7n8, o9p0q1r2s3t4, s3t4u5v6w7x8). After
  running this script, re-run those migrations to re-seed baseline data:
    alembic upgrade head
  Then trigger a full scrape to populate fresh official data.

  NOT affected:
    - admins, users, subscriptions, reports, territory_watchlist,
      comparable_productions, email_gating_records, data_sources,
      territory_weather — none of these hold scraped data.

Usage:
    # Full reset — nuke everything and reseed scrape_sources
    python scripts/reset_scraper_data.py

    # Dry run — show what would be deleted without touching the DB
    python scripts/reset_scraper_data.py --dry-run
"""
import argparse
import sys
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Allow running from the project root
sys.path.insert(0, ".")

from app.core.config import get_settings
from app.modules.scraper.sources import DEFAULT_SOURCES


# Tables to fully truncate (no conditional logic)
_FULL_TRUNCATE_TABLES = [
    "pending_changes",
    "scrape_runs",
    "scrape_sources",
    "sync_settings",
]

# Tables holding scraped data — may support --preserve-seeds
_DATA_TABLES = [
    "incentive_programs",
    "grant_opportunities",
    "film_festivals",
    "crew_costs",
]


def _table_exists(session: Session, table_name: str) -> bool:
    """Check if a table exists in the database."""
    result = session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = :t"
        ),
        {"t": table_name},
    ).scalar()
    return (result or 0) > 0


def _count_rows(session: Session, table_name: str) -> int:
    return session.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0  # noqa: S608


def reset(dry_run: bool = False) -> None:
    settings = get_settings()
    engine = create_engine(settings.DB_URL)

    with Session(engine) as session:
        print("=" * 60)
        print("PRODCULATOR — Scraper Data Reset")
        print("=" * 60)
        print(f"  Database: {settings.DB_URL.split('@')[-1] if '@' in settings.DB_URL else '(local)'}")
        print(f"  Dry run:  {dry_run}")
        print()

        # ── Phase 1: Report current state ────────────────────────────
        print("Current row counts:")
        all_tables = _FULL_TRUNCATE_TABLES + _DATA_TABLES
        for t in all_tables:
            if _table_exists(session, t):
                count = _count_rows(session, t)
                print(f"  {t:30s} {count:>6,} rows")
            else:
                print(f"  {t:30s}  (table does not exist)")
        print()

        if dry_run:
            print("[DRY RUN] No changes made. Rerun without --dry-run to execute.")
            return

        # ── Phase 2: Truncate scraper metadata tables ────────────────
        print("Phase 1/3 — Truncating scraper metadata tables...")
        for t in _FULL_TRUNCATE_TABLES:
            if not _table_exists(session, t):
                print(f"  Skipping {t} (does not exist)")
                continue
            before = _count_rows(session, t)
            session.execute(text(f"DELETE FROM {t}"))  # noqa: S608
            print(f"  {t}: deleted {before:,} rows")
        session.commit()

        # ── Phase 3: Truncate data tables ────────────────────────
        print("Phase 2/3 — Clearing scraped data tables...")
        for t in _DATA_TABLES:
            if not _table_exists(session, t):
                print(f"  Skipping {t} (does not exist)")
                continue
            before = _count_rows(session, t)
            session.execute(text(f"DELETE FROM {t}"))  # noqa: S608
            print(f"  {t}: deleted {before:,} rows")
        session.commit()

        # ── Phase 4: Reseed scrape_sources from DEFAULT_SOURCES ──────
        print("Phase 3/3 — Reseeding scrape_sources from DEFAULT_SOURCES...")
        now = datetime.now(timezone.utc).isoformat()
        seeded = 0
        for source in DEFAULT_SOURCES:
            session.execute(
                text(
                    "INSERT INTO scrape_sources "
                    "(id, resource_type, url, label, territory, is_pdf, "
                    " use_bls_api, use_rest_api, source_authority, "
                    " enabled, last_scraped_at, last_status, last_error, "
                    " created_at, updated_at) "
                    "VALUES "
                    "(:id, :resource_type, :url, :label, :territory, :is_pdf, "
                    " :use_bls_api, :use_rest_api, :source_authority, "
                    " :enabled, NULL, NULL, NULL, :created_at, :updated_at)"
                ),
                {
                    "id": str(uuid4()),
                    "resource_type": source["resource_type"],
                    "url": source["url"],
                    "label": source["label"],
                    "territory": source.get("territory"),
                    "is_pdf": source.get("is_pdf", False),
                    "use_bls_api": source.get("use_bls_api", False),
                    "use_rest_api": source.get("use_rest_api", False),
                    "source_authority": source.get("source_authority", "government_agency"),
                    "enabled": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            seeded += 1
        session.commit()
        print(f"  Seeded {seeded} sources ({len(DEFAULT_SOURCES)} in DEFAULT_SOURCES)")

        # ── Summary ──────────────────────────────────────────────────
        print()
        print("Post-reset row counts:")
        for t in all_tables:
            if _table_exists(session, t):
                count = _count_rows(session, t)
                print(f"  {t:30s} {count:>6,} rows")
        print()
        print("✅ Reset complete. You can now trigger a fresh scrape via the admin panel")
        print("   or run: POST /api/admin/scraper/run-all")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset all scraped data and reseed scrape_sources."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without touching the database.",
    )
    args = parser.parse_args()

    # Safety confirmation
    if not args.dry_run:
        print()
        print("⚠️  WARNING: This will DELETE all scraped data from the database.")
        print("   Tables affected: pending_changes, scrape_runs, scrape_sources,")
        print("   sync_settings, incentive_programs, grant_opportunities,")
        print("   film_festivals, crew_costs")
        print()
        confirm = input("Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)

    reset(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
