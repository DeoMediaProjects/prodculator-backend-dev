"""Discover Stripe subscriptions missing from the local DB and backfill them.

Why this exists: webhooks were misconfigured for some period, so paid users
have active Stripe subscriptions with no local mirror. RequirePlan reads from
users.plan, which is mirrored by the webhook — so these users are gated as
'free' despite paying.

What it does:
  1. Iterate every Stripe customer (or a single user via --email).
  2. For each customer, list subscriptions and identify the one to keep
     (highest tier; tie-break: most recent).
  3. Upsert the kept subscription into the local DB.
  4. Update users.plan to the kept tier and bust the user-profile cache.
  5. Print duplicate subs that should probably be cancelled — but does NOT
     cancel them automatically. Run with --cancel-duplicates to do that.

Usage:
  python -m scripts.backfill_stripe_subscriptions --email <email>
  python -m scripts.backfill_stripe_subscriptions --email <email> --cancel-duplicates
  python -m scripts.backfill_stripe_subscriptions --all
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from uuid import uuid4

import stripe

from app.core.config import get_settings
from app.core.database_client import create_client
from app.modules.payments.plan_catalog import build_price_to_plan_map
from app.modules.payments.webhook_handler import PLAN_REPORT_LIMITS

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill")

PLAN_LEVEL = {"free": 0, "professional": 1, "producer": 2, "studio": 3}


def _stripe_period(sub) -> tuple[int | None, int | None]:
    start = sub.get("current_period_start")
    end = sub.get("current_period_end")
    if not start or not end:
        items = (sub.get("items") or {}).get("data") or []
        if items:
            start = start or items[0].get("current_period_start")
            end = end or items[0].get("current_period_end")
    return start, end


def _resolve_plan(sub, price_map) -> str | None:
    items = (sub.get("items") or {}).get("data") or []
    if not items:
        return None
    price_id = (items[0].get("price") or {}).get("id")
    return price_map.get(price_id)


def _select_kept_subscription(subs, price_map):
    """Pick highest-tier; tie-break by most recent created."""
    candidates = []
    for s in subs:
        plan = _resolve_plan(s, price_map)
        if not plan:
            continue
        candidates.append((PLAN_LEVEL.get(plan, 0), s.get("created", 0), plan, s))
    if not candidates:
        return None, None
    candidates.sort(reverse=True)
    _, _, plan, sub = candidates[0]
    return sub, plan


def backfill_for_user(db, settings, user_id: str, email: str, *, cancel_duplicates: bool):
    customers = stripe.Customer.list(email=email, limit=100).data
    active_subs = []
    for c in customers:
        for s in stripe.Subscription.list(customer=c.id, status="active", limit=100).data:
            active_subs.append(s)
        for s in stripe.Subscription.list(customer=c.id, status="trialing", limit=100).data:
            active_subs.append(s)
        for s in stripe.Subscription.list(customer=c.id, status="past_due", limit=100).data:
            active_subs.append(s)

    if not active_subs:
        logger.info("user %s (%s): no Stripe subscriptions", user_id, email)
        return

    price_map = build_price_to_plan_map(settings)
    kept_sub, kept_plan = _select_kept_subscription(active_subs, price_map)
    if not kept_sub:
        logger.warning(
            "user %s: %d Stripe subs but none mapped to a known plan price — skipping",
            user_id,
            len(active_subs),
        )
        return

    duplicates = [s for s in active_subs if s["id"] != kept_sub["id"]]
    logger.info(
        "user %s (%s): %d active Stripe subs found; keeping %s (%s)",
        user_id,
        email,
        len(active_subs),
        kept_sub["id"],
        kept_plan,
    )
    for d in duplicates:
        plan = _resolve_plan(d, price_map) or "?"
        logger.info(
            "  duplicate to consider cancelling: %s plan=%s amount=%s",
            d["id"],
            plan,
            (d.get("items", {}).get("data") or [{}])[0].get("price", {}).get("unit_amount"),
        )

    # Backfill the kept sub locally.
    period_start, period_end = _stripe_period(kept_sub)
    payload = {
        "user_id": user_id,
        "stripe_customer_id": kept_sub.get("customer"),
        "stripe_subscription_id": kept_sub["id"],
        "plan_type": kept_plan,
        "status": kept_sub.get("status"),
        "report_limit": PLAN_REPORT_LIMITS.get(kept_plan, 3),
        "cancel_at_period_end": kept_sub.get("cancel_at_period_end", False),
        "current_period_start": datetime.fromtimestamp(period_start, tz=timezone.utc).isoformat() if period_start else None,
        "current_period_end": datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat() if period_end else None,
    }

    existing = (
        db.table("subscriptions")
        .select("id")
        .eq("stripe_subscription_id", kept_sub["id"])
        .limit(1)
        .execute()
    )
    if existing.data:
        db.table("subscriptions").update(payload).eq(
            "stripe_subscription_id", kept_sub["id"]
        ).execute()
    else:
        payload["id"] = str(uuid4())
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        db.table("subscriptions").insert(payload).execute()

    user_type = "paid" if kept_plan != "free" else "free"
    db.table("users").update({"plan": kept_plan, "user_type": user_type}).eq(
        "id", user_id
    ).execute()

    # Bust the Redis profile cache so /me sees the new plan immediately.
    try:
        import redis as sync_redis

        r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.delete(f"user_profile:{user_id}")
        r.close()
    except Exception as exc:  # pragma: no cover
        logger.warning("cache bust failed: %s", exc)

    logger.info("  backfilled — users.plan = %s", kept_plan)

    if cancel_duplicates and duplicates:
        for d in duplicates:
            try:
                stripe.Subscription.cancel(d["id"])
                logger.info("  cancelled duplicate: %s", d["id"])
            except stripe.StripeError as exc:
                logger.error("  failed to cancel %s: %s", d["id"], exc)


def main():
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--email", help="Backfill a single user by email")
    g.add_argument("--all", action="store_true", help="Backfill every paid user")
    parser.add_argument(
        "--cancel-duplicates",
        action="store_true",
        help="Cancel duplicate Stripe subs (irreversible — keep the kept one only)",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        sys.exit("STRIPE_SECRET_KEY not set")
    stripe.api_key = settings.STRIPE_SECRET_KEY

    db = create_client()
    try:
        if args.email:
            row = db.table("users").select("id, email").eq("email", args.email).limit(1).execute()
            if not row.data:
                sys.exit(f"User not found: {args.email}")
            backfill_for_user(
                db,
                settings,
                row.data[0]["id"],
                row.data[0]["email"],
                cancel_duplicates=args.cancel_duplicates,
            )
        else:
            users = db.table("users").select("id, email").execute().data or []
            for u in users:
                if not u.get("email"):
                    continue
                backfill_for_user(
                    db, settings, u["id"], u["email"],
                    cancel_duplicates=args.cancel_duplicates,
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()
