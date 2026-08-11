import logging

import stripe as stripe_lib
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from app.core.database_client import DatabaseClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_supabase, get_current_user
from app.core.permissions import RequirePermission
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminUser
from app.modules.auth.schemas import AuthUser
from app.modules.payments.schemas import (
    CheckoutRequest,
    SubscriptionCheckoutRequest,
    CancelSubscriptionRequest,
    UpdatePaymentMethodRequest,
    CustomerPortalRequest,
    CheckoutResponse,
    CustomerPortalResponse,
)
from app.modules.payments.service import StripeService
from app.modules.payments.webhook_handler import WebhookHandler
from app.modules.subscriptions.service import SubscriptionService

router = APIRouter(prefix="/api/payments", tags=["Payments"])
webhook_router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


def get_stripe_service(settings: Settings = Depends(get_settings)) -> StripeService:
    return StripeService(settings)


def _resolve_base_subscription_price(
    settings: Settings, plan_type: str, currency: str, billing_cycle: str
) -> str:
    """Map a plan/currency/cycle to its configured live Stripe price id."""
    plan = plan_type.strip().upper()
    cyc = "_ANNUAL" if billing_cycle == "annual" else ""
    cur = currency.strip().upper()
    return getattr(settings, f"STRIPE_PRICE_{plan}{cyc}_{cur}", "") or ""


class TestSubscriptionCheckoutRequest(BaseModel):
    """Admin-only: mint a compressed-cycle test checkout for a target user."""
    user_email: str
    plan_type: str  # professional | producer | studio
    currency: str = "gbp"  # gbp | usd
    billing_cycle: str = "monthly"  # monthly | annual


@router.post("/test/subscription-checkout", response_model=CheckoutResponse)
async def create_test_subscription_checkout(
    body: TestSubscriptionCheckoutRequest,
    _admin: AdminUser = Depends(RequirePermission("canManageAdmins")),
    settings: Settings = Depends(get_settings),
    supabase: DatabaseClient = Depends(get_supabase),
    service: StripeService = Depends(get_stripe_service),
):
    """Mint a Checkout URL that bills a target user a token amount on the normal
    monthly cycle and auto-refunds every charge, to validate recurring billing
    without charging a real plan price. LIVE money, master-admin only, and
    inert unless STRIPE_TEST_BILLING_ENABLED is on."""
    if not settings.STRIPE_TEST_BILLING_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    email = body.user_email.strip().lower()
    user_rows = (
        supabase.table("users").select("id,email").eq("email", email).limit(1).execute().data or []
    )
    if not user_rows:
        raise HTTPException(status_code=404, detail=f"No user account found for {email}")
    target = user_rows[0]

    price_id = _resolve_base_subscription_price(
        settings, body.plan_type, body.currency, body.billing_cycle
    )
    if not price_id:
        raise HTTPException(
            status_code=400,
            detail=f"No Stripe price configured for {body.plan_type}/{body.currency}/{body.billing_cycle}",
        )

    try:
        result = service.create_subscription_checkout(
            price_id=price_id,
            user_email=target["email"],
            user_id=target["id"],
            metadata={"planType": body.plan_type},
            test_billing=True,
        )
        logger.info(
            "Test-billing checkout minted for user=%s plan=%s by admin=%s",
            target["id"], body.plan_type, _admin.id,
        )
        return CheckoutResponse(**result)
    except stripe_lib.StripeError:
        logger.exception("Stripe error minting test checkout for %s", email)
        raise HTTPException(status_code=400, detail="Payment processing failed")
    except Exception:
        logger.exception("Unexpected error minting test checkout for %s", email)
        raise HTTPException(status_code=500, detail="Internal server error")


class InvoiceSubscriptionRequest(BaseModel):
    """Admin-only: bill a user's subscription by invoice instead of by card."""
    user_email: str
    plan_type: str                 # professional | producer | studio
    currency: str = "gbp"          # gbp | usd
    billing_cycle: str = "monthly"  # monthly | annual
    days_until_due: int = 30


