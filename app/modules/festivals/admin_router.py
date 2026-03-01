from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_admin, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminListResponse, AdminUpsertRequest, AdminUser
from app.modules.festivals.service import FestivalsService

router = APIRouter(prefix="/api/admin/festivals", tags=["Admin - Festivals"])


def get_festivals_service(supabase: DatabaseClient = Depends(get_supabase)) -> FestivalsService:
    return FestivalsService(supabase)


@router.get("", response_model=AdminListResponse)
async def list_festivals_admin(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: FestivalsService = Depends(get_festivals_service),
):
    try:
        items, total = service.list_for_admin(limit=limit, offset=offset)
        return AdminListResponse(items=items, total=total, limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch festivals")


@router.post("", response_model=dict)
async def create_festival_admin(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: FestivalsService = Depends(get_festivals_service),
):
    try:
        return service.create(body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create festival")


@router.patch("/{item_id}", response_model=dict)
async def update_festival_admin(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: FestivalsService = Depends(get_festivals_service),
):
    try:
        return service.update(item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update festival")


@router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_festival_admin(
    item_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: FestivalsService = Depends(get_festivals_service),
):
    try:
        service.delete(item_id)
        return SuccessResponse(message="festival deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete festival")
