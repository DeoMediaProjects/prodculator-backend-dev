import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.audit import AuditedAPIRoute
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_admin, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminUser
from app.modules.subscribers.schemas import (
    CreditAdjustRequest,
    SubscriberItem,
    SubscriberListResponse,
    SubscriberMetricsResponse,
)
from app.modules.subscribers.service import SubscriberAdminService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/subscribers", tags=["Admin - Subscribers"], route_class=AuditedAPIRoute)


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
    except Exception:
        logger.exception("Failed to fetch subscribers")
        raise HTTPException(status_code=500, detail="Failed to fetch subscribers")

    # Rows are validated one at a time. Validating the whole list at once meant a
    # single row with an unexpected NULL failed the response model, the blanket
    # except turned that into a 500, and an admin saw no subscribers at all while
    # the database held twenty-two. A bad row is now skipped and named in the log
    # instead of hiding every good row behind it.
    items: list[SubscriberItem] = []
    unreadable = 0
    for row in result.get("items") or []:
        try:
            items.append(SubscriberItem(**row))
        except Exception as exc:
            unreadable += 1
            logger.error(
                "Subscriber row could not be rendered: user_id=%s error=%s row_keys=%s",
                (row or {}).get("id"), exc, sorted((row or {}).keys()),
            )

    if unreadable:
        logger.warning(
            "Subscriber listing dropped %s unreadable row(s) of %s",
            unreadable, len(result.get("items") or []),
        )

    try:
        return SubscriberListResponse(
            items=items,
            total=result.get("total", len(items)),
            limit=result.get("limit", limit),
            offset=result.get("offset", offset),
            counts=result["counts"],
            unreadable=unreadable,
        )
    except Exception:
        logger.exception("Failed to assemble the subscriber listing")
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
