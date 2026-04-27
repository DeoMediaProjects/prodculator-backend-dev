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
    CanGenerateResponse,
    ChangePlanRequest,
    ChangePlanResponse,
    CurrentSubscriptionResponse,
    PreviewChangeRequest,
    PreviewChangeResponse,
    SubscriptionStatusResponse,
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

