from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.database_client import DatabaseClient

from app.core.dependencies import get_current_admin, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.admin.schemas import (
    AdminListResponse,
    AdminUpsertRequest,
    AdminUser,
    BusinessMetricsResponse,
    ProductionSignalsResponse,
)
from app.modules.admin.service import AdminService

router = APIRouter(prefix="/api/admin", tags=["Admin"])

def get_admin_service(supabase: DatabaseClient = Depends(get_supabase)) -> AdminService:
    return AdminService(supabase)


def _list_resource(
    service: AdminService,
    *,
    table_name: str,
    limit: int,
    offset: int,
) -> AdminListResponse:
    items, total = service.list_table(table_name, limit=limit, offset=offset)
    return AdminListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/users", response_model=AdminListResponse)
async def list_users(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="users", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch users")


@router.get("/reports", response_model=AdminListResponse)
async def list_reports(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="reports", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch reports")


@router.get("/metrics", response_model=BusinessMetricsResponse)
async def get_metrics(
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return BusinessMetricsResponse(**service.get_business_metrics())
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch business metrics")


@router.get("/production-signals", response_model=ProductionSignalsResponse)
async def get_production_signals(
    territory: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        items, total = service.get_production_signals(
            territory=territory,
            start_date=start_date,
            end_date=end_date,
        )
        return ProductionSignalsResponse(items=items, total=total)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch production signals")


@router.get("/incentives", response_model=AdminListResponse)
async def list_incentives(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="incentive_programs", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch incentives")


@router.post("/incentives", response_model=dict)
async def create_incentive(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.create_row("incentive_programs", body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create incentives")


@router.patch("/incentives/{item_id}", response_model=dict)
async def update_incentive(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.update_row("incentive_programs", item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update incentives")


@router.delete("/incentives/{item_id}", response_model=SuccessResponse)
async def delete_incentive(
    item_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        service.delete_row("incentive_programs", item_id)
        return SuccessResponse(message="incentive item deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete incentives")


@router.get("/crew-costs", response_model=AdminListResponse)
async def list_crew_costs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="crew_costs", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch crew costs")


@router.post("/crew-costs", response_model=dict)
async def create_crew_cost(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.create_row("crew_costs", body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create crew costs")


@router.patch("/crew-costs/{item_id}", response_model=dict)
async def update_crew_cost(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.update_row("crew_costs", item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update crew costs")


@router.delete("/crew-costs/{item_id}", response_model=SuccessResponse)
async def delete_crew_cost(
    item_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        service.delete_row("crew_costs", item_id)
        return SuccessResponse(message="crew cost item deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete crew costs")


@router.get("/comparables", response_model=AdminListResponse)
async def list_comparables(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="comparable_productions", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch comparables")


@router.post("/comparables", response_model=dict)
async def create_comparable(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.create_row("comparable_productions", body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create comparables")


@router.patch("/comparables/{item_id}", response_model=dict)
async def update_comparable(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.update_row("comparable_productions", item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update comparables")


@router.delete("/comparables/{item_id}", response_model=SuccessResponse)
async def delete_comparable(
    item_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        service.delete_row("comparable_productions", item_id)
        return SuccessResponse(message="comparable item deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete comparables")


@router.get("/grants", response_model=AdminListResponse)
async def list_grants_admin(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="grant_opportunities", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch grants")


@router.post("/grants", response_model=dict)
async def create_grant_admin(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.create_row("grant_opportunities", body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create grants")


@router.patch("/grants/{item_id}", response_model=dict)
async def update_grant_admin(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.update_row("grant_opportunities", item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update grants")


@router.delete("/grants/{item_id}", response_model=SuccessResponse)
async def delete_grant_admin(
    item_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        service.delete_row("grant_opportunities", item_id)
        return SuccessResponse(message="grant item deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete grants")


@router.get("/festivals", response_model=AdminListResponse)
async def list_festivals_admin(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return _list_resource(service, table_name="film_festivals", limit=limit, offset=offset)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch festivals")


@router.post("/festivals", response_model=dict)
async def create_festival_admin(
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.create_row("film_festivals", body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to create festivals")


@router.patch("/festivals/{item_id}", response_model=dict)
async def update_festival_admin(
    item_id: str,
    body: AdminUpsertRequest,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        return service.update_row("film_festivals", item_id, body.payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update festivals")


@router.delete("/festivals/{item_id}", response_model=SuccessResponse)
async def delete_festival_admin(
    item_id: str,
    _: AdminUser = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
):
    try:
        service.delete_row("film_festivals", item_id)
        return SuccessResponse(message="festival item deleted")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to delete festivals")
