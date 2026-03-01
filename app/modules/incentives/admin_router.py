from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_admin, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminListResponse, AdminUpsertRequest, AdminUser
from app.modules.incentives.service import IncentivesService

router = APIRouter(prefix="/api/admin/incentives", tags=["Admin - Incentives"])


def get_incentives_service(supabase: DatabaseClient = Depends(get_supabase)) -> IncentivesService:
    return IncentivesService(supabase)


@router.get("", response_model=AdminListResponse)
async def list_incentives_admin(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        items, total = service.list_for_admin(limit=limit, offset=offset)
        return AdminListResponse(items=items, total=total, limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch incentives")


@router.post("", response_model=dict)
async def create_incentive_admin(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return service.create(body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create incentive")


@router.patch("/{item_id}", response_model=dict)
async def update_incentive_admin(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        return service.update(item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update incentive")


@router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_incentive_admin(
    item_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: IncentivesService = Depends(get_incentives_service),
):
    try:
        service.delete(item_id)
        return SuccessResponse(message="incentive deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete incentive")
