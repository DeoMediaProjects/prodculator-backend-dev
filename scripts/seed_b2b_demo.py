#!/usr/bin/env python3
"""Seed a local Business Intelligence (B2B) demo so the whole slice is clickable.

Sets up, idempotently:

  1. A manual-contract B2B subscription for a target user. Manual contracts need
     NO Stripe, so this works on a local box with no STRIPE_PRICE_* configured.
  2. Consented, non-internal production signals across N months, shaped to
     demonstrate the privacy floors and monthly composition:
       - a territory well above the floor            -> renders
       - a territory below the floor EVERY month but above it across the
         quarter                                     -> hidden monthly, VISIBLE quarterly
       - an internal + a non-consented row           -> must never appear anywhere
  3. Monthly aggregates for those months (the atomic unit quarterly composes from).
  4. An exclusive entitlement (the contract pack's "AI Usage Module", reverting
     2028-06-30) so exclusivity blocking is visible in the admin composer.
  5. Optionally a completed delivery, so Report History is not empty.

Usage:
    venv/Scripts/python.exe scripts/seed_b2b_demo.py --email you@example.com
    venv/Scripts/python.exe scripts/seed_b2b_demo.py --email you@example.com --generate
    venv/Scripts/python.exe scripts/seed_b2b_demo.py --clean --email you@example.com
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

from app.core.config import get_settings  # noqa: E402

# Signal prefix so the seeded rows are identifiable and removable.
SCRIPT_PREFIX = "bi-demo-"

# The showcase: Malta appears 4x per month. Below the 5-per-segment floor in
# every individual month, but 12 across a quarter -- so it is suppressed in a
# monthly report and appears in a quarterly one. That is the whole point of
# storing raw monthly facts rather than rendered ones.
ABOVE_FLOOR_TERRITORY = "United Kingdom"
BELOW_FLOOR_TERRITORY = "Malta"
ABOVE_FLOOR_PER_MONTH = 9
BELOW_FLOOR_PER_MONTH = 4

GENRES = ["Drama", "Thriller", "Comedy"]
FORMATS = ["Feature Film", "TV Series", "Commercial"]
CAMERAS = ["ARRI Alexa 35", "RED V-Raptor", "Sony Venice 2"]
BUDGET_BANDS = ["250k-1M", "1M-5M", "5M-20M"]


def month_starts(count: int, *, ending_month_offset: int = 1) -> list[date]:
    """`count` consecutive month-starts, ending `ending_month_offset` months back.

    Defaults to ending with LAST month, because the delivery scheduler only ever
    reports on completed calendar months.
    """
    today = date.today()
    anchor_year, anchor_month = today.year, today.month - ending_month_offset
    while anchor_month <= 0:
        anchor_month += 12
        anchor_year -= 1
    months: list[date] = []
    for back in range(count - 1, -1, -1):
        m = anchor_month - back
        y = anchor_year
        while m <= 0:
            m += 12
            y -= 1
        months.append(date(y, m, 1))
    return months


def _signal_row(script_id: str, submitted: date, territory: str, index: int) -> dict:
    """One signal row.

    List-valued columns are JSON in this schema, so they are serialised here.
    Passing a raw Python list through textual SQL makes psycopg2 adapt it to a
    Postgres ARRAY, which the JSON column rejects.
    """
    return {
        "id": str(uuid4()),
        "script_id": script_id,
        "home_country": territory,
        "territory": territory,
        "territories_considered": json.dumps([territory, "Ireland"]),
        "territories_recommended": json.dumps([territory, "Hungary"]),
        "submission_date": submitted,
        "completion_window": f"{submitted.year}-{submitted.month:02d}",
        "camera_equipment": json.dumps([CAMERAS[index % len(CAMERAS)]]),
        "crew_size": 12 + (index % 5) * 9,
        "principal_cast": 3 + (index % 4),
        "supporting_cast": 6 + (index % 7),
        "background_extras": 20 + (index % 3) * 15,
        "budget_range": BUDGET_BANDS[index % len(BUDGET_BANDS)],
        "budget_amount_gbp": float(500_000 + (index % 6) * 750_000),
        "budget_currency": "GBP",
        "format": FORMATS[index % len(FORMATS)],
        "genres": json.dumps([GENRES[index % len(GENRES)]]),
        "target_audience": json.dumps(["Adult 18-34"]),
        "audience_segments": json.dumps(["Arthouse"]),
        "primary_languages": json.dumps(["English"]),
        "b2b_consent": True,
        "is_internal": False,
        "report_runs": 1,
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def clean(session: Session, user_id: str | None) -> None:
    deleted = session.execute(
        text("DELETE FROM production_signals WHERE script_id LIKE :p"),
        {"p": f"{SCRIPT_PREFIX}%"},
    ).rowcount
    print(f"  Removed {deleted} seeded signal(s).")
    session.execute(text("DELETE FROM b2b_monthly_aggregates"))
    print("  Cleared monthly aggregates.")
    if user_id:
        subs = session.execute(
            text("SELECT id FROM b2b_subscriptions WHERE user_id = :u"), {"u": user_id}
        ).fetchall()
        for (sub_id,) in subs:
            session.execute(
                text("DELETE FROM b2b_client_entitlements WHERE b2b_subscription_id = :s"),
                {"s": sub_id},
            )
        session.execute(
            text("DELETE FROM b2b_intelligence_requests WHERE user_id = :u"), {"u": user_id}
        )
        session.execute(text("DELETE FROM b2b_subscriptions WHERE user_id = :u"), {"u": user_id})
        print("  Removed this user's subscriptions, deliveries and entitlements.")
    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a local Business Intelligence demo")
    parser.add_argument("--email", required=True, help="Existing user's email to attach the contract to")
    parser.add_argument("--product", default="production_trend",
                        help="B2B product type (default: production_trend)")
    parser.add_argument("--cadence", default="quarterly", choices=["monthly", "quarterly"])
    parser.add_argument("--months", type=int, default=6, help="How many months of signals (default: 6)")
    parser.add_argument("--company", default="Grey Consortium UK")
    parser.add_argument("--extra-recipient", default=None, help="Second recipient on the distribution list")
    parser.add_argument("--generate", action="store_true",
                        help="Also generate one completed delivery so Report History is populated")
    parser.add_argument("--clean", action="store_true", help="Remove seeded data and exit")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.DB_URL)
    email = args.email.strip().lower()

    with Session(engine) as session:
        row = session.execute(
            text("SELECT id, email FROM users WHERE lower(email) = :e"), {"e": email}
        ).first()
        if not row:
            print(f"No user found for {email}.")
            print("Sign up in the app first, or run: python scripts/create_demo_account.py")
            sys.exit(1)
        user_id = row[0]

        if args.clean:
            print("Cleaning seeded Business Intelligence demo data...")
            clean(session, user_id)
            print("Done.")
            return

        months = month_starts(args.months)
        print(f"Seeding {args.months} month(s): {months[0]} .. {months[-1]}")

        # --- 1. signals -------------------------------------------------------
        session.execute(
            text("DELETE FROM production_signals WHERE script_id LIKE :p"),
            {"p": f"{SCRIPT_PREFIX}%"},
        )
        columns = list(_signal_row("x", months[0], "X", 0).keys())
        insert_sql = text(
            f"INSERT INTO production_signals ({', '.join(columns)}) "
            f"VALUES ({', '.join(':' + c for c in columns)})"
        )
        total = 0
        for month in months:
            for i in range(ABOVE_FLOOR_PER_MONTH):
                session.execute(insert_sql, _signal_row(
                    f"{SCRIPT_PREFIX}{month}-uk-{i}", month + timedelta(days=3 + i), ABOVE_FLOOR_TERRITORY, i))
                total += 1
            for i in range(BELOW_FLOOR_PER_MONTH):
                session.execute(insert_sql, _signal_row(
                    f"{SCRIPT_PREFIX}{month}-mt-{i}", month + timedelta(days=5 + i), BELOW_FLOOR_TERRITORY, i))
                total += 1

        # Governance rows that must NEVER surface in any report.
        excluded = _signal_row(f"{SCRIPT_PREFIX}internal", months[-1] + timedelta(days=2), "Narnia", 0)
        excluded["is_internal"] = True
        session.execute(insert_sql, excluded)
        unconsented = _signal_row(f"{SCRIPT_PREFIX}no-consent", months[-1] + timedelta(days=2), "Atlantis", 1)
        unconsented["b2b_consent"] = False
        session.execute(insert_sql, unconsented)
        session.commit()
        print(f"  {total} consented signals + 2 excluded control rows (internal / no-consent).")

        # --- 2. subscription --------------------------------------------------
        now = datetime.now(timezone.utc)
        existing = session.execute(
            text("SELECT id FROM b2b_subscriptions WHERE user_id = :u AND product_type = :p LIMIT 1"),
            {"u": user_id, "p": args.product},
        ).first()
        next_delivery = now + timedelta(days=2)
        if existing:
            sub_id = existing[0]
            session.execute(
                text("UPDATE b2b_subscriptions SET status='active', source='manual_contract', "
                     "delivery_frequency=:f, company_name=:c, extra_recipient_email=:x, "
                     "next_delivery_at=:n, cancel_at_period_end=FALSE, cancelled_at=NULL, "
                     "updated_at=:now WHERE id=:id"),
                {"f": args.cadence, "c": args.company, "x": args.extra_recipient,
                 "n": next_delivery, "now": now, "id": sub_id},
            )
            print(f"  Refreshed existing {args.product} subscription.")
        else:
            sub_id = str(uuid4())
            session.execute(
                text("INSERT INTO b2b_subscriptions (id, user_id, product_type, status, source, "
                     "delivery_frequency, extra_recipient_email, company_name, admin_notes, "
                     "current_period_start, next_delivery_at, cancel_at_period_end, created_at, updated_at) "
                     "VALUES (:id,:u,:p,'active','manual_contract',:f,:x,:c,:notes,:start,:n,FALSE,:now,:now)"),
                {"id": sub_id, "u": user_id, "p": args.product, "f": args.cadence,
                 "x": args.extra_recipient, "c": args.company,
                 "notes": "Seeded by scripts/seed_b2b_demo.py for local review.",
                 "start": now, "n": next_delivery, "now": now},
            )
            print(f"  Created manual-contract {args.product} subscription (no Stripe needed).")
        session.commit()

        # --- 3. exclusive entitlement ----------------------------------------
        session.execute(
            text("DELETE FROM b2b_client_entitlements WHERE b2b_subscription_id = :s AND module_key = 'ai_usage'"),
            {"s": sub_id},
        )
        session.execute(
            text("INSERT INTO b2b_client_entitlements (id, b2b_subscription_id, module_key, module_label, "
                 "section_keys, is_exclusive, reverts_at, notes, created_at, updated_at) "
                 "VALUES (:id,:s,'ai_usage','AI Usage Module',:keys,TRUE,:rev,:notes,:now,:now)"),
            {"id": str(uuid4()), "s": sub_id,
             "keys": '["sig_audience", "sig_audience_seg"]',
             "rev": date(2028, 6, 30),
             "notes": "Contract pack example: exclusive to this client until reversion.",
             "now": now},
        )
        session.commit()
        print("  Granted exclusive 'AI Usage Module' (reverts 2028-06-30).")

    # --- 4. monthly aggregates + optional delivery ---------------------------
    from app.core.database_client import create_client
    from app.modules.b2b.service import B2BService

    db = create_client()
    try:
        service = B2BService(db, settings)
        for month in months:
            record = service.build_monthly_aggregate(args.product, month)
            print(f"  Stored aggregate {month} -> {record['signal_count']} signals")

        quarter = months[-3:]
        composed = service.compose_from_months(args.product, quarter)
        visible = []
        for section in composed.get("sections", []):
            for r in section.get("rows", []):
                if r.get("label") == BELOW_FLOOR_TERRITORY:
                    visible.append(section["title"])
        print(f"\n  Quarterly composition ({quarter[0]} .. {quarter[-1]}): "
              f"{composed['source_signal_count']} signals")
        print(f"  '{BELOW_FLOOR_TERRITORY}' visible quarterly in: {visible or 'no sections'}")

        if args.generate:
            with Session(engine) as session:
                sub = session.execute(
                    text("SELECT id FROM b2b_subscriptions WHERE user_id=:u AND product_type=:p LIMIT 1"),
                    {"u": user_id, "p": args.product},
                ).first()
            request = service.create_intelligence_request(
                user_id=user_id,
                user_email=email,
                product_type=args.product,
                period_start=quarter[0],
                period_end=service._parse_date(
                    (quarter[-1].replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
                ),
                request_type="admin",
                subscription={"id": sub[0]} if sub else None,
            )
            service.process_request(request["id"])
            with Session(engine) as session:
                status = session.execute(
                    text("SELECT status FROM b2b_intelligence_requests WHERE id=:i"),
                    {"i": request["id"]},
                ).scalar()
            print(f"  Generated delivery {request['id']} -> status={status}")
    finally:
        db.close()

    print("\nReady. What to look at:")
    print(f"  /business-intelligence   the client dashboard (logged in as {email})")
    print("  /b2b                     catalogue + request history")
    print("  /admin/b2b               admin: subscriptions, entitlements, composer endpoints")


if __name__ == "__main__":
    main()
