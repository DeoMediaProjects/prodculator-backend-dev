import logging
from datetime import datetime, timezone

from app.core.database_client import DatabaseClient

from app.core.config import Settings
from app.modules.email.service import EmailService

logger = logging.getLogger(__name__)


class WebhookHandler:
    def __init__(self, supabase: DatabaseClient, settings: Settings | None = None):
        self.supabase = supabase
        self.email_service = EmailService(settings) if settings else None

    def handle_event(self, event_type: str, data_object: dict) -> None:
        """Dispatch webhook event to appropriate handler."""
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

        plan_type = session.get("metadata", {}).get("planType", "single")
        stripe_subscription_id = session.get("subscription")
        self.supabase.table("subscriptions").upsert(
            {
                "user_id": user_id,
                "stripe_customer_id": session.get("customer"),
                "stripe_subscription_id": stripe_subscription_id,
                "plan_type": plan_type,
                "status": "active",
                "current_period_start": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="stripe_subscription_id",
        ).execute()

        self._send_email_to_user_id(
            user_id,
            "payment_confirmation",
            {
                "plan_type": plan_type,
                "stripe_customer_id": session.get("customer"),
                "stripe_subscription_id": stripe_subscription_id,
            },
        )

    def _handle_payment_succeeded(self, payment_intent: dict) -> None:
        logger.info("Payment succeeded: %s", payment_intent.get("id"))

    def _handle_payment_failed(self, payment_intent: dict) -> None:
        logger.error("Payment failed: %s", payment_intent.get("id"))

    def _handle_subscription_updated(self, subscription: dict) -> None:
        subscription_id = subscription.get("id")
        if not subscription_id:
            logger.warning("subscription event missing id")
            return

        result = self.supabase.table("subscriptions").update(
            {
                "status": subscription.get("status"),
                "current_period_start": datetime.fromtimestamp(
                    subscription.get("current_period_start", 0), tz=timezone.utc
                ).isoformat(),
                "current_period_end": datetime.fromtimestamp(
                    subscription.get("current_period_end", 0), tz=timezone.utc
                ).isoformat(),
                "cancel_at_period_end": subscription.get("cancel_at_period_end", False),
            }
        ).eq("stripe_subscription_id", subscription_id).execute()

        if not result.data:
            self.supabase.table("subscriptions").upsert(
                {
                    "stripe_subscription_id": subscription_id,
                    "stripe_customer_id": subscription.get("customer"),
                    "status": subscription.get("status"),
                    "current_period_start": datetime.fromtimestamp(
                        subscription.get("current_period_start", 0), tz=timezone.utc
                    ).isoformat(),
                    "current_period_end": datetime.fromtimestamp(
                        subscription.get("current_period_end", 0), tz=timezone.utc
                    ).isoformat(),
                    "cancel_at_period_end": subscription.get("cancel_at_period_end", False),
                },
                on_conflict="stripe_subscription_id",
            ).execute()

    def _handle_subscription_deleted(self, subscription: dict) -> None:
        subscription_id = subscription.get("id")
        if not subscription_id:
            logger.warning("customer.subscription.deleted missing id")
            return
        self.supabase.table("subscriptions").update(
            {
                "status": "cancelled",
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("stripe_subscription_id", subscription_id).execute()

    def _handle_invoice_paid(self, invoice: dict) -> None:
        logger.info("Invoice paid: %s", invoice.get("id"))
        customer_id = invoice.get("customer")
        if not customer_id:
            return
        subscription_result = (
            self.supabase.table("subscriptions")
            .select("user_id")
            .eq("stripe_customer_id", customer_id)
            .limit(1)
            .execute()
        )
        rows = subscription_result.data or []
        if rows:
            self._send_email_to_user_id(
                rows[0]["user_id"],
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
        self.supabase.table("subscriptions").update({"status": "past_due"}).eq(
            "stripe_customer_id", customer_id
        ).execute()
        logger.error("Invoice payment failed: %s", invoice.get("id"))

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
