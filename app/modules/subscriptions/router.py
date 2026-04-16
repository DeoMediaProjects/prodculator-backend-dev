from fastapi import APIRouter, Depends, HTTPException
from app.core.database_client import DatabaseClient

from app.core.dependencies import get_current_user, get_supabase
from app.modules.auth.schemas import AuthUser
from app.models.enums import normalize_plan
from app.modules.subscriptions.schemas import (
    ActiveSubscriptionResponse,
    CanGenerateResponse,
    SubscriptionStatusResponse,
)
from app.modules.subscriptions.service import SubscriptionService

router = APIRouter(prefix="/api/subscriptions", tags=["Subscriptions"])


def get_subscription_service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> SubscriptionService:
    return SubscriptionService(supabase)


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

