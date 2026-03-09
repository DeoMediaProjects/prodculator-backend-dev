import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_admin, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminUser
from app.modules.subscribers.schemas import (
    CreditAdjustRequest,
    SubscriberListResponse,
    SubscriberMetricsResponse,
)
from app.modules.subscribers.service import SubscriberAdminService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/subscribers", tags=["Admin - Subscribers"])


def get_subscriber_service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> SubscriberAdminService:
    return SubscriberAdminService(supabase)


@router.get("/metrics", response_model=SubscriberMetricsResponse)
async def get_subscriber_metrics(
    _: AdminUser = Depends(get_current_admin),
    service: SubscriberAdminService = Depends(get_subscriber_service),
):
    try:
        return SubscriberMetricsResponse(**service.get_subscriber_metrics())
    except Exception:
        logger.exception("Failed to fetch subscriber metrics")
        raise HTTPException(status_code=500, detail="Failed to fetch subscriber metrics")


@router.get("", response_model=SubscriberListResponse)
async def list_subscribers(
    status: str | None = Query(None, pattern="^(active|past_due|canceled)$"),
    search: str | None = Query(None, min_length=1, max_length=200),
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: SubscriberAdminService = Depends(get_subscriber_service),
):
    try:
        result = service.list_subscribers(
            status=status, search=search, limit=limit, offset=offset
        )
        return SubscriberListResponse(**result)
    except Exception:
        logger.exception("Failed to fetch subscribers")
        raise HTTPException(status_code=500, detail="Failed to fetch subscribers")


@router.post("/{user_id}/block", response_model=SuccessResponse)
async def block_subscriber(
    user_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: SubscriberAdminService = Depends(get_subscriber_service),
):
    try:
        service.block_subscriber(user_id)
        return SuccessResponse(message="User blocked")
    except Exception:
        logger.exception("Failed to block subscriber %s", user_id)
        raise HTTPException(status_code=500, detail="Failed to block subscriber")


@router.post("/{user_id}/unblock", response_model=SuccessResponse)
async def unblock_subscriber(
    user_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: SubscriberAdminService = Depends(get_subscriber_service),
):
    try:
        service.unblock_subscriber(user_id)
        return SuccessResponse(message="User unblocked")
    except Exception:
        logger.exception("Failed to unblock subscriber %s", user_id)
        raise HTTPException(status_code=500, detail="Failed to unblock subscriber")


@router.post("/{user_id}/credit", response_model=dict)
async def adjust_credits(
    user_id: str,
    body: CreditAdjustRequest,
    _: AdminUser = Depends(get_current_admin),
    service: SubscriberAdminService = Depends(get_subscriber_service),
):
    try:
        return service.adjust_credits(user_id, body.adjustment)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("Failed to adjust credits for %s", user_id)
        raise HTTPException(status_code=500, detail="Failed to adjust credits")