class InvoiceSubscriptionResponse(BaseModel):
    subscription_id: str
    customer_id: str
    status: str | None = None
    invoice_id: str | None = None
    hosted_invoice_url: str | None = None


@router.post("/invoice-subscription", response_model=InvoiceSubscriptionResponse)
async def create_invoice_billed_subscription(
    body: InvoiceSubscriptionRequest,
    _admin: AdminUser = Depends(RequirePermission("canManageAdmins")),
    settings: Settings = Depends(get_settings),
    supabase: DatabaseClient = Depends(get_supabase),
    service: StripeService = Depends(get_stripe_service),
):
    """Start an invoice-billed subscription for a user (contract/net-terms billing).

    Stripe issues a real invoice with a hosted payment page for every cycle
    instead of charging a card. Entitlements are applied by the ordinary
    customer.subscription.created webhook — this endpoint deliberately writes
    no plan itself, so there is exactly one path that grants access.
    """
    email = body.user_email.strip().lower()
    user_rows = (
        supabase.table("users").select("id,email,name").eq("email", email).limit(1)
        .execute().data or []
    )
    if not user_rows:
        raise HTTPException(status_code=404, detail=f"No user account found for {email}")
    target = user_rows[0]

    # Refuse if the user already has a live subscription — creating a second one
    # would bill them twice, the same guard the self-serve checkout applies.
    existing = SubscriptionService(supabase).get_active_subscription(target["id"])
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{email} already has an active subscription "
                f"({existing.get('plan_type')}). Cancel or change it instead."
            ),
        )

    if body.days_until_due < 0 or body.days_until_due > 365:
        raise HTTPException(status_code=400, detail="days_until_due must be between 0 and 365")

    price_id = _resolve_base_subscription_price(
        settings, body.plan_type, body.currency, body.billing_cycle
    )
    if not price_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No Stripe price configured for {body.plan_type}/{body.currency}/"
                f"{body.billing_cycle}. Set STRIPE_PRICE_{body.plan_type.strip().upper()}"
                f"{'_ANNUAL' if body.billing_cycle == 'annual' else ''}_"
                f"{body.currency.strip().upper()} on the server."
            ),
        )

    # Reuse the customer already linked to this user where one exists, so their
    # invoice history and payment methods stay on a single Stripe customer.
    prior_rows = (
        supabase.table("subscriptions").select("stripe_customer_id")
        .eq("user_id", target["id"]).limit(1).execute().data or []
    )
    existing_customer_id = prior_rows[0].get("stripe_customer_id") if prior_rows else None

    try:
        customer_id = service.get_or_create_customer(
            user_id=target["id"],
            email=target["email"],
            name=target.get("name"),
            existing_customer_id=existing_customer_id,
        )
        result = service.create_invoice_billed_subscription(
            customer_id=customer_id,
            price_id=price_id,
            user_id=target["id"],
            plan_type=body.plan_type,
            days_until_due=body.days_until_due,
        )
        logger.info(
            "Invoice-billed subscription created for user=%s plan=%s subscription=%s by admin=%s",
            target["id"], body.plan_type, result.get("subscription_id"), _admin.id,
        )
        return InvoiceSubscriptionResponse(**result)
    except stripe_lib.StripeError as exc:
        logger.exception("Stripe error creating invoice subscription for %s", email)
        raise HTTPException(status_code=400, detail=f"Stripe rejected the request: {exc}")
    except Exception:
        logger.exception("Unexpected error creating invoice subscription for %s", email)
        raise HTTPException(status_code=500, detail="Internal server error")


def ensure_user_owns_subscription(
    supabase: DatabaseClient,
    user_id: str,
    *,
    subscription_id: str | None = None,
    customer_id: str | None = None,
) -> bool:
    query = supabase.table("subscriptions").select("id").eq("user_id", user_id)
    if subscription_id:
        query = query.eq("stripe_subscription_id", subscription_id)
    if customer_id:
        query = query.eq("stripe_customer_id", customer_id)
    result = query.limit(1).execute()
    return bool(result.data)


