import logging

import stripe as stripe_lib
from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)

from app.core.database_client import DatabaseClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_supabase, get_current_user
from app.core.schemas import SuccessResponse
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

router = APIRouter(prefix="/api/payments", tags=["Payments"])
webhook_router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


def get_stripe_service(settings: Settings = Depends(get_settings)) -> StripeService:
    return StripeService(settings)


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
    service: StripeService = Depends(get_stripe_service),
):
    """Create a subscription checkout session."""
    try:
        result = service.create_subscription_checkout(
            price_id=body.price_id,
            user_email=user.email,
            user_id=user.id,
        )
        return CheckoutResponse(**result)
    except stripe_lib.StripeError:
        logger.exception("Stripe error in create_subscription_checkout for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Payment processing failed")
    except Exception:
        logger.exception("Unexpected error in create_subscription_checkout for user=%s", user.id)
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

    handler = WebhookHandler(supabase, settings)
    handler.handle_event(event.id, event.type, event.data.object)

    return {"received": True}
