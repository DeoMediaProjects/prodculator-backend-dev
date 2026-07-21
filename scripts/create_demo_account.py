#!/usr/bin/env python3
"""Seed (or refresh) a Studio demo account for investor walkthroughs.

Creates a user whose email contains "demomedia", on the Studio plan, with an
active Studio subscription and a stock of pay-per-report credits — so a client
can demo the full authenticated experience on the live deployment.

Idempotent: re-running updates the existing account in place (plan, credits,
password, subscription window) rather than erroring or duplicating rows.

Usage:
    python scripts/create_demo_account.py
    python scripts/create_demo_account.py --email demo@demomedia.com --password 'DemoMedia2026!' --credits 100
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Allow running from the project root.
sys.path.insert(0, ".")

from app.core.config import get_settings
from app.core.security import hash_password

DEFAULT_EMAIL = "demo@demomedia.net"
DEFAULT_PASSWORD = "DemoMedia2026!"
PLAN = "studio"
# Studio's per-period report limit (see app/modules/payments/plan_catalog.py).
# Credits act as overflow once the period limit is hit, so a demo effectively
# has limit + credits worth of full reports.
STUDIO_REPORT_LIMIT = 10


def create_demo_account(email: str, password: str, credits: int, name: str) -> None:
    email = email.strip().lower()
    if "demomedia" not in email:
        print("Error: demo email must contain 'demomedia'.")
        sys.exit(1)

    settings = get_settings()
    engine = create_engine(settings.DB_URL)

    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=365)
    pw_hash = hash_password(password)

    with Session(engine) as session:
        existing = session.execute(
            text("SELECT id FROM users WHERE lower(email) = lower(:email)"),
            {"email": email},
        ).first()

        if existing:
            user_id = existing[0]
            session.execute(
                text(
                    "UPDATE users SET password_hash = :pw, name = :name, user_type = 'paid', "
                    "plan = :plan, credits_remaining = :credits, email_verified = TRUE, "
                    "is_blocked = FALSE, blocked_at = NULL WHERE id = :id"
                ),
                {"pw": pw_hash, "name": name, "plan": PLAN, "credits": credits, "id": user_id},
            )
            print(f"Updated existing demo user ({email}).")
        else:
            user_id = str(uuid4())
            session.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, name, user_type, "
                    "credits_remaining, plan, email_verified, is_blocked, created_at) "
                    "VALUES (:id, :email, :pw, :name, 'paid', :credits, :plan, TRUE, FALSE, :created_at)"
                ),
                {
                    "id": user_id,
                    "email": email,
                    "pw": pw_hash,
                    "name": name,
                    "credits": credits,
                    "plan": PLAN,
                    "created_at": now,
                },
            )
            print(f"Created demo user ({email}).")

        # Active Studio subscription so plan gates unlock and reports are metered
        # against the Studio period limit (with credits as overflow).
        sub = session.execute(
            text(
                "SELECT id FROM subscriptions WHERE user_id = :uid "
                "AND status IN ('active', 'trialing', 'past_due') LIMIT 1"
            ),
            {"uid": user_id},
        ).first()

        if sub:
            session.execute(
                text(
                    "UPDATE subscriptions SET plan_type = :plan, status = 'active', "
                    "report_limit = :limit, current_period_start = :start, "
                    "current_period_end = :end, cancel_at_period_end = FALSE, "
                    "cancelled_at = NULL, pending_plan = NULL, past_due_since = NULL "
                    "WHERE id = :id"
                ),
                {"plan": PLAN, "limit": STUDIO_REPORT_LIMIT, "start": now, "end": period_end, "id": sub[0]},
            )
            print("Refreshed existing subscription -> Studio (active).")
        else:
            session.execute(
                text(
                    "INSERT INTO subscriptions (id, user_id, plan_type, status, report_limit, "
                    "current_period_start, current_period_end, cancel_at_period_end, created_at) "
                    "VALUES (:id, :uid, :plan, 'active', :limit, :start, :end, FALSE, :created_at)"
                ),
                {
                    "id": str(uuid4()),
                    "uid": user_id,
                    "plan": PLAN,
                    "limit": STUDIO_REPORT_LIMIT,
                    "start": now,
                    "end": period_end,
                    "created_at": now,
                },
            )
            print("Created Studio subscription (active).")

        session.commit()

    print("\nDemo account ready:")
    print(f"  Email:    {email}")
    print(f"  Password: {password}")
    print(f"  Plan:     Studio (active subscription, {STUDIO_REPORT_LIMIT} reports/period)")
    print(f"  Credits:  {credits} pay-per-report credits")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a Studio demo account")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help=f"Demo email (default: {DEFAULT_EMAIL}); must contain 'demomedia'")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Demo password")
    parser.add_argument("--credits", type=int, default=100, help="Pay-per-report credits (default: 100)")
    parser.add_argument("--name", default="Demo Media", help="Display name")
    args = parser.parse_args()

    create_demo_account(email=args.email, password=args.password, credits=args.credits, name=args.name)


if __name__ == "__main__":
    main()
