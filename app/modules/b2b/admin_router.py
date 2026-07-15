from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.modules.admin.schemas import AdminUser
from app.modules.b2b.schemas import (
    AdminB2BManualSubscriptionCreate,
    AdminB2BRequestListResponse,
    AdminB2BResendResponse,
    AdminB2BSubscriptionUpdate,
    B2BIntelligenceRequestResponse,
    B2BSubscriptionListResponse,
    B2BSubscriptionResponse,
)
from app.modules.b2b.service import B2BService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/b2b", tags=["Admin B2B"])


def get_b2b_service(
    db: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> B2BService:
    return B2BService(db, settings)


@router.get("/subscriptions", response_model=B2BSubscriptionListResponse)
async def list_subscriptions(
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    rows = (
        service.db.table("b2b_subscriptions")
        .select("*")
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    return {"items": rows}


@router.post("/subscriptions", response_model=B2BSubscriptionResponse)
async def create_manual_subscription(
    body: AdminB2BManualSubscriptionCreate,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    try:
        return service.create_manual_subscription(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/subscriptions/{subscription_id}", response_model=B2BSubscriptionResponse)
async def update_subscription(
    subscription_id: str,
    body: AdminB2BSubscriptionUpdate,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    subscription = service.update_subscription(subscription_id, body.model_dump(exclude_unset=True))
    if not subscription:
        raise HTTPException(status_code=404, detail="B2B subscription not found")
    return subscription


@router.get("/requests", response_model=AdminB2BRequestListResponse)
async def list_requests(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    items, total = service.list_requests(limit=limit, offset=offset, include_metrics=True)
    return {"items": items, "total": total}


@router.get("/requests/{request_id}", response_model=B2BIntelligenceRequestResponse)
async def get_request(
    request_id: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    request = service.get_request(request_id, include_metrics=True)
    if not request:
        raise HTTPException(status_code=404, detail="B2B request not found")
    return request


@router.post("/requests/{request_id}/resend", response_model=AdminB2BResendResponse)
async def resend_request(
    request_id: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    request = service.get_request(request_id, include_metrics=True)
    if not request:
        raise HTTPException(status_code=404, detail="B2B request not found")
    if request.get("status") != "completed" or not request.get("pdf_url"):
        raise HTTPException(status_code=409, detail="Only completed B2B requests with PDFs can be resent")

    try:
        recipients = service.deliver_request_pdf(request)
    except Exception:
        logger.exception("B2B resend failed: request_id=%s", request_id)
        raise HTTPException(status_code=500, detail="Failed to resend B2B PDF")
    return {"sent": True, "recipients": recipients}


@router.get("/requests/{request_id}/pdf")
async def download_request_pdf(
    request_id: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    service: B2BService = Depends(get_b2b_service),
):
    request = service.get_request(request_id, include_metrics=True)
    if not request:
        raise HTTPException(status_code=404, detail="B2B request not found")
    if request.get("status") != "completed" or not request.get("pdf_url"):
        raise HTTPException(status_code=404, detail="PDF not available")

    try:
        pdf_bytes = service.download_request_pdf(request)
    except Exception:
        logger.warning("Admin B2B PDF download failed: request_id=%s", request_id)
        raise HTTPException(status_code=404, detail="PDF not found")

    filename = f"B2B Intelligence - {request['product_type']} - {request['period_start']} to {request['period_end']}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ---------------------------------------------------------------------------
# Package assembly (admin data-pull layer)
# ---------------------------------------------------------------------------
from datetime import date as _date  # noqa: E402

from pydantic import BaseModel  # noqa: E402

from app.modules.b2b.package_service import PackageService  # noqa: E402


def get_package_service(
    service: B2BService = Depends(get_b2b_service),
) -> PackageService:
    return PackageService(service)


class PackagePreviewRequest(BaseModel):
    section_keys: list[str]
    period_start: _date
    period_end: _date


class BespokeGenerateRequest(BaseModel):
    subscription_id: str | None = None
    title: str
    section_keys: list[str]
    period_start: _date
    period_end: _date
    client_name: str | None = None


@router.get("/package/library")
async def package_library(
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    pkg: PackageService = Depends(get_package_service),
):
    """The full section catalogue an admin can compose from (signals + market context)."""
    return {"sections": pkg.library()}


@router.get("/package/template/{product_type}")
async def package_template(
    product_type: str,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    pkg: PackageService = Depends(get_package_service),
):
    """Ordered default section list for a standard product."""
    return {"product_type": product_type, "section_keys": pkg.product_template(product_type)}


@router.post("/package/preview")
async def package_preview(
    body: PackagePreviewRequest,
    _admin: AdminUser = Depends(RequirePermission("canManageB2B")),
    pkg: PackageService = Depends(get_package_service),
):
    """Sufficiency preview: which sections/segments WOULD render for the period,
    before anything is generated or delivered."""
    return pkg.preview(
        section_keys=body.section_keys,
        period_start=body.period_start,
        period_end=body.period_end,
    )
