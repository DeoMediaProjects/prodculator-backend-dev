import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminListResponse, AdminUpsertRequest, AdminUser
from app.modules.territory_profiles.service import TerritoryProfilesService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/territory-profiles", tags=["Admin - Territory Profiles"]
)


def get_service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> TerritoryProfilesService:
    return TerritoryProfilesService(supabase)


@router.get("", response_model=AdminListResponse)
async def list_territory_profiles_admin(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: TerritoryProfilesService = Depends(get_service),
):
    try:
        items, total = service.list_for_admin(limit=limit, offset=offset)
        return AdminListResponse(items=items, total=total, limit=limit, offset=offset)
    except Exception:
        logger.exception("Failed to fetch territory profiles")
        raise HTTPException(status_code=500, detail="Failed to fetch territory profiles")


@router.post("", response_model=dict)
async def create_territory_profile_admin(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: TerritoryProfilesService = Depends(get_service),
):
    try:
        return service.create(body.payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Failed to create territory profile")
        raise HTTPException(status_code=400, detail="Failed to create territory profile")


@router.patch("/{item_id}", response_model=dict)
async def update_territory_profile_admin(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: TerritoryProfilesService = Depends(get_service),
):
    try:
        return service.update(item_id, body.payload)
    except Exception:
        logger.exception("Failed to update territory profile")
        raise HTTPException(status_code=400, detail="Failed to update territory profile")


@router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_territory_profile_admin(
    item_id: str,
    _: AdminUser = Depends(RequirePermission("canEditIncentiveData")),
    service: TerritoryProfilesService = Depends(get_service),
):
    try:
        service.delete(item_id)
        return SuccessResponse(message="territory profile deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete territory profile")
