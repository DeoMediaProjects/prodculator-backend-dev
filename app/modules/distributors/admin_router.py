from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import (
    AdminListResponse,
    AdminUpsertRequest,
    AdminUser,
    PendingChangeResponse,
    SyncSettingsResponse,
    SyncSettingsUpdateRequest,
    SyncStatusResponse,
)
from app.modules.distributors.service import DistributorsService

router = APIRouter(prefix="/api/admin/distributors", tags=["Admin - Distributors"])


def get_distributors_service(supabase: DatabaseClient = Depends(get_supabase)) -> DistributorsService:
    return DistributorsService(supabase)


@router.get("", response_model=AdminListResponse)
async def list_distributors_admin(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        items, total = service.list_for_admin(limit=limit, offset=offset)
        return AdminListResponse(items=items, total=total, limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch distributors")


@router.post("/sync", response_model=dict)
async def trigger_distributors_sync(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        return service.trigger_sync()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to trigger sync")


@router.get("/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        return SyncStatusResponse(**service.get_sync_status())
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch sync status")


@router.get("/pending-changes", response_model=list[PendingChangeResponse])
async def get_pending_changes(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        return service.get_pending_changes()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch pending changes")


@router.post("/pending-changes/{change_id}/approve", response_model=PendingChangeResponse)
async def approve_pending_change(
    change_id: str,
    admin: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
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
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        return service.reject_change(change_id, admin.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to reject change")


@router.get("/sync-settings", response_model=SyncSettingsResponse)
async def get_sync_settings(
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        return service.get_sync_settings()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch sync settings")


@router.patch("/sync-settings", response_model=SyncSettingsResponse)
async def update_sync_settings(
    body: SyncSettingsUpdateRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        payload = body.model_dump(exclude_none=True)
        return service.update_sync_settings(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update sync settings")


@router.post("", response_model=dict)
async def create_distributor_admin(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        return service.create(body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create distributor")


@router.patch("/{item_id}", response_model=dict)
async def update_distributor_admin(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        return service.update(item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update distributor")


@router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_distributor_admin(
    item_id: str,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: DistributorsService = Depends(get_distributors_service),
):
    try:
        service.delete(item_id)
        return SuccessResponse(message="distributor deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete distributor")
