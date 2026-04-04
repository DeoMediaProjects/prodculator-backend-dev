#!/usr/bin/env python3
"""Backfill production_signals from completed reports.

This one-off script reads completed rows from `reports`, derives a
production-signal payload using the same mapper as the live pipeline, and
upserts into `production_signals` on `id`.

Usage:
  python scripts/backfill_production_signals.py --dry-run
  python scripts/backfill_production_signals.py
  python scripts/backfill_production_signals.py --limit 200
  python scripts/backfill_production_signals.py --report-id <report_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

sys.path.insert(0, ".")


def _load_reports(
    service: Any,
    *,
    report_id: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    query = service.supabase.table("reports").select("*").eq("status", "completed").order("created_at", desc=False)
    if report_id:
        query = query.eq("id", report_id)
    if limit is not None:
        query = query.limit(limit)
    result = query.execute()
    return result.data or []


def run_backfill(*, dry_run: bool, report_id: str | None, limit: int | None, include_empty_metadata: bool) -> None:
    from app.core.database_client import DatabaseClient
    from app.core.db import get_db_context
    from app.modules.reports.service import ReportService

    with get_db_context() as session:
        db = DatabaseClient(session)
        report_service = ReportService(db)
        reports = _load_reports(report_service, report_id=report_id, limit=limit)

        attempted = 0
        upserted = 0
        skipped_empty_metadata = 0
        skipped_missing_report_id = 0
        errors = 0

        print(f"Loaded {len(reports)} completed report(s).")
        if dry_run:
            print("Dry run enabled: no writes will be performed.")

        for idx, row in enumerate(reports, start=1):
            rid = row.get("id")
            if not rid:
                skipped_missing_report_id += 1
                continue

            request_metadata = row.get("request_metadata")
            metadata = request_metadata if isinstance(request_metadata, dict) else {}
            if not metadata and not include_empty_metadata:
                skipped_empty_metadata += 1
                continue

            attempted += 1
            try:
                if dry_run:
                    payload = report_service._build_production_signal_payload(  # noqa: SLF001
                        report_id=str(rid),
                        report_row=row,
                        request_metadata=metadata,
                        script_analysis=None,
                    )
                    if idx <= 5:
                        print(f"[DRY-RUN] report={rid} payload={json.dumps(payload, default=str)}")
                    continue

                record = report_service.upsert_production_signal(
                    report_id=str(rid),
                    report_row=row,
                    request_metadata=metadata,
                    script_analysis=None,
                )
                if record is not None:
                    upserted += 1
            except Exception as exc:
                errors += 1
                print(f"[ERROR] report={rid} error={exc}")

        print()
        print("Backfill summary:")
        print(f"  attempted: {attempted}")
        print(f"  upserted: {upserted if not dry_run else 0}")
        print(f"  skipped_empty_metadata: {skipped_empty_metadata}")
        print(f"  skipped_missing_report_id: {skipped_missing_report_id}")
        print(f"  errors: {errors}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill production_signals from completed reports.")
    parser.add_argument("--dry-run", action="store_true", help="Show derived payloads without writing.")
    parser.add_argument("--report-id", help="Backfill a single report id.")
    parser.add_argument("--limit", type=int, help="Max completed reports to process.")
    parser.add_argument(
        "--include-empty-metadata",
        action="store_true",
        help="Attempt backfill even when request_metadata is empty/null.",
    )
    args = parser.parse_args()

    run_backfill(
        dry_run=args.dry_run,
        report_id=args.report_id,
        limit=args.limit,
        include_empty_metadata=args.include_empty_metadata,
    )


if __name__ == "__main__":
    main()
