import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.modules.admin.schemas import AdminUser
from app.modules.email_gating.schemas import (
    EmailGatingListResponse,
    EmailGatingResponse,
)
from app.modules.email_gating.service import EmailGatingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/email-gating", tags=["Admin - Email Gating"])


def get_email_gating_service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> EmailGatingService:
    return EmailGatingService(supabase)


def _to_response(row: dict) -> EmailGatingResponse:
    return EmailGatingResponse(
        id=row["id"],
        email=row["email"],
        date=row["created_at"],
        report_generated=row["report_generated"],
        blocked=row["blocked"],
    )


@router.get("", response_model=EmailGatingListResponse)
async def list_email_gating_records(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str = Query(""),
    _: AdminUser = Depends(RequirePermission("canManageEmailGating")),
    service: EmailGatingService = Depends(get_email_gating_service),
):
    try:
        rows, total = service.list_records(limit=limit, offset=offset, search=search)
        return EmailGatingListResponse(
            items=[_to_response(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
    except Exception:
        logger.exception("Failed to fetch email gating records")
        raise HTTPException(status_code=500, detail="Failed to fetch email gating records")


@router.post("/{record_id}/block", response_model=EmailGatingResponse)
async def block_email(
    record_id: str,
    _: AdminUser = Depends(RequirePermission("canManageEmailGating")),
    service: EmailGatingService = Depends(get_email_gating_service),
):
    try:
        row = service.block_record(record_id)
        if not row:
            raise HTTPException(status_code=404, detail="Record not found")
        return _to_response(row)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to block email gating record %s", record_id)
        raise HTTPException(status_code=500, detail="Failed to block record")


@router.post("/{record_id}/unblock", response_model=EmailGatingResponse)
async def unblock_email(
    record_id: str,
    _: AdminUser = Depends(RequirePermission("canManageEmailGating")),
    service: EmailGatingService = Depends(get_email_gating_service),
):
    try:
        row = service.unblock_record(record_id)
        if not row:
            raise HTTPException(status_code=404, detail="Record not found")
        return _to_response(row)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to unblock email gating record %s", record_id)
        raise HTTPException(status_code=500, detail="Failed to unblock record")
