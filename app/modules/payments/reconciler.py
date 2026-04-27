"""Subscription reconciler — runs hourly via APScheduler.

Webhooks are best-effort; deploys, network blips, and out-of-order delivery can
leave local state diverged from Stripe. The reconciler iterates active/trialing/
past_due subscriptions, fetches the live Stripe subscription, and corrects any
drift in status, plan_type, period dates, and the mirrored user.plan field.

Each correction is logged at WARNING so we notice if drift becomes routine
(which would indicate a deeper webhook problem).
"""

import logging
from datetime import datetime, timezone

import redis as sync_redis
import stripe

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.modules.payments.plan_catalog import resolve_plan_from_subscription
from app.modules.payments.webhook_handler import PLAN_REPORT_LIMITS

logger = logging.getLogger(__name__)

RECONCILE_STATUSES = ("active", "trialing", "past_due")


def run_subscription_reconciler(supabase: DatabaseClient, settings: Settings) -> int:
    """Pull the current Stripe state for each tracked subscription and fix drift.

    Returns the number of subscriptions that were corrected.
    """
    if not settings.STRIPE_SECRET_KEY:
        logger.info("reconciler: STRIPE_SECRET_KEY not set — skipping run")
        return 0

    stripe.api_key = settings.STRIPE_SECRET_KEY

    result = (
        supabase.table("subscriptions")
        .select("*")
        .in_("status", list(RECONCILE_STATUSES))
        .execute()
    )
    rows = result.data or []
    fixed = 0

    for row in rows:
        sub_id = row.get("stripe_subscription_id")
        if not sub_id:
            continue
        try:
            stripe_sub = stripe.Subscription.retrieve(sub_id)
        except stripe.StripeError as exc:
            logger.warning("reconciler: Stripe fetch failed for %s: %s", sub_id, exc)
            continue

        update = _diff_subscription(row, stripe_sub, settings)
        if not update:
            continue

        supabase.table("subscriptions").update(update).eq("id", row["id"]).execute()

        new_plan = update.get("plan_type")
        user_id = row.get("user_id")
        if new_plan and user_id:
            user_type = "paid" if new_plan != "free" else "free"
            supabase.table("users").update(
                {"plan": new_plan, "user_type": user_type}
            ).eq("id", user_id).execute()
            _bust_user_cache(user_id, settings)

        logger.warning(
            "reconciler: corrected subscription=%s drift=%s",
            sub_id,
            list(update.keys()),
        )
        fixed += 1

    return fixed


def _diff_subscription(local: dict, stripe_sub, settings: Settings) -> dict:
    """Return only the fields where local diverges from Stripe."""
    update: dict = {}

    stripe_status = stripe_sub.get("status")
    if stripe_status and stripe_status != local.get("status"):
        update["status"] = stripe_status

    cancel_at_period_end = stripe_sub.get("cancel_at_period_end", False)
    if cancel_at_period_end != local.get("cancel_at_period_end"):
        update["cancel_at_period_end"] = cancel_at_period_end

    period_start = stripe_sub.get("current_period_start")
    period_end = stripe_sub.get("current_period_end")
    if not period_start or not period_end:
        items = (stripe_sub.get("items") or {}).get("data") or []
        if items:
            period_start = period_start or items[0].get("current_period_start")
            period_end = period_end or items[0].get("current_period_end")

    if period_start:
        iso = datetime.fromtimestamp(period_start, tz=timezone.utc).isoformat()
        if iso != local.get("current_period_start"):
            update["current_period_start"] = iso
    if period_end:
        iso = datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat()
        if iso != local.get("current_period_end"):
            update["current_period_end"] = iso

    resolved_plan = resolve_plan_from_subscription(stripe_sub, settings)
    if resolved_plan and resolved_plan != local.get("plan_type"):
        update["plan_type"] = resolved_plan
        update["report_limit"] = PLAN_REPORT_LIMITS.get(resolved_plan, 3)

    return update


def _bust_user_cache(user_id: str, settings: Settings) -> None:
    try:
        r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.delete(f"user_profile:{user_id}")
        r.close()
    except Exception as exc:
        logger.warning("reconciler: cache bust failed for user %s: %s", user_id, exc)
