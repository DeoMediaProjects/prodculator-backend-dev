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

    def create_subscription_checkout(
        self,
        price_id: str,
        user_email: str,
        user_id: str,
        metadata: dict | None = None,
    ) -> dict:
        """Create a Stripe Checkout session for subscription."""
        combined_metadata = {"userId": user_id, **(metadata or {})}
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            customer_email=user_email,
            success_url=f"{self.settings.FRONTEND_URL}/dashboard?subscription=success",
            cancel_url=f"{self.settings.FRONTEND_URL}/pricing?subscription=cancelled",
            metadata=combined_metadata,
            subscription_data={"metadata": combined_metadata},
        )
        return {"session_id": session.id, "url": session.url}

    def cancel_subscription(self, subscription_id: str) -> None:
        """Cancel a Stripe subscription at period end."""
        stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)

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
