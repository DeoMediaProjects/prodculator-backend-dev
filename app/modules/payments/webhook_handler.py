import logging
from datetime import datetime, timezone
from uuid import uuid4

import redis as sync_redis

from app.core.database_client import DatabaseClient

from app.core.config import Settings
from app.models.enums import normalize_plan
from app.modules.email.service import EmailService
from app.modules.payments.plan_catalog import resolve_plan_from_subscription

# Map plan types to their report limits per billing period
PLAN_REPORT_LIMITS: dict[str, int] = {
    "free": 1,
    "professional": 1,
    "producer": 3,
    "studio": 10,
}

logger = logging.getLogger(__name__)


class WebhookHandler:
    def __init__(self, supabase: DatabaseClient, settings: Settings | None = None):
        self.supabase = supabase
        self.settings = settings
        self.email_service = EmailService(settings) if settings else None

    def handle_event(self, event_id: str, event_type: str, data_object: dict) -> None:
        """Dispatch webhook event to appropriate handler.

        event_id is the Stripe event ID used for idempotency deduplication.
        """
        # Deduplication: skip events we've already processed.
        existing = (
            self.supabase.table("processed_webhook_events")
            .select("event_id")
            .eq("event_id", event_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            logger.info("Skipping duplicate webhook event: %s", event_id)
            return

        self.supabase.table("processed_webhook_events").insert(
            {"event_id": event_id, "processed_at": datetime.now(timezone.utc).isoformat()}
        ).execute()

        handlers = {
            "checkout.session.completed": self._handle_checkout_completed,
            "payment_intent.succeeded": self._handle_payment_succeeded,
            "payment_intent.payment_failed": self._handle_payment_failed,
            "customer.subscription.created": self._handle_subscription_updated,
            "customer.subscription.updated": self._handle_subscription_updated,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "invoice.paid": self._handle_invoice_paid,
            "invoice.payment_failed": self._handle_invoice_payment_failed,
        }
        handler = handlers.get(event_type)
        if handler:
            handler(data_object)
        else:
            logger.info("Unhandled webhook event: %s", event_type)

    def _handle_checkout_completed(self, session: dict) -> None:
        user_id = session.get("metadata", {}).get("userId")
        if not user_id:
            logger.warning("checkout.session.completed missing metadata.userId")
            return

        # One-time purchase (pay-per-report) — increment credits_remaining.
        if session.get("mode") == "payment":
            self._handle_credit_purchase(user_id)
            return

        raw_plan = session.get("metadata", {}).get("planType", "professional")
        plan_type = normalize_plan(raw_plan)
        stripe_subscription_id = session.get("subscription")
        report_limit = PLAN_REPORT_LIMITS.get(plan_type, 3)

        self.supabase.table("subscriptions").upsert(
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "stripe_customer_id": session.get("customer"),
                "stripe_subscription_id": stripe_subscription_id,
                "plan_type": plan_type,
                "status": "active",
                "report_limit": report_limit,
                "cancel_at_period_end": False,
                "current_period_start": datetime.now(timezone.utc).isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="stripe_subscription_id",
        ).execute()

        # Update user record so /api/auth/me reflects the new plan
        user_type = "paid" if plan_type != "free" else "free"
        self.supabase.table("users").update(
            {"plan": plan_type, "user_type": user_type}
        ).eq("id", user_id).execute()
        self._bust_user_cache(user_id)

        self._send_email_to_user_id(
            user_id,
            "payment_confirmation",
            {
                "plan_type": plan_type,
                "stripe_customer_id": session.get("customer"),
                "stripe_subscription_id": stripe_subscription_id,
            },
        )

    def _handle_credit_purchase(self, user_id: str) -> None:
        """Increment credits_remaining by 1 for a pay-per-report purchase."""
        result = (
            self.supabase.table("users")
            .select("credits_remaining")
            .eq("id", user_id)
            .single()
            .execute()
        )
        current = (result.data or {}).get("credits_remaining", 0) or 0
        self.supabase.table("users").update(
            {"credits_remaining": current + 1}
        ).eq("id", user_id).execute()
        self._bust_user_cache(user_id)
        logger.info("Pay-per-report credit added for user=%s (now %d)", user_id, current + 1)

        self._send_email_to_user_id(user_id, "payment_confirmation", {"payment_type": "credit"})

    def _handle_payment_succeeded(self, payment_intent: dict) -> None:
        logger.info("Payment succeeded: %s", payment_intent.get("id"))

    def _handle_payment_failed(self, payment_intent: dict) -> None:
        logger.error("Payment failed: %s", payment_intent.get("id"))

    def _handle_subscription_updated(self, subscription: dict) -> None:
        subscription_id = subscription.get("id")
        if not subscription_id:
            logger.warning("subscription event missing id")
            return

        # Newer Stripe API versions moved period dates onto the subscription item.
        period_start = subscription.get("current_period_start")
        period_end = subscription.get("current_period_end")
        if not period_start or not period_end:
            items = (subscription.get("items") or {}).get("data") or []
            if items:
                period_start = period_start or items[0].get("current_period_start")
                period_end = period_end or items[0].get("current_period_end")

        payload: dict = {
            "status": subscription.get("status"),
            "cancel_at_period_end": subscription.get("cancel_at_period_end", False),
        }
        if period_start:
            payload["current_period_start"] = datetime.fromtimestamp(period_start, tz=timezone.utc).isoformat()
        if period_end:
            payload["current_period_end"] = datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat()

        # Resolve the plan from the active item's price ID — the source of truth
        # that survives mid-cycle modifies and metadata drift. Without this,
        # upgrades via Subscription.modify or the Customer Portal silently fail
        # to propagate to user.plan / RequirePlan entitlements.
        resolved_plan: str | None = None
        if self.settings:
            resolved_plan = resolve_plan_from_subscription(subscription, self.settings)
        if resolved_plan:
            payload["plan_type"] = resolved_plan
            payload["report_limit"] = PLAN_REPORT_LIMITS.get(resolved_plan, 3)

        result = (
            self.supabase.table("subscriptions")
            .update(payload)
            .eq("stripe_subscription_id", subscription_id)
            .execute()
        )

        rows = result.data or []
        if not rows:
            # No row matched — skip creating a stub. The checkout.session.completed
            # handler creates the row with the correct user_id and plan_type.
            logger.info(
                "subscription.updated received before checkout.session.completed for %s — skipping",
                subscription_id,
            )
            return

        if not resolved_plan:
            return

        row = rows[0]
        user_id = row.get("user_id")
        if not user_id:
            return

        # If a downgrade was scheduled (pending_plan set) and the now-active plan
        # matches it, the rollover has happened — clear the pending marker.
        if row.get("pending_plan") == resolved_plan:
            self.supabase.table("subscriptions").update({"pending_plan": None}).eq(
                "stripe_subscription_id", subscription_id
            ).execute()

        # Mirror the resolved plan onto the user row so RequirePlan reads correct
        # entitlement on the next request. Bust the 5-min profile cache.
        user_type = "paid" if resolved_plan != "free" else "free"
        self.supabase.table("users").update(
            {"plan": resolved_plan, "user_type": user_type}
        ).eq("id", user_id).execute()
        self._bust_user_cache(user_id)

    def _handle_subscription_deleted(self, subscription: dict) -> None:
        subscription_id = subscription.get("id")
        if not subscription_id:
            logger.warning("customer.subscription.deleted missing id")
            return
        result = self.supabase.table("subscriptions").update(
            {
                "status": "cancelled",
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("stripe_subscription_id", subscription_id).execute()

        # Only downgrade the user if they have NO other active subscription.
        # During an upgrade (e.g. professional → producer), Stripe deletes the
        # old subscription after the new one is active; downgrading blindly
        # here would clobber the new plan written by checkout.session.completed.
        rows = result.data or []
        if not rows:
            return
        user_id = rows[0].get("user_id")
        if not user_id:
            return

        active = (
            self.supabase.table("subscriptions")
            .select("id")
            .eq("user_id", user_id)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if active.data:
            logger.info(
                "subscription %s deleted but user %s still has an active subscription — keeping plan",
                subscription_id,
                user_id,
            )
            return

        self.supabase.table("users").update(
            {"plan": "free", "user_type": "free"}
        ).eq("id", user_id).execute()
        self._bust_user_cache(user_id)

    def _handle_invoice_paid(self, invoice: dict) -> None:
        logger.info("Invoice paid: %s", invoice.get("id"))
        customer_id = invoice.get("customer")
        if not customer_id:
            return
        subscription_result = (
            self.supabase.table("subscriptions")
            .select("user_id, past_due_since")
            .eq("stripe_customer_id", customer_id)
            .limit(1)
            .execute()
        )
        rows = subscription_result.data or []
        if not rows:
            return

        row = rows[0]
        user_id = row["user_id"]
        was_past_due = row.get("past_due_since") is not None

        if was_past_due:
            # Recovery from a previous payment failure — clear past_due_since,
            # restore active status, and notify the user.
            self.supabase.table("subscriptions").update(
                {"status": "active", "past_due_since": None}
            ).eq("stripe_customer_id", customer_id).execute()
            self._send_email_to_user_id(
                user_id,
                "subscription_recovered",
                {"invoice_id": invoice.get("id")},
            )
        else:
            self._send_email_to_user_id(
                user_id,
                "payment_confirmation",
                {
                    "invoice_id": invoice.get("id"),
                    "amount_paid": invoice.get("amount_paid"),
                },
            )

    def _handle_invoice_payment_failed(self, invoice: dict) -> None:
        customer_id = invoice.get("customer")
        if not customer_id:
            logger.warning("invoice.payment_failed missing customer id")
            return

        # Only set past_due_since on the first failure of a streak. On retries,
        # the existing timestamp is what the dunning grace task measures from.
        existing = (
            self.supabase.table("subscriptions")
            .select("user_id, past_due_since")
            .eq("stripe_customer_id", customer_id)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        if not rows:
            logger.warning("invoice.payment_failed: no subscription for customer %s", customer_id)
            return

        row = rows[0]
        update: dict = {"status": "past_due"}
        if not row.get("past_due_since"):
            update["past_due_since"] = datetime.now(timezone.utc).isoformat()

        self.supabase.table("subscriptions").update(update).eq(
            "stripe_customer_id", customer_id
        ).execute()
        logger.error("Invoice payment failed: %s", invoice.get("id"))

        user_id = row.get("user_id")
        if user_id:
            self._send_email_to_user_id(
                user_id,
                "payment_failed",
                {
                    "invoice_id": invoice.get("id"),
                    "amount_due": invoice.get("amount_due"),
                    "next_payment_attempt": invoice.get("next_payment_attempt"),
                },
            )

    def _bust_user_cache(self, user_id: str) -> None:
        """Delete the Redis user-profile cache so /api/auth/me reads fresh DB data."""
        if not self.settings:
            return
        try:
            r = sync_redis.from_url(self.settings.REDIS_URL, decode_responses=True)
            r.delete(f"user_profile:{user_id}")
            r.close()
        except Exception as exc:
            logger.warning("Cache bust failed for user %s: %s", user_id, exc)

    def _send_email_to_user_id(self, user_id: str, template_name: str, context: dict) -> None:
        if not self.email_service:
            return
        try:
            user_result = (
                self.supabase.table("users").select("email").eq("id", user_id).limit(1).execute()
            )
            rows = user_result.data or []
            if not rows or not rows[0].get("email"):
                return
            self.email_service.send(rows[0]["email"], template_name, context)
        except Exception as exc:
            logger.warning("Unable to send webhook email (%s): %s", template_name, exc)
