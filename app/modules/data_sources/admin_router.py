import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.permissions import RequirePermission
from app.modules.admin.schemas import AdminUser
from app.modules.data_sources.rate_limit import check_test_rate_limit
from app.modules.data_sources.schemas import (
    BulkConfigurationRequest,
    BulkConfigurationResponse,
    DataSourceListResponse,
    DataSourceResponse,
    DataSourceTestResponse,
    DataSourceUpdateRequest,
    SyncScheduleResponse,
)
from app.modules.data_sources.service import DataSourceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/data-sources", tags=["Admin - Data Sources"])


def get_data_source_service(
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> DataSourceService:
    return DataSourceService(supabase, settings)


# Static routes MUST be defined before /{source_id} to avoid path collisions


@router.get("/sync-schedule", response_model=SyncScheduleResponse)
async def get_sync_schedule(
    _: AdminUser = Depends(RequirePermission("canManageDataSources")),
    service: DataSourceService = Depends(get_data_source_service),
):
    try:
        items = service.get_sync_schedule()
        return SyncScheduleResponse(items=items)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch sync schedule")


@router.put("/configuration", response_model=BulkConfigurationResponse)
async def bulk_configure(
    body: BulkConfigurationRequest,
    _: AdminUser = Depends(RequirePermission("canManageDataSources")),
    service: DataSourceService = Depends(get_data_source_service),
):
    try:
        items = [{"id": s.id, "enabled": s.enabled} for s in body.sources]
        updated = service.bulk_configure(items)
        return BulkConfigurationResponse(updated=updated)
    except Exception:
        raise HTTPException(
            status_code=400, detail="Failed to save configuration"
        )


@router.get("", response_model=DataSourceListResponse)
async def list_data_sources(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: AdminUser = Depends(RequirePermission("canManageDataSources")),
    service: DataSourceService = Depends(get_data_source_service),
):
    try:
        items, total = service.list_sources(limit=limit, offset=offset)
        return DataSourceListResponse(
            items=items, total=total, limit=limit, offset=offset
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch data sources")


@router.get("/{source_id}", response_model=DataSourceResponse)
async def get_data_source(
    source_id: str,
    _: AdminUser = Depends(RequirePermission("canManageDataSources")),
    service: DataSourceService = Depends(get_data_source_service),
):
    try:
        source = service.get_source(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Data source not found")
        return source
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch data source")


@router.patch("/{source_id}", response_model=DataSourceResponse)
async def update_data_source(
    source_id: str,
    body: DataSourceUpdateRequest,
    _: AdminUser = Depends(RequirePermission("canManageDataSources")),
    service: DataSourceService = Depends(get_data_source_service),
):
    try:
        payload = body.model_dump(exclude_none=True)
        if not payload:
            raise HTTPException(status_code=400, detail="No fields to update")
        source = service.update_source(source_id, payload)
        return source
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to update data source")


@router.post("/{source_id}/test", response_model=DataSourceTestResponse)
async def test_data_source(
    source_id: str,
    _: AdminUser = Depends(RequirePermission("canManageDataSources")),
    service: DataSourceService = Depends(get_data_source_service),
):
    source = service.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")

    check_test_rate_limit(source["slug"])

    try:
        result = service.test_connection(source_id)
        return result
    except Exception:
        logger.exception("Connection test failed for %s", source_id)
        raise HTTPException(status_code=500, detail="Connection test failed")
