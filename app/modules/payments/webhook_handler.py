import logging
from datetime import datetime, timezone
from uuid import uuid4

import redis as sync_redis

from app.core.database_client import DatabaseClient

from app.core.config import Settings
from app.models.enums import normalize_plan
from app.modules.email.service import EmailService
from app.modules.payments.plan_catalog import PLAN_REPORT_LIMITS, resolve_plan_from_subscription

logger = logging.getLogger(__name__)

_PLAN_LABEL: dict[str, str] = {
    "free": "Explorer",
    "professional": "Professional",
    "producer": "Producer",
    "studio": "Studio",
}


def _billing_geo_from_session(session: dict) -> dict[str, str]:
    """Extract billing country/state from a Stripe checkout session.

    Stripe populates ``customer_details.address`` with whatever billing address
    the customer entered. Returns only the keys that are present so an absent
    address never blanks out a value already on the user row.
    """
    address = (session.get("customer_details") or {}).get("address") or {}
    geo: dict[str, str] = {}
    if address.get("country"):
        geo["country"] = address["country"]
    if address.get("state"):
        geo["state"] = address["state"]
    return geo


class WebhookHandler:
    def __init__(
        self,
        supabase: DatabaseClient,
        settings: Settings | None = None,
        background_tasks=None,
    ):
        self.supabase = supabase
        self.settings = settings
        self.email_service = EmailService(settings) if settings else None
        # Optional FastAPI BackgroundTasks. When supplied, the slow SMTP send is
        # deferred until after the webhook responds 200 to Stripe, so a slow
        # email provider can't push the response past Stripe's delivery timeout.
        self.background_tasks = background_tasks

    def handle_event(self, event_id: str, event_type: str, data_object: dict) -> None:
        """Dispatch webhook event to appropriate handler.

        event_id is the Stripe event ID used for idempotency deduplication.
        """
        # Deduplication: skip events we've already fully processed. The marker is
        # recorded only AFTER the handler succeeds (see end of method) — if it
        # were written first, a mid-handler failure would mark the event done and
        # Stripe's retry would be skipped, leaving a customer charged but never
        # upgraded.
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

        handlers = {
            "checkout.session.completed": self._handle_checkout_completed,
            "payment_intent.succeeded": self._handle_payment_succeeded,
            "payment_intent.payment_failed": self._handle_payment_failed,
            "customer.subscription.created": self._handle_subscription_updated,
            "customer.subscription.updated": self._handle_subscription_updated,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "invoice.paid": self._handle_invoice_paid,
            "invoice.payment_failed": self._handle_invoice_payment_failed,
            # Subscription Schedule events
            "subscription_schedule.released": self._handle_schedule_released,
        }
        handler = handlers.get(event_type)
        if handler:
            # Let exceptions propagate: the route returns 500 and Stripe retries.
            # Because the dedup marker below has not been written yet, the retry
            # reprocesses the event. The handlers' DB writes are idempotent
            # (upsert on stripe_subscription_id, plan set to a fixed value), so
            # reprocessing converges rather than double-applying.
            handler(data_object)
        else:
            logger.info("Unhandled webhook event: %s", event_type)

        # Record the marker now that the work has committed. Tolerate a unique
        # violation from a concurrent delivery of the same event — the PRIMARY
        # KEY on event_id makes the second insert raise. Both deliveries ran the
        # same idempotent work, so the loser just moves on.
        try:
            self.supabase.table("processed_webhook_events").insert(
                {"event_id": event_id, "processed_at": datetime.now(timezone.utc)}
            ).execute()
        except Exception as exc:
            # Clear the failed transaction so the request's session is reusable.
            try:
                self.supabase.session.rollback()
            except Exception:
                logger.debug("Session rollback after webhook write failure also failed", exc_info=True)
            logger.info("Event %s already recorded by a concurrent delivery: %s", event_id, exc)

    def _handle_checkout_completed(self, session: dict) -> None:
        metadata = session.get("metadata", {}) or {}
        user_id = metadata.get("userId")
        if not user_id:
            logger.error(
                "CRITICAL: checkout.session.completed missing metadata.userId. "
                "Session ID: %s, Customer: %s. User was charged but cannot be upgraded!",
                session.get("id"),
                session.get("customer")
            )
            return

        # One-time purchase (pay-per-report) — increment credits_remaining.
        if session.get("mode") == "payment":
            self._handle_credit_purchase(user_id)
            return

        if metadata.get("subscriptionKind") == "b2b":
            self._handle_b2b_checkout_completed(session)
            return

        raw_plan = metadata.get("planType", "professional")
        plan_type = normalize_plan(raw_plan)
        stripe_subscription_id = session.get("subscription")
        report_limit = PLAN_REPORT_LIMITS.get(plan_type, 3)

        # Fetch previous plan before overwriting so we can send an accurate upgrade email
        try:
            prev_result = (
                self.supabase.table("users").select("plan").eq("id", user_id).limit(1).execute()
            )
            previous_plan = normalize_plan((prev_result.data or [{}])[0].get("plan") or "free")
        except Exception:
            previous_plan = "free"

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

        # Update user record so /api/auth/me reflects the new plan. This is the
        # CRITICAL entitlement write — the customer has paid and must receive what
        # they bought — so it carries ONLY the plan fields. Anything non-essential
        # (e.g. billing geography) is written separately below so it can never
        # fail this UPDATE and strand a paying customer on the free tier.
        user_type = "paid" if plan_type != "free" else "free"
        self.supabase.table("users").update(
            {"plan": plan_type, "user_type": user_type}
        ).eq("id", user_id).execute()

        # Best-effort, analytics-only: capture billing country/state for the admin
        # Business Metrics dashboard. Isolated from the upgrade above so a missing
        # column or a bad address value degrades gracefully instead of 500-ing the
        # webhook (which would make Stripe retry forever, never upgrading the user).
        self._capture_billing_geo(user_id, session)

        # Bust cache AFTER all critical writes are confirmed successful. This ensures
        # that when /api/auth/me reads from the DB (cache miss), it sees the upgraded plan.
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

        # Send plan upgrade email whenever the user lands on a paid plan via checkout.
        # Skip if they're re-subscribing to the exact same plan (e.g. after cancellation).
        if plan_type != "free" and plan_type != previous_plan:
            self._send_email_to_user_id(
                user_id,
                "plan_upgraded",
                {
                    "previous_plan_name": _PLAN_LABEL.get(previous_plan, previous_plan.title()),
                    "new_plan_name": _PLAN_LABEL.get(plan_type, plan_type.title()),
                },
            )

    def _capture_billing_geo(self, user_id: str, session: dict) -> None:
        """Persist billing country/state from the checkout session, best-effort.

        Never raises: the plan upgrade has already committed by the time this runs,
        and geography is analytics-only. On failure we roll back the poisoned
        transaction so the shared request session stays usable for the dedup insert
        and email lookup that follow.
        """
        geo = _billing_geo_from_session(session)
        if not geo:
            return
        try:
            self.supabase.table("users").update(geo).eq("id", user_id).execute()
        except Exception as exc:
            try:
                self.supabase.session.rollback()
            except Exception:
                logger.debug("Session rollback after webhook write failure also failed", exc_info=True)
            logger.warning("Billing geo capture skipped for user %s: %s", user_id, exc)

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

        if self._handle_b2b_subscription_updated(subscription):
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

        # Fetch the current row BEFORE updating so we have the previous plan_type
        # (for the downgrade_applied email) and the pending-downgrade markers.
        pre_update = (
            self.supabase.table("subscriptions")
            .select("plan_type, pending_plan, stripe_schedule_id, user_id")
            .eq("stripe_subscription_id", subscription_id)
            .limit(1)
            .execute()
        )
        pre_row = (pre_update.data or [{}])[0]
        previous_plan = pre_row.get("plan_type")
        pending_plan = pre_row.get("pending_plan")

        # A pending downgrade is backed by a Stripe Subscription Schedule (created
        # with end_behavior="release"). Stripe clears subscription.schedule once
        # that schedule completes or is released, so "we had a schedule locally but
        # Stripe no longer reports one" means the rollover has happened (or the
        # schedule was cancelled out-of-band). During the pending window Stripe
        # still reports the schedule, so unrelated subscription.updated events
        # (e.g. a card update) won't be mistaken for a rollover.
        had_local_schedule = bool(pre_row.get("stripe_schedule_id")) or bool(pending_plan)
        schedule_fired = had_local_schedule and not subscription.get("schedule")

        # #7 — price→plan resolution fails if a price ID isn't configured in
        # settings. If a schedule we created has now fired, trust the pending_plan
        # we recorded rather than stranding the user on their old (higher) tier.
        if not resolved_plan and schedule_fired and pending_plan:
            resolved_plan = normalize_plan(pending_plan)
            logger.error(
                "subscription %s: could not resolve plan from price; falling back to "
                "recorded pending_plan=%s after schedule fired. Check Stripe price IDs "
                "in settings — automatic resolution is broken.",
                subscription_id,
                resolved_plan,
            )
        elif not resolved_plan and (pending_plan or pre_row.get("stripe_schedule_id")):
            logger.error(
                "subscription %s: could not resolve plan from price while a downgrade "
                "was pending (pending_plan=%s). Entitlement NOT updated — check Stripe "
                "price IDs in settings.",
                subscription_id,
                pending_plan,
            )

        if resolved_plan:
            payload["plan_type"] = resolved_plan
            payload["report_limit"] = PLAN_REPORT_LIMITS.get(resolved_plan, 3)

        # #6 / #8 — once the schedule has fired, clear the pending markers no matter
        # which plan the rollover actually landed on. Folding this into the same
        # UPDATE keeps it atomic with the plan/period write.
        if schedule_fired:
            payload["pending_plan"] = None
            payload["stripe_schedule_id"] = None

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
            # Status/period dates (and any pending-marker clear) were still
            # persisted above; we just can't mirror an unknown plan to the user.
            return

        user_id = rows[0].get("user_id")
        if not user_id:
            return

        # Notify the user only when the rollover landed on the plan they were told
        # it would (pending_plan). An out-of-band schedule change to a different
        # plan still clears the marker above, but we skip the now-inaccurate email.
        if schedule_fired and pending_plan and normalize_plan(pending_plan) == resolved_plan:
            self._send_email_to_user_id(
                user_id,
                "downgrade_applied",
                {
                    "previous_plan_name": _PLAN_LABEL.get(
                        normalize_plan(previous_plan or ""), (previous_plan or "").title()
                    ),
                    "new_plan_name": _PLAN_LABEL.get(resolved_plan, resolved_plan.title()),
                },
            )

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
        if self._handle_b2b_subscription_deleted(subscription_id):
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
        self._send_email_to_user_id(user_id, "subscription_downgraded", {})

    def _handle_b2b_checkout_completed(self, session: dict) -> None:
        try:
            from app.modules.b2b.service import B2BService

            B2BService(self.supabase, self.settings).create_or_update_subscription_from_checkout(session)
        except Exception:
            logger.exception(
                "CRITICAL: B2B checkout completed but local entitlement write failed. "
                "Session ID: %s, Customer: %s",
                session.get("id"),
                session.get("customer"),
            )
            raise

    def _handle_b2b_subscription_updated(self, subscription: dict) -> bool:
        try:
            from app.modules.b2b.service import B2BService

            return B2BService(self.supabase, self.settings).update_from_stripe_subscription(subscription)
        except Exception:
            logger.exception("B2B subscription update handling failed for %s", subscription.get("id"))
            raise

    def _handle_b2b_subscription_deleted(self, subscription_id: str) -> bool:
        try:
            from app.modules.b2b.service import B2BService

            return B2BService(self.supabase, self.settings).mark_stripe_subscription_deleted(subscription_id)
        except Exception:
            logger.exception("B2B subscription delete handling failed for %s", subscription_id)
            raise

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

    def _handle_schedule_released(self, schedule: dict) -> None:
        """Clear pending_plan and stripe_schedule_id when a schedule is released.

        Handles the case where a schedule is released externally (e.g. via the
        Stripe Dashboard) rather than through our cancel-scheduled-change API,
        which clears those fields synchronously. Without this handler the UI
        would keep showing 'Switching to X at renewal' indefinitely.
        """
        subscription_id = schedule.get("subscription")
        if not subscription_id:
            return
        self.supabase.table("subscriptions").update(
            {"pending_plan": None, "stripe_schedule_id": None}
        ).eq("stripe_subscription_id", subscription_id).execute()
        logger.info("Schedule released for subscription %s — cleared pending_plan", subscription_id)

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
            cache_key = f"user_profile:{user_id}"
            r.delete(cache_key)
            r.close()
        except Exception as exc:
            logger.warning("Cache bust failed for user %s: %s", user_id, exc)

    def _send_email_to_user_id(self, user_id: str, template_name: str, context: dict) -> None:
        if not self.email_service:
            return
        # Resolve the address synchronously — the DB session is request-scoped
        # and is closed once the webhook responds, so the lookup must happen now.
        try:
            user_result = (
                self.supabase.table("users").select("email").eq("id", user_id).limit(1).execute()
            )
            rows = user_result.data or []
            if not rows or not rows[0].get("email"):
                return
            email = rows[0]["email"]
        except Exception as exc:
            logger.warning("Unable to look up email recipient (%s): %s", template_name, exc)
            return

        self._dispatch_email(email, template_name, context)

    def _dispatch_email(self, email: str, template_name: str, context: dict) -> None:
        """Send the email, deferring the slow SMTP call off the response path
        when a BackgroundTasks instance is available. Never raises."""
        email_service = self.email_service
        if email_service is None:
            return

        def _send() -> None:
            try:
                email_service.send(email, template_name, context)
            except Exception as exc:
                logger.warning("Unable to send webhook email (%s): %s", template_name, exc)

        if self.background_tasks is not None:
            self.background_tasks.add_task(_send)
        else:
            _send()
