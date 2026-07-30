#!/usr/bin/env python3
"""Seed 10 Studio-tier demo accounts capped at 3 reports each, for the live site.

Each account gets full feature access (Studio is the top plan, so every
RequirePlan gate, unlimited territories and the investor summary all unlock)
but a hard ceiling of 3 reports that never replenishes.

Two details make the cap actually hold, and both are easy to get wrong:

1. ``credits_remaining`` is forced to 0. Pay-per-report credits act as overflow
   once the period limit is hit (see SubscriptionService.can_generate_report),
   so a demo account holding credits would sail straight past its 3 reports.
   scripts/create_demo_account.py grants 100 credits by design — do not copy
   that here.

2. The subscription period runs for ``--period-years`` (default 10) rather than
   a month. Quota is counted per period, so a 30-day window would hand each
   account 3 fresh reports every month. A decade-long window makes 3 effectively
   a lifetime total.

The subscription rows are written with ``stripe_subscription_id`` NULL on
purpose: the payments reconciler skips rows without one
(app/modules/payments/reconciler.py), so these manual grants are never
overwritten by Stripe drift correction.

Idempotent — re-running updates accounts in place rather than duplicating them.

Usage (run where DB_URL resolves, i.e. on Railway, not a laptop):
    python scripts/create_demo_accounts.py --dry-run
    python scripts/create_demo_accounts.py
    python scripts/create_demo_accounts.py --count 10 --reports 3 --reset-usage
"""
import argparse
import secrets
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Allow running from the project root.
sys.path.insert(0, ".")

from app.core.config import get_settings
from app.core.security import hash_password

EMAIL_DOMAIN = "deomedia.net"
EMAIL_PREFIX = "demo"
PLAN = "studio"
DEFAULT_COUNT = 10
DEFAULT_REPORTS = 3
DEFAULT_PERIOD_YEARS = 10

# Ambiguous glyphs removed — these passwords get read aloud and typed by hand
# at demos, so 0/O and 1/l/I cause more support pain than they add entropy.
_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_PASSWORD_LENGTH = 14


def generate_password() -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH))


def upsert_account(
    session: Session,
    *,
    email: str,
    password: str,
    name: str,
    report_limit: int,
    now: datetime,
    period_end: datetime,
    reset_usage: bool,
) -> str:
    """Create or refresh one demo account. Returns 'created' or 'updated'."""
    existing = session.execute(
        text("SELECT id FROM users WHERE lower(email) = lower(:email)"),
        {"email": email},
    ).first()

    if existing:
        user_id = existing[0]
        session.execute(
            text(
                "UPDATE users SET password_hash = :pw, name = :name, user_type = 'paid', "
                "plan = :plan, credits_remaining = 0, email_verified = TRUE, "
                "is_blocked = FALSE, blocked_at = NULL WHERE id = :id"
            ),
            {"pw": hash_password(password), "name": name, "plan": PLAN, "id": user_id},
        )
        action = "updated"
    else:
        user_id = str(uuid4())
        session.execute(
            text(
                "INSERT INTO users (id, email, password_hash, name, user_type, "
                "credits_remaining, plan, email_verified, is_blocked, created_at) "
                "VALUES (:id, :email, :pw, :name, 'paid', 0, :plan, TRUE, FALSE, :created_at)"
            ),
            {
                "id": user_id,
                "email": email,
                "pw": hash_password(password),
                "name": name,
                "plan": PLAN,
                "created_at": now,
            },
        )
        action = "created"

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
            {"plan": PLAN, "limit": report_limit, "start": now, "end": period_end, "id": sub[0]},
        )
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
                "limit": report_limit,
                "start": now,
                "end": period_end,
                "created_at": now,
            },
        )

    if reset_usage:
        # Void rather than delete: the ledger is append-only by design, and
        # count_usage already ignores voided rows.
        session.execute(
            text(
                "UPDATE report_usage_events SET voided_at = :now "
                "WHERE user_id = :uid AND voided_at IS NULL"
            ),
            {"now": now, "uid": user_id},
        )

    return action


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed Studio demo accounts with a hard report cap"
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"Number of demo accounts (default: {DEFAULT_COUNT})")
    parser.add_argument("--reports", type=int, default=DEFAULT_REPORTS,
                        help=f"Reports allowed per account (default: {DEFAULT_REPORTS})")
    parser.add_argument("--period-years", type=int, default=DEFAULT_PERIOD_YEARS,
                        help=f"Quota period length (default: {DEFAULT_PERIOD_YEARS})")
    parser.add_argument("--domain", default=EMAIL_DOMAIN, help=f"Email domain (default: {EMAIL_DOMAIN})")
    parser.add_argument("--reset-usage", action="store_true",
                        help="Void existing report usage so re-run accounts start at 0/3 again")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be written and roll back without committing")
    args = parser.parse_args()

    if args.reports < 1:
        print("Error: --reports must be at least 1.")
        sys.exit(1)

    settings = get_settings()
    engine = create_engine(settings.DB_URL)

    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=365 * args.period_years)

    accounts = [
        {
            "email": f"{EMAIL_PREFIX}{i}@{args.domain}",
            "password": generate_password(),
            "name": f"Demo Account {i}",
        }
        for i in range(1, args.count + 1)
    ]

    with Session(engine) as session:
        results = []
        for account in accounts:
            action = upsert_account(
                session,
                email=account["email"],
                password=account["password"],
                name=account["name"],
                report_limit=args.reports,
                now=now,
                period_end=period_end,
                reset_usage=args.reset_usage,
            )
            results.append(action)

        if args.dry_run:
            session.rollback()
        else:
            session.commit()

    mode = "DRY RUN — nothing was written" if args.dry_run else "Committed"
    print(f"\n{mode}")
    print(f"Plan: {PLAN} (full feature access) | Reports: {args.reports} per account, "
          f"non-renewing for {args.period_years} years | Credits: 0\n")
    print(f"{'EMAIL':<28} {'PASSWORD':<16} {'ACTION'}")
    print("-" * 56)
    for account, action in zip(accounts, results):
        print(f"{account['email']:<28} {account['password']:<16} {action}")

    print(
        "\nPasswords are shown once and are not recoverable — save them now. "
        "Re-running this script issues new passwords."
    )


if __name__ == "__main__":
    main()
