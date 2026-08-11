import logging

import stripe

from app.core.config import Settings

logger = logging.getLogger(__name__)


def _first_line_description(inv: dict) -> str:
    """Pull the first line-item description as a human-readable label."""
    lines = (inv.get("lines", {}).get("data") or [])
    if lines:
        return lines[0].get("description", "")
    return ""


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
        test_billing: bool = False,
    ) -> dict:
        """Create a Stripe Checkout session for subscription.

        When ``test_billing`` is True, swaps in a token-amount test price
        cloned from ``price_id`` and tags the subscription so the
        auto-refund webhook keeps the test subscriber whole. Never set from a
        normal user path — only the admin test endpoint passes it.
        """
        combined_metadata = {"userId": user_id, **(metadata or {})}
        plan_type = (metadata or {}).get("planType", "professional")
        if test_billing:
            price_id = self.get_or_create_test_price(price_id)
            combined_metadata["testBilling"] = f"{self.settings.STRIPE_TEST_BILLING_INTERVAL_DAYS}day"
            combined_metadata["autoRefund"] = "true"
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

    def create_b2b_subscription_checkout(
        self,
        price_id: str,
        user_email: str,
        user_id: str,
        product_type: str,
        currency: str,
        delivery_frequency: str,
        extra_recipient_email: str | None = None,
        test_billing: bool = False,
    ) -> dict:
        """Create a Stripe Checkout session for an independent B2B subscription.

        See ``create_subscription_checkout`` for the ``test_billing`` contract.
        """
        if test_billing:
            price_id = self.get_or_create_test_price(price_id)
        metadata = {
            "userId": user_id,
            "subscriptionKind": "b2b",
            "productType": product_type,
            "priceId": price_id,
            "currency": currency,
            "deliveryFrequency": delivery_frequency,
        }
        if extra_recipient_email:
            metadata["extraRecipientEmail"] = extra_recipient_email
        if test_billing:
            metadata["testBilling"] = f"{self.settings.STRIPE_TEST_BILLING_INTERVAL_DAYS}day"
            metadata["autoRefund"] = "true"

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            customer_email=user_email,
            success_url=(
                f"{self.settings.FRONTEND_URL}/b2b"
                f"?b2b_subscription=success&product={product_type}"
            ),
            cancel_url=f"{self.settings.FRONTEND_URL}/b2b?b2b_subscription=cancelled",
            metadata=metadata,
            subscription_data={"metadata": metadata},
        )
        return {"session_id": session.id, "url": session.url}

    # ── Invoice-billed subscriptions ─────────────────────────────────────────
    #
    # Checkout takes a card up front and charges it silently every cycle. An
    # invoice-billed subscription instead issues a real invoice each cycle with
    # a hosted payment page and payment terms — the way agencies and studios
    # expect to be billed. Entitlements still flow through the ordinary
    # customer.subscription.created/updated webhook, so nothing downstream
    # needs to know which collection method was used.

    def get_or_create_customer(
        self, *, user_id: str, email: str, name: str | None = None,
        existing_customer_id: str | None = None,
    ) -> str:
        """Return a Stripe customer id for this user, creating one if needed.

        Checkout creates customers implicitly; invoicing cannot, so a customer
        must exist before an invoice-billed subscription can be created. An
        existing id is verified rather than trusted — a stale or deleted id
        would otherwise fail later, at invoice creation.
        """
        if existing_customer_id:
            try:
                customer = stripe.Customer.retrieve(existing_customer_id)
                if not getattr(customer, "deleted", False):
                    return existing_customer_id
                logger.warning(
                    "Stripe customer %s is deleted; creating a replacement for user=%s",
                    existing_customer_id, user_id,
                )
            except stripe.StripeError:
                logger.warning(
                    "Stripe customer %s could not be retrieved; creating a replacement "
                    "for user=%s", existing_customer_id, user_id, exc_info=True,
                )

        customer = stripe.Customer.create(
            email=email,
            name=name or None,
            metadata={"userId": user_id},
        )
        return customer.id

    def create_invoice_billed_subscription(
        self,
        *,
        customer_id: str,
        price_id: str,
        user_id: str,
        plan_type: str,
        days_until_due: int = 30,
        metadata: dict | None = None,
    ) -> dict:
        """Create a subscription Stripe bills by invoice instead of by card.

        ``collection_method='send_invoice'`` makes Stripe finalise and send an
        invoice for every cycle, including the first. The customer pays from
        the hosted invoice page; ``invoice.paid`` and ``invoice.payment_failed``
        then drive confirmation and dunning through the existing handlers.

        The subscription carries the same metadata shape as the Checkout path so
        the webhook resolves the plan identically.
        """
        combined_metadata = {
            "userId": user_id,
            "planType": plan_type,
            "billingMode": "invoice",
            **(metadata or {}),
        }
        subscription = stripe.Subscription.create(
            customer=customer_id,
            items=[{"price": price_id}],
            collection_method="send_invoice",
            days_until_due=days_until_due,
            metadata=combined_metadata,
        )
        sub = subscription.to_dict() if hasattr(subscription, "to_dict") else dict(subscription)

        # Surface the payment link for the first invoice so the caller can show
        # or email it immediately. Absent when Stripe has not finalised the
        # invoice yet — that is expected, and invoice.finalized fills it in.
        hosted_url: str | None = None
        invoice_id = sub.get("latest_invoice")
        if isinstance(invoice_id, dict):
            hosted_url = invoice_id.get("hosted_invoice_url")
            invoice_id = invoice_id.get("id")
        elif invoice_id:
            try:
                invoice = stripe.Invoice.retrieve(invoice_id)
                hosted_url = invoice.get("hosted_invoice_url")
            except stripe.StripeError:
                logger.warning(
                    "Could not retrieve first invoice %s for subscription %s",
                    invoice_id, sub.get("id"), exc_info=True,
                )

        return {
            "subscription_id": sub.get("id"),
            "customer_id": customer_id,
            "status": sub.get("status"),
            "invoice_id": invoice_id,
            "hosted_invoice_url": hosted_url,
        }

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
        sub = stripe.Subscription.retrieve(subscription_id).to_dict()
        item_id = sub["items"]["data"][0]["id"]
        current_price = sub["items"]["data"][0].get("price") or {}
        current_interval = (current_price.get("recurring") or {}).get("interval", "month")

        # The newer Stripe API nests item changes under subscription_details.
        # The legacy Invoice.upcoming() endpoint accepted flat subscription_items;
        # create_preview rejects that shape with 400.
        if hasattr(stripe.Invoice, "create_preview"):
            invoice = stripe.Invoice.create_preview(
                subscription=subscription_id,
                subscription_details={
                    "items": [{"id": item_id, "price": new_price_id}],
                },
            ).to_dict()
        else:
            invoice = stripe.Invoice.upcoming(
                subscription=subscription_id,
                subscription_items=[{"id": item_id, "price": new_price_id}],
            ).to_dict()

        # Sum proration line items (negative = credit, positive = prorated charge).
        proration_credit = 0
        immediate_total = invoice.get("amount_due", 0) or 0
        for line in (invoice.get("lines", {}).get("data") or []):
            if line.get("proration"):
                amount = line.get("amount", 0) or 0
                if amount < 0:
                    proration_credit += -amount

        # Detect billing-cycle change (monthly ↔ annual) so the modal can warn.
        try:
            target_price = stripe.Price.retrieve(new_price_id).to_dict()
            target_interval = (target_price.get("recurring") or {}).get("interval", "month")
        except Exception:
            target_interval = current_interval
        billing_cycle_changes = current_interval != target_interval

        effective_date = sub.get("current_period_end") or (
            (sub.get("items", {}).get("data") or [{}])[0].get("current_period_end")
        )

        return {
            "immediate_total": immediate_total,
            "proration_credit": proration_credit,
            "next_invoice_total": invoice.get("total", 0) or 0,
            "currency": invoice.get("currency", "usd"),
            "effective_date": effective_date,
            "billing_cycle_changes": billing_cycle_changes,
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

    def schedule_subscription_downgrade(
        self,
        subscription_id: str,
        new_price_id: str,
    ) -> str:
        """Create a Subscription Schedule to defer a downgrade to the next billing period.

        Using a schedule keeps the subscription item on the current (paid) plan
        until current_period_end. Stripe fires customer.subscription.updated at
        the natural rollover rather than immediately, so the user retains access
        to their current tier for the remainder of the period they already paid
        for. Returns the schedule_id to store for later cancellation.
        """
        sub = stripe.Subscription.retrieve(subscription_id).to_dict()
        current_price_id = sub["items"]["data"][0]["price"]["id"]

        # Create a schedule from the existing subscription first. Stripe auto-populates
        # phase 1 from the current subscription state, including start/end dates.
        schedule = stripe.SubscriptionSchedule.create(
            from_subscription=subscription_id,
        ).to_dict()

        # Read period boundaries: prefer the subscription fields, fall back to the
        # auto-populated schedule phase (handles subscriptions already on a schedule
        # or Stripe API versions that omit these from the sub object directly).
        auto_phase = (schedule.get("phases") or [{}])[0]
        current_period_start = (
            sub.get("current_period_start") or auto_phase.get("start_date")
        )
        current_period_end = (
            sub.get("current_period_end") or auto_phase.get("end_date")
        )

        if not current_period_start or not current_period_end:
            raise ValueError(
                f"Cannot determine billing period for subscription {subscription_id}"
            )

        # Redefine the schedule with two explicit phases:
        # phase 1 — current plan for the remainder of this billing period
        # phase 2 — downgraded plan starting at period end, managed normally after
        stripe.SubscriptionSchedule.modify(
            schedule["id"],
            end_behavior="release",
            phases=[
                {
                    "start_date": current_period_start,
                    "end_date": current_period_end,
                    "items": [{"price": current_price_id, "quantity": 1}],
                    "proration_behavior": "none",
                },
                {
                    "start_date": current_period_end,
                    "items": [{"price": new_price_id, "quantity": 1}],
                    "proration_behavior": "none",
                },
            ],
        )
        return schedule["id"]

    def cancel_scheduled_plan_change(self, schedule_id: str) -> None:
        """Release a subscription schedule, reverting any pending plan changes.

        Releasing (not cancelling) the schedule returns the subscription to
        normal management on the current plan — the downgrade is discarded.
        """
        stripe.SubscriptionSchedule.release(schedule_id)

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

    def list_invoices(self, customer_id: str, limit: int = 20) -> list[dict]:
        """Return the most recent paid/open invoices for a Stripe customer.

        Only `paid` and `open` invoices are returned — draft and void invoices
        are filtered out because they don't represent a real charge to the user.
        Each dict contains the fields needed to render an invoice row:
        id, number, status, amount_paid, currency, created, period_start,
        period_end, hosted_invoice_url, invoice_pdf.
        """
        invoices = stripe.Invoice.list(customer=customer_id, limit=limit)
        results: list[dict] = []
        for inv in invoices.auto_paging_iter():
            inv = inv.to_dict()
            if inv.get("status") not in ("paid", "open"):
                continue
            results.append(
                {
                    "id": inv.get("id"),
                    "number": inv.get("number"),
                    "status": inv.get("status"),
                    "amount_paid": inv.get("amount_paid", 0),
                    "amount_due": inv.get("amount_due", 0),
                    "currency": inv.get("currency", "usd"),
                    "created": inv.get("created"),
                    "period_start": (inv.get("lines", {}).get("data") or [{}])[0].get("period", {}).get("start"),
                    "period_end": (inv.get("lines", {}).get("data") or [{}])[0].get("period", {}).get("end"),
                    "hosted_invoice_url": inv.get("hosted_invoice_url"),
                    "invoice_pdf": inv.get("invoice_pdf"),
                    "description": inv.get("description") or _first_line_description(inv),
                }
            )
            if len(results) >= limit:
                break
        return results

    # ── Compressed-cycle billing test helpers ────────────────────────────────
    def get_or_create_test_price(self, real_price_id: str) -> str:
        """Find-or-create a token-amount recurring price for billing tests.

        Cloned from the real price's product + currency, but with a token unit
        amount so the real price is never touched and the refunded charge is
        tiny. The interval matches the real monthly cadence (default 30 days) so
        a tester sees the period length and renewal date they would in
        production. Idempotent via a deterministic lookup_key that includes the
        interval, so repeated tests reuse the same price and changing the
        interval mints a new one rather than mutating a price Stripe has already
        billed against.
        """
        interval_days = self.settings.STRIPE_TEST_BILLING_INTERVAL_DAYS
        unit_amount = self.settings.STRIPE_TEST_BILLING_UNIT_AMOUNT
        lookup_key = f"test_{interval_days}d_{unit_amount}_{real_price_id}"

        existing = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1)
        if existing.data:
            return existing.data[0].id

        real = stripe.Price.retrieve(real_price_id).to_dict()
        if not real.get("recurring"):
            raise ValueError(f"Price {real_price_id} is not a recurring subscription price")

        created = stripe.Price.create(
            product=real["product"],
            currency=real["currency"],
            unit_amount=unit_amount,
            recurring={"interval": "day", "interval_count": interval_days},
            lookup_key=lookup_key,
            nickname=f"[TEST {interval_days}d] cloned from {real_price_id}",
            metadata={
                "test_billing": "true",
                "source_price_id": real_price_id,
                "interval_days": str(interval_days),
            },
        )
        logger.info(
            "Created %s-day test price %s (from %s, %s %s)",
            interval_days, created.id, real_price_id, unit_amount, real["currency"],
        )
        return created.id

    def auto_refund_test_invoice(self, invoice: dict) -> str | None:
        """Fully refund a paid invoice IFF its subscription is flagged as a test.

        Safe by construction: the refund fires only when the subscription's
        Stripe metadata carries autoRefund="true" (set exclusively by the admin
        test-checkout path), read straight from Stripe. A real customer's
        subscription never carries that flag, so this returns None for them and
        no refund is ever issued. Returns the refund id, or None if not
        applicable / already refunded.
        """
        subscription_id = invoice.get("subscription")
        if not subscription_id:
            return None

        subscription = stripe.Subscription.retrieve(subscription_id).to_dict()
        if (subscription.get("metadata") or {}).get("autoRefund") != "true":
            return None  # Not a test subscription — never refund.

        payment_intent = invoice.get("payment_intent")
        if not payment_intent:
            fresh = stripe.Invoice.retrieve(invoice.get("id")).to_dict()
            payment_intent = fresh.get("payment_intent")
        if not payment_intent:
            logger.warning(
                "Test-billing auto-refund: no payment_intent on invoice %s", invoice.get("id")
            )
            return None

        try:
            refund = stripe.Refund.create(
                payment_intent=payment_intent,
                metadata={
                    "auto_refund": "test_billing",
                    "invoice_id": invoice.get("id") or "",
                    "subscription_id": subscription_id,
                },
            )
            return refund.id
        except stripe.error.InvalidRequestError as exc:
            # Already refunded (e.g. a Stripe retry of the same event) — benign.
            logger.info(
                "Test-billing auto-refund skipped for invoice %s: %s", invoice.get("id"), exc
            )
            return None

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> stripe.Event:
        """Verify and construct a Stripe webhook event."""
        return stripe.Webhook.construct_event(
            payload, sig_header, self.settings.STRIPE_WEBHOOK_SECRET
        )
