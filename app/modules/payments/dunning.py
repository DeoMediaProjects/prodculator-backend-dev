"""Dunning grace task — runs hourly via APScheduler.

Stripe Smart Retries handles the actual retry attempts. This task enforces our
own grace period: 7 days after past_due_since, the user is downgraded to free
and the subscription is marked cancelled. Until then, RequirePlan continues to
grant entitlement so users can recover without losing access mid-flight.
"""

import logging
from datetime import datetime, timedelta, timezone

import redis as sync_redis

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.modules.email.service import EmailService

logger = logging.getLogger(__name__)

GRACE_PERIOD_DAYS = 7


def run_dunning_grace_check(supabase: DatabaseClient, settings: Settings) -> int:
    """Downgrade users whose payment has been past_due longer than the grace window.

    Returns the number of subscriptions downgraded — useful for tests and logs.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=GRACE_PERIOD_DAYS)

    result = (
        supabase.table("subscriptions")
        .select("id, user_id, stripe_subscription_id, past_due_since")
        .eq("status", "past_due")
        .lte("past_due_since", cutoff.isoformat())
        .execute()
    )
    rows = result.data or []
    if not rows:
        return 0

    email_service = EmailService(settings) if settings.BREVO_API_KEY else None
    downgraded = 0

    for row in rows:
        sub_id = row["id"]
        user_id = row.get("user_id")
        if not user_id:
            continue

        supabase.table("subscriptions").update(
            {
                "status": "cancelled",
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", sub_id).execute()

        supabase.table("users").update({"plan": "free", "user_type": "free"}).eq(
            "id", user_id
        ).execute()

        _bust_user_cache(user_id, settings)

        if email_service:
            try:
                user_row = (
                    supabase.table("users")
                    .select("email")
                    .eq("id", user_id)
                    .limit(1)
                    .execute()
                )
                user_data = (user_row.data or [{}])[0]
                if user_data.get("email"):
                    email_service.send(user_data["email"], "subscription_downgraded", {})
            except Exception as exc:
                logger.warning("dunning: email send failed for user %s: %s", user_id, exc)

        logger.warning(
            "dunning: downgraded user=%s subscription=%s past_due_since=%s",
            user_id,
            row.get("stripe_subscription_id"),
            row.get("past_due_since"),
        )
        downgraded += 1

    return downgraded


def _bust_user_cache(user_id: str, settings: Settings) -> None:
    try:
        r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
        r.delete(f"user_profile:{user_id}")
        r.close()
    except Exception as exc:
        logger.warning("dunning: cache bust failed for user %s: %s", user_id, exc)
