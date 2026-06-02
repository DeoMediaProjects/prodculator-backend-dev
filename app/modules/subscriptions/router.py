import logging

import stripe as stripe_lib
from fastapi import APIRouter, Depends, HTTPException
from app.core.database_client import DatabaseClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_current_user, get_supabase
from app.modules.auth.schemas import AuthUser
from app.models.enums import normalize_plan
from app.modules.payments.service import StripeService
from app.modules.subscriptions.schemas import (
    ActiveSubscriptionResponse,
    CancelScheduledChangeResponse,
    CanGenerateResponse,
    ChangePlanRequest,
    ChangePlanResponse,
    CurrentSubscriptionResponse,
    InvoicesResponse,
    PreviewChangeRequest,
    PreviewChangeResponse,
    SubscriptionStatusResponse,
    UsageResponse,
)
from app.modules.subscriptions.service import SubscriptionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/subscriptions", tags=["Subscriptions"])


def get_subscription_service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> SubscriptionService:
    return SubscriptionService(supabase)


def get_stripe_service(settings: Settings = Depends(get_settings)) -> StripeService:
    return StripeService(settings)


@router.get("/active", response_model=ActiveSubscriptionResponse)
async def get_active_subscription(
    user: AuthUser = Depends(get_current_user),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Get the current user's active subscription."""
    try:
        subscription = service.get_active_subscription(user.id)
        return ActiveSubscriptionResponse(subscription=subscription)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch active subscription")


@router.get("/can-generate", response_model=CanGenerateResponse)
async def can_generate_report(
    user: AuthUser = Depends(get_current_user),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Check if the current user can generate a report."""
    try:
        can_generate, reason = service.can_generate_report(user.id)
        return CanGenerateResponse(can_generate=can_generate, reason=reason)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to check generation eligibility")


@router.get("/status", response_model=SubscriptionStatusResponse)
async def subscription_status(
    user: AuthUser = Depends(get_current_user),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Return complete subscription status for the current user."""
    subscription = service.get_active_subscription(user.id)
    can_generate, reason = service.can_generate_report(user.id)
    return SubscriptionStatusResponse(
        plan=normalize_plan(user.plan),
        subscription=subscription,
        can_generate=can_generate,
        reason=reason,
    )


@router.get("/invoices", response_model=InvoicesResponse)
async def list_invoices(
    user: AuthUser = Depends(get_current_user),
    service: SubscriptionService = Depends(get_subscription_service),
    stripe_service: StripeService = Depends(get_stripe_service),
):
    """Return the user's billing invoice history (paid subscriptions only).

    Pulls from Stripe using the customer ID stored in the subscriptions table.
    Returns an empty list for users who have never subscribed.
    """
    subscription = service.get_active_subscription(user.id)
    if not subscription:
        # Fall back to a cancelled/expired subscription so users who cancelled
        # can still view their payment history.
        try:
            result = (
                service.supabase.table("subscriptions")
                .select("stripe_customer_id")
                .eq("user_id", user.id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            customer_id = rows[0].get("stripe_customer_id") if rows else None
        except Exception:
            customer_id = None
    else:
        customer_id = subscription.get("stripe_customer_id")

    if not customer_id:
        return InvoicesResponse(invoices=[], has_more=False)

    try:
        invoice_list = stripe_service.list_invoices(customer_id, limit=20)
        return InvoicesResponse(invoices=invoice_list, has_more=len(invoice_list) >= 20)
    except Exception:
        logger.exception("Failed to fetch invoices for user=%s", user.id)
        raise HTTPException(status_code=502, detail="Failed to fetch invoice history")


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    user: AuthUser = Depends(get_current_user),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Return current-period report usage for the dashboard widget.

    Exposes reports_used, reports_limit, reports_remaining, credits_remaining,
    period dates, and can_generate — everything needed to render a usage bar.
    """
    try:
        return UsageResponse(**service.get_usage(user.id, user.plan))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch usage data")


@router.get("/current", response_model=CurrentSubscriptionResponse)
async def get_current_subscription(
    user: AuthUser = Depends(get_current_user),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Return everything the frontend needs to render plan UI in one call.

    Used by Pricing.tsx (to decide CTA labels per card) and the Account tab.
    """
    return CurrentSubscriptionResponse(**service.get_current(user.id, user.plan))


@router.post("/preview-change", response_model=PreviewChangeResponse)
async def preview_change(
    body: PreviewChangeRequest,
    user: AuthUser = Depends(get_current_user),
    service: SubscriptionService = Depends(get_subscription_service),
    stripe_service: StripeService = Depends(get_stripe_service),
    settings: Settings = Depends(get_settings),
):
    """Preview proration totals for a proposed plan change."""
    try:
        result = service.preview_change(user.id, body.target_price_id, settings, stripe_service)
    except ValueError as exc:
        code = str(exc)
        if code == "no_active_subscription":
            raise HTTPException(status_code=404, detail="No active subscription")
        if code == "unknown_price_id":
            raise HTTPException(status_code=400, detail="Unknown price")
        raise HTTPException(status_code=400, detail=code)
    except stripe_lib.InvalidRequestError as exc:
        msg = str(exc)
        if "currency" in msg.lower():
            raise HTTPException(
                status_code=400,
                detail="This plan is priced in a different currency than your current subscription. Use the same currency to switch plans.",
            )
        logger.exception("preview-change Stripe rejected request for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Invalid plan change request")
    except stripe_lib.StripeError:
        logger.exception("preview-change Stripe error for user=%s", user.id)
        raise HTTPException(status_code=502, detail="Stripe preview failed")
    return PreviewChangeResponse(**result)


@router.delete("/scheduled-change", response_model=CancelScheduledChangeResponse)
async def cancel_scheduled_change(
    user: AuthUser = Depends(get_current_user),
    service: SubscriptionService = Depends(get_subscription_service),
    stripe_service: StripeService = Depends(get_stripe_service),
    settings: Settings = Depends(get_settings),
):
    """Cancel a pending scheduled downgrade and retain the current plan."""
    try:
        result = service.cancel_scheduled_change(user.id, settings, stripe_service)
    except ValueError as exc:
        code = str(exc)
        if code == "no_active_subscription":
            raise HTTPException(status_code=404, detail="No active subscription")
        if code == "no_scheduled_change":
            raise HTTPException(status_code=400, detail="No scheduled change to cancel")
        raise HTTPException(status_code=400, detail=code)
    except stripe_lib.StripeError:
        logger.exception("cancel_scheduled_change Stripe error for user=%s", user.id)
        raise HTTPException(status_code=502, detail="Failed to cancel scheduled change")
    return CancelScheduledChangeResponse(**result)


@router.post("/change", response_model=ChangePlanResponse)
async def change_plan(
    body: ChangePlanRequest,
    user: AuthUser = Depends(get_current_user),
    service: SubscriptionService = Depends(get_subscription_service),
    stripe_service: StripeService = Depends(get_stripe_service),
    settings: Settings = Depends(get_settings),
):
    """Apply an upgrade immediately or schedule a downgrade at period end."""
    try:
        result = service.change_plan(
            user.id, body.target_price_id, body.idempotency_key, settings, stripe_service
        )
    except ValueError as exc:
        code = str(exc)
        if code == "no_active_subscription":
            raise HTTPException(status_code=404, detail="No active subscription")
        if code == "unknown_price_id":
            raise HTTPException(status_code=400, detail="Unknown price")
        if code == "same_plan":
            raise HTTPException(status_code=400, detail="Already on this plan")
        raise HTTPException(status_code=400, detail=code)
    except stripe_lib.CardError as exc:
        logger.warning("change_plan card declined for user=%s: %s", user.id, exc)
        raise HTTPException(status_code=402, detail="Card was declined")
    except stripe_lib.InvalidRequestError as exc:
        msg = str(exc)
        if "currency" in msg.lower():
            raise HTTPException(
                status_code=400,
                detail="This plan is priced in a different currency than your current subscription. Use the same currency to switch plans.",
            )
        logger.exception("change_plan Stripe rejected request for user=%s", user.id)
        raise HTTPException(status_code=400, detail="Invalid plan change request")
    except stripe_lib.StripeError:
        logger.exception("change_plan Stripe error for user=%s", user.id)
        raise HTTPException(status_code=502, detail="Plan change failed")
    return ChangePlanResponse(**result)

