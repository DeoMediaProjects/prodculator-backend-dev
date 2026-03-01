from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_admin, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import AdminListResponse, AdminUpsertRequest, AdminUser
from app.modules.grants.service import GrantsService

router = APIRouter(prefix="/api/admin/grants", tags=["Admin - Grants"])


def get_grants_service(supabase: DatabaseClient = Depends(get_supabase)) -> GrantsService:
    return GrantsService(supabase)


@router.get("", response_model=AdminListResponse)
async def list_grants_admin(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        items, total = service.list_for_admin(limit=limit, offset=offset)
        return AdminListResponse(items=items, total=total, limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch grants")


@router.post("/bulk-import", response_model=dict)
async def bulk_import_grants_admin(
    file: UploadFile = File(...),
    _: AdminUser = Depends(get_current_admin),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        content = await file.read()
        csv_text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Invalid file encoding — expected UTF-8 CSV")
    try:
        return service.bulk_import(csv_text)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to import grants")


@router.post("", response_model=dict)
async def create_grant_admin(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return service.create(body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create grant")


@router.patch("/{item_id}", response_model=dict)
async def update_grant_admin(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        return service.update(item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update grant")


@router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_grant_admin(
    item_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: GrantsService = Depends(get_grants_service),
):
    try:
        service.delete(item_id)
        return SuccessResponse(message="grant deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete grant")
