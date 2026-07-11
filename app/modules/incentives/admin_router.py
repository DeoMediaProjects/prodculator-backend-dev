import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.core.schemas import SuccessResponse

logger = logging.getLogger(__name__)
from app.modules.admin.schemas import (
    AdminListResponse,
    AdminUpsertRequest,
    AdminUser,
    PendingChangeResponse,
    SyncSettingsResponse,
    SyncSettingsUpdateRequest,
    SyncStatusResponse,
)
from app.modules.incentives.service import IncentivesService

router = APIRouter(prefix="/api/admin/incentives", tags=["Admin - Incentives"])


def get_incentives_service(supabase: DatabaseClient = Depends(get_supabase)) -> IncentivesService:
    return IncentivesService(supabase)


# ── List & Create ────────────────────────────────────────────────────────────


@router.get("", response_model=AdminListResponse)
async def list_incentives_admin(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        items, total = service.list_for_admin(limit=limit, offset=offset)
        return AdminListResponse(items=items, total=total, limit=limit, offset=offset)
    except Exception:
        logger.exception("Failed to fetch incentives")
        raise HTTPException(status_code=500, detail="Failed to fetch incentives")


@router.post("", response_model=dict)
async def create_incentive_admin(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return service.create(body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create incentive")


# ── Qualifying Spend Calculator ──────────────────────────────────────────────


@router.post("/calculate", response_model=dict)
async def calculate_qualifying_spend(
    body: dict,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    """Read-only calculator. The maths is ReportValidator._compute_corrected_rebate
    — the same function the report pipeline uses (single source of truth)."""
    try:
        return service.calculate_qualifying_spend(
            budget_amount=float(body.get("budgetAmount") or 0),
            budget_currency=str(body.get("budgetCurrency") or "GBP"),
            territory=str(body.get("territory") or ""),
            program=str(body.get("program") or ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Qualifying spend calculation failed")
        raise HTTPException(status_code=500, detail="Calculation failed")


# ── Sync Status ──────────────────────────────────────────────────────────────


@router.get("/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return SyncStatusResponse(**service.get_sync_status())
    except Exception:
        logger.exception("Failed to fetch sync status")
        raise HTTPException(status_code=500, detail="Failed to fetch sync status")


# ── Pending Changes ──────────────────────────────────────────────────────────


@router.get("/pending-changes", response_model=list[PendingChangeResponse])
async def get_pending_changes(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return service.get_pending_changes()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch pending changes")


@router.post("/pending-changes/{change_id}/approve", response_model=PendingChangeResponse)
async def approve_pending_change(
    change_id: str,
    admin: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return service.approve_change(change_id, admin.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to approve change")


@router.post("/pending-changes/{change_id}/reject", response_model=PendingChangeResponse)
async def reject_pending_change(
    change_id: str,
    admin: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return service.reject_change(change_id, admin.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to reject change")


# ── Sync Trigger ─────────────────────────────────────────────────────────────


@router.post("/sync", response_model=dict)
async def trigger_sync(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return service.trigger_sync()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to trigger sync")


# ── Sync Settings ────────────────────────────────────────────────────────────


@router.get("/sync-settings", response_model=SyncSettingsResponse)
async def get_sync_settings(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return service.get_sync_settings()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch sync settings")


@router.patch("/sync-settings", response_model=SyncSettingsResponse)
async def update_sync_settings(
    body: SyncSettingsUpdateRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        payload = body.model_dump(exclude_none=True)
        return service.update_sync_settings(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update sync settings")


# ── Update & Delete (after specific paths to avoid route conflicts) ──────────


@router.patch("/{item_id}", response_model=dict)
async def update_incentive_admin(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return service.update(item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update incentive")


@router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_incentive_admin(
    item_id: str,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        service.delete(item_id)
        return SuccessResponse(message="incentive deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete incentive")
