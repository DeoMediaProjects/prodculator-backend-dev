"""Release a user's consumed report quota by voiding their usage-ledger rows.

Quota is counted from ``report_usage_events``, an append-only ledger, precisely so
that deleting a report does not hand the slot back. That means there is no way to
give an account its allowance back through the product: the ledger is doing its job.
This script is the deliberate exception, for demo and support accounts.

It VOIDS rows rather than deleting them, using the same ``voided_at`` mechanism the
application already uses when a report fails. A voided row stops counting but stays
on the record, so the reset is auditable and the ledger keeps its history.

Dry run by default. Nothing is written without --apply.

    DB_URL=postgresql://... python scripts/reset_report_quota.py --email demo@deomedia.net
    DB_URL=postgresql://... python scripts/reset_report_quota.py --email demo@deomedia.net --apply

By default only the current calendar month is released, which is the period a
monthly allowance is measured over. Pass --all to release every period.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="account whose quota to release")
    parser.add_argument(
        "--all",
        action="store_true",
        help="release every period, not just the current calendar month",
    )
    parser.add_argument(
        "--apply", action="store_true", help="execute the changes (default: dry run)",
    )
    args = parser.parse_args()

    load_dotenv()
    db_url = os.environ["DB_URL"]
    engine = create_engine(db_url)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== quota reset [{mode}] target: {db_url.split('@')[-1]} ===\n")

    now = datetime.now(timezone.utc)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    with engine.begin() as conn:
        user = conn.execute(
            text("SELECT id, email, plan, credits_remaining FROM users WHERE lower(email) = lower(:e)"),
            {"e": args.email},
        ).mappings().first()
        if not user:
            print(f"No account found for {args.email}. Nothing done.")
            return 1

        print(f"user      {user['email']}  ({user['id']})")
        print(f"plan      {user['plan']}")
        print(f"credits   {user['credits_remaining']}")

        # Only live rows are candidates. Re-voiding an already-voided row would
        # move its timestamp and lose when the report actually failed.
        where = "user_id = :uid AND voided_at IS NULL"
        params: dict = {"uid": user["id"]}
        if not args.all:
            where += " AND created_at >= :since"
            params["since"] = period_start.isoformat()
            print(f"period    from {period_start.date()} (current month)")
        else:
            print("period    all time")

        rows = conn.execute(
            text(
                f"SELECT id, report_id, report_type, created_at FROM report_usage_events "
                f"WHERE {where} ORDER BY created_at"
            ),
            params,
        ).mappings().all()

        print(f"\n{len(rows)} usage event(s) currently counting against quota:")
        for r in rows:
            print(f"  {str(r['created_at'])[:19]}  {r['report_type']:<10} report={r['report_id']}")

        if not rows:
            print("\nQuota is already clear for this period. Nothing to do.")
            return 0

        if not args.apply:
            print("\nDry run. Re-run with --apply to void these events.")
            return 0

        conn.execute(
            text(f"UPDATE report_usage_events SET voided_at = :now WHERE {where}"),
            {**params, "now": now.isoformat()},
        )
        remaining = conn.execute(
            text(
                "SELECT count(*) FROM report_usage_events "
                "WHERE user_id = :uid AND voided_at IS NULL AND created_at >= :since"
            ),
            {"uid": user["id"], "since": period_start.isoformat()},
        ).scalar()

    print(f"\nVoided {len(rows)} event(s). Quota consumed this month is now {remaining}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