@router.get("/promotion")
async def active_promotion(settings: Settings = Depends(get_settings)) -> dict:
    """The promotion the checkout will actually apply, or none.

    Public and unauthenticated: the pricing page renders before sign-in. The site
    advertises a discount ONLY from this response, never from a percentage written
    into the frontend, because the charged amount comes from Stripe. A hardcoded
    "45% off" would keep advertising a discount after the coupon expired, showing one
    price and billing another — which is the failure mode this endpoint exists to
    make impossible.
    """
    coupon = (settings.STRIPE_PROMO_COUPON_ID or "").strip()
    percent = int(settings.STRIPE_PROMO_PERCENT_OFF or 0)
    if not coupon or percent <= 0 or percent >= 100:
        return {"active": False, "percentOff": 0, "label": "", "plans": []}
    # The plans the coupon is scoped to in Stripe. The pricing surfaces discount
    # exactly these and no others, so a plan outside the coupon never shows a saving
    # it would not receive at checkout.
    plans = sorted({p.strip().lower() for p in (settings.STRIPE_PROMO_PLANS or "").split(",") if p.strip()})
    return {
        "active": True,
        "percentOff": percent,
        "label": settings.STRIPE_PROMO_LABEL or f"{percent}% off all subscription plans",
        "plans": plans,
    }


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    user: AuthUser = Depends(get_current_user),
    service: StripeService = Depends(get_stripe_service),
):
    """Create a one-time payment checkout session."""
    try:
        result = service.create_checkout_session(
            price_id=body.price_id,
            user_email=user.email,
            user_id=user.id,
        )
        return CheckoutResponse(**result)
    except stripe_lib.StripeError:
        logger.exception("Stripe error in create_checkout for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Payment processing failed")
    except Exception:
        logger.exception("Unexpected error in create_checkout for user=%s", user.id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/subscription-checkout", response_model=CheckoutResponse)
async def create_subscription_checkout(
    body: SubscriptionCheckoutRequest,
    user: AuthUser = Depends(get_current_user),
    supabase: DatabaseClient = Depends(get_supabase),
    service: StripeService = Depends(get_stripe_service),
    settings: Settings = Depends(get_settings),
):
    """Create a subscription checkout session.

    Existing subscribers must go through /api/subscriptions/change instead —
    Checkout would create a second Stripe subscription, double-billing the user.
    """
    existing = SubscriptionService(supabase).get_active_subscription(user.id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "existing_subscription",
                "subscription_id": existing.get("stripe_subscription_id"),
                "redirect": "/account?action=change-plan",
            },
        )

    # Resolve the Stripe price server-side. The frontend bakes VITE_STRIPE_PRICE_*
    # at build time, so a build without those env vars sends an empty price_id and
    # Stripe rejects the request. The backend is the source of truth for prices:
    # honour a non-empty client price_id (keeps local dev working), otherwise
    # resolve from plan/currency/cycle out of the server's own STRIPE_PRICE_* config.
    price_id = (body.price_id or "").strip()
    if not price_id:
        price_id = _resolve_base_subscription_price(
            settings, body.plan_type, body.currency, body.billing_cycle
        )
    if not price_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No Stripe price configured for "
                f"{body.plan_type}/{body.currency}/{body.billing_cycle}. "
                f"Set STRIPE_PRICE_{body.plan_type.strip().upper()}"
                f"{'_ANNUAL' if body.billing_cycle == 'annual' else ''}_"
                f"{body.currency.strip().upper()} on the server."
            ),
        )

    try:
        # When token-amount billing test mode is ON (a deliberate ops flag,
        # OFF in normal operation), the public checkout mints a $1 subscription
        # on the normal monthly cycle tagged for auto-refund, so a demo
        # subscriber sees a real subscription and is kept whole. OFF by
        # default → charges the real plan price with no refund, exactly as
        # before.
        result = service.create_subscription_checkout(
            price_id=price_id,
            user_email=user.email,
            user_id=user.id,
            metadata={"planType": body.plan_type},
            test_billing=settings.STRIPE_TEST_BILLING_ENABLED,
        )
        return CheckoutResponse(**result)
    except stripe_lib.StripeError:
        logger.exception("Stripe error in create_subscription_checkout for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Payment processing failed")
    except Exception:
        logger.exception("Unexpected error in create_subscription_checkout for user=%s", user.id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/credit-checkout", response_model=CheckoutResponse)
async def create_credit_checkout(
    body: CheckoutRequest,
    user: AuthUser = Depends(get_current_user),
    service: StripeService = Depends(get_stripe_service),
):
    """Create a one-time checkout session for a pay-per-report credit."""
    try:
        result = service.create_credit_checkout_session(
            price_id=body.price_id,
            user_email=user.email,
            user_id=user.id,
        )
        return CheckoutResponse(**result)
    except stripe_lib.StripeError:
        logger.exception("Stripe error in create_credit_checkout for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Payment processing failed")
    except Exception:
        logger.exception("Unexpected error in create_credit_checkout for user=%s", user.id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/cancel-subscription", response_model=SuccessResponse)
async def cancel_subscription(
    body: CancelSubscriptionRequest,
    user: AuthUser = Depends(get_current_user),
    supabase: DatabaseClient = Depends(get_supabase),
    service: StripeService = Depends(get_stripe_service),
):
    """Cancel a subscription at period end."""
    owns_subscription = ensure_user_owns_subscription(
        supabase,
        user.id,
        subscription_id=body.subscription_id,
    )
    if not owns_subscription:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        service.cancel_subscription(body.subscription_id)
        return SuccessResponse(message="Subscription will be cancelled at end of billing period")
    except stripe_lib.StripeError:
        logger.exception("Stripe error cancelling subscription for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Failed to cancel subscription")
    except Exception:
        logger.exception("Unexpected error cancelling subscription for user=%s", user.id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/customer-portal", response_model=CustomerPortalResponse)
async def customer_portal(
    body: CustomerPortalRequest,
    user: AuthUser = Depends(get_current_user),
    supabase: DatabaseClient = Depends(get_supabase),
    service: StripeService = Depends(get_stripe_service),
):
    """Get Stripe Customer Portal URL."""
    owns_customer = ensure_user_owns_subscription(supabase, user.id, customer_id=body.customer_id)
    if not owns_customer:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        url = service.create_customer_portal_session(body.customer_id)
        return CustomerPortalResponse(url=url)
    except stripe_lib.StripeError:
        logger.exception("Stripe error creating customer portal for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Failed to open customer portal")
    except Exception:
        logger.exception("Unexpected error creating customer portal for user=%s", user.id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/update-payment-method", response_model=SuccessResponse)
async def update_payment_method(
    body: UpdatePaymentMethodRequest,
    user: AuthUser = Depends(get_current_user),
    supabase: DatabaseClient = Depends(get_supabase),
    service: StripeService = Depends(get_stripe_service),
):
    """Update a customer's default payment method."""
    owns_customer = ensure_user_owns_subscription(supabase, user.id, customer_id=body.customer_id)
    if not owns_customer:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        service.update_payment_method(body.customer_id, body.payment_method_id)
        return SuccessResponse(message="Payment method updated successfully")
    except stripe_lib.StripeError:
        logger.exception("Stripe error updating payment method for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Failed to update payment method")
    except Exception:
        logger.exception("Unexpected error updating payment method for user=%s", user.id)
        raise HTTPException(status_code=500, detail="Internal server error")


# --- Stripe Webhook (no auth, signature verified) ---


@webhook_router.post("/stripe")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    supabase: DatabaseClient = Depends(get_supabase),
):
    """Handle Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    service = StripeService(settings)
    try:
        event = service.construct_webhook_event(payload, sig_header)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    handler = WebhookHandler(supabase, settings, background_tasks)
    # Stripe's SDK objects (Subscription, Invoice, Session, ...) are NOT dicts —
    # they only support attribute/bracket access, not .get(). Every downstream
    # handler is written against plain dicts (and is tested against them), so
    # convert once here rather than at each of the dozens of .get() call sites
    # in webhook_handler.py / b2b/service.py.
    handler.handle_event(event.id, event.type, event.data.object.to_dict())

    return {"received": True}
