import logging

import stripe

from app.core.config import Settings

logger = logging.getLogger(__name__)


class StripeService:
    def __init__(self, settings: Settings):
        self.settings = settings
        stripe.api_key = settings.STRIPE_SECRET_KEY

    def create_checkout_session(
        self, price_id: str, user_email: str, user_id: str, metadata: dict | None = None
    ) -> dict:
        """Create a Stripe Checkout session for one-time payment."""
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="payment",
            customer_email=user_email,
            success_url=f"{self.settings.FRONTEND_URL}/dashboard?payment=success",
            cancel_url=f"{self.settings.FRONTEND_URL}/pricing?payment=cancelled",
            metadata={"userId": user_id, **(metadata or {})},
        )
        return {"session_id": session.id, "url": session.url}

    def create_credit_checkout_session(
        self, price_id: str, user_email: str, user_id: str
    ) -> dict:
        """Create a one-time Stripe Checkout session for a pay-per-report credit."""
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="payment",
            customer_email=user_email,
            success_url=f"{self.settings.FRONTEND_URL}/dashboard?credit=success",
            cancel_url=f"{self.settings.FRONTEND_URL}/pay-per-report?payment=cancelled",
            metadata={"userId": user_id, "paymentType": "credit"},
        )
        return {"session_id": session.id, "url": session.url}

    def create_subscription_checkout(
        self,
        price_id: str,
        user_email: str,
        user_id: str,
        metadata: dict | None = None,
    ) -> dict:
        """Create a Stripe Checkout session for subscription."""
        combined_metadata = {"userId": user_id, **(metadata or {})}
        plan_type = (metadata or {}).get("planType", "professional")
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            customer_email=user_email,
            success_url=(
                f"{self.settings.FRONTEND_URL}/dashboard"
                f"?subscription=success&plan={plan_type}"
            ),
            cancel_url=f"{self.settings.FRONTEND_URL}/pricing?subscription=cancelled",
            metadata=combined_metadata,
            subscription_data={"metadata": combined_metadata},
        )
        return {"session_id": session.id, "url": session.url}

    def cancel_subscription(self, subscription_id: str) -> None:
        """Cancel a Stripe subscription at period end."""
        stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)

    def preview_subscription_change(
        self, subscription_id: str, new_price_id: str
    ) -> dict:
        """Return upcoming-invoice numbers for a proposed plan change.

        Read-only — no Stripe state mutation. Used by the change-plan modal to
        show the user what they'll be charged before they confirm.
        """
        sub = stripe.Subscription.retrieve(subscription_id)
        item_id = sub["items"]["data"][0]["id"]
        # The newer Stripe API nests item changes under subscription_details.
        # The legacy Invoice.upcoming() endpoint accepted flat subscription_items;
        # create_preview rejects that shape with 400.
        if hasattr(stripe.Invoice, "create_preview"):
            invoice = stripe.Invoice.create_preview(
                subscription=subscription_id,
                subscription_details={
                    "items": [{"id": item_id, "price": new_price_id}],
                },
            )
        else:
            invoice = stripe.Invoice.upcoming(
                subscription=subscription_id,
                subscription_items=[{"id": item_id, "price": new_price_id}],
            )

        # Sum proration line items (negative = credit, positive = prorated charge).
        proration_credit = 0
        immediate_total = invoice.get("amount_due", 0) or 0
        for line in (invoice.get("lines", {}).get("data") or []):
            if line.get("proration"):
                amount = line.get("amount", 0) or 0
                if amount < 0:
                    proration_credit += -amount

        return {
            "immediate_total": immediate_total,
            "proration_credit": proration_credit,
            "next_invoice_total": invoice.get("total", 0) or 0,
            "currency": invoice.get("currency", "usd"),
            "period_end": sub.get("current_period_end")
            or (sub.get("items", {}).get("data") or [{}])[0].get("current_period_end"),
        }

    def change_subscription_plan(
        self,
        subscription_id: str,
        new_price_id: str,
        *,
        prorate: bool,
        idempotency_key: str | None = None,
    ) -> dict:
        """Swap the subscription's price.

        prorate=True  → upgrades: immediate proration, charges the difference now.
        prorate=False → downgrades: new price applies, no refund/credit; the user
                        keeps the old tier through the end of the paid period.
        """
        sub = stripe.Subscription.retrieve(subscription_id)
        item_id = sub["items"]["data"][0]["id"]
        proration_behavior = "always_invoice" if prorate else "none"

        kwargs: dict = {
            "items": [{"id": item_id, "price": new_price_id}],
            "proration_behavior": proration_behavior,
        }
        if prorate:
            # If the prorated charge fails (e.g. card declined), reject the change
            # rather than leaving the subscription in `incomplete`.
            kwargs["payment_behavior"] = "error_if_incomplete"

        if idempotency_key:
            updated = stripe.Subscription.modify(
                subscription_id, **kwargs, idempotency_key=idempotency_key
            )
        else:
            updated = stripe.Subscription.modify(subscription_id, **kwargs)
        return updated

    def update_payment_method(self, customer_id: str, payment_method_id: str) -> None:
        """Update default payment method for a customer."""
        stripe.PaymentMethod.attach(payment_method_id, customer=customer_id)
        stripe.Customer.modify(
            customer_id,
            invoice_settings={"default_payment_method": payment_method_id},
        )

    def create_customer_portal_session(self, customer_id: str) -> str:
        """Create a Stripe Customer Portal session URL."""
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{self.settings.FRONTEND_URL}/dashboard",
        )
        return session.url

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> stripe.Event:
        """Verify and construct a Stripe webhook event."""
        return stripe.Webhook.construct_event(
            payload, sig_header, self.settings.STRIPE_WEBHOOK_SECRET
        )
