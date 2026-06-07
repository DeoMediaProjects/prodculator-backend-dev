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
from app.models.enums import normalize_plan
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


def _to_utc_datetime(value) -> datetime | None:
    """Coerce a stored period value into an aware UTC datetime for comparison.

    Handles the three shapes a local value can take: a datetime (from the
    timestamptz column in production), an ISO string (test fixtures / legacy
    writes), or None. Naive datetimes are assumed UTC, matching how the app
    always writes these fields.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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

    # Compare as instants, not as mixed types. The DB columns are timestamptz, so
    # local values come back as datetime objects — comparing them to an ISO string
    # would ALWAYS differ, making the reconciler "correct" the same period dates on
    # every run without ever converging.
    if period_start:
        stripe_start = datetime.fromtimestamp(period_start, tz=timezone.utc)
        if _to_utc_datetime(local.get("current_period_start")) != stripe_start:
            update["current_period_start"] = stripe_start.isoformat()
    if period_end:
        stripe_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
        if _to_utc_datetime(local.get("current_period_end")) != stripe_end:
            update["current_period_end"] = stripe_end.isoformat()

    resolved_plan = resolve_plan_from_subscription(stripe_sub, settings)

    # A pending downgrade is backed by a Stripe Subscription Schedule. Stripe
    # clears subscription.schedule once it completes/releases, so "we recorded a
    # schedule locally but Stripe no longer reports one" means the rollover has
    # already happened — the safety net for a missed customer.subscription.updated
    # webhook (#5).
    pending_plan = local.get("pending_plan")
    had_local_schedule = bool(local.get("stripe_schedule_id")) or bool(pending_plan)
    schedule_fired = had_local_schedule and not stripe_sub.get("schedule")

    # #7 — if the price→plan map can't resolve (a price ID isn't configured) but a
    # schedule we created has fired, fall back to the recorded pending_plan so a
    # config gap doesn't strand the user on their old tier.
    if not resolved_plan and schedule_fired and pending_plan:
        resolved_plan = normalize_plan(pending_plan)
        logger.error(
            "reconciler: price→plan resolution failed for %s; using recorded "
            "pending_plan=%s after schedule fired. Check Stripe price IDs in settings.",
            local.get("stripe_subscription_id"),
            resolved_plan,
        )

    if resolved_plan and resolved_plan != local.get("plan_type"):
        update["plan_type"] = resolved_plan
        update["report_limit"] = PLAN_REPORT_LIMITS.get(resolved_plan, 3)

    # #6 / #8 — clear stale pending markers once the backing schedule is gone,
    # regardless of which plan the rollover landed on.
    if schedule_fired:
        if local.get("pending_plan") is not None:
            update["pending_plan"] = None
        if local.get("stripe_schedule_id") is not None:
            update["stripe_schedule_id"] = None

    return update


def _bust_user_cache(user_id: str, settings: Settings) -> None:
    try:
        r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.delete(f"user_profile:{user_id}")
        r.close()
    except Exception as exc:
        logger.warning("reconciler: cache bust failed for user %s: %s", user_id, exc)
