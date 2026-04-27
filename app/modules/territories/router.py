"""Territory comparison API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import RequirePlan, get_supabase
from app.modules.auth.schemas import AuthUser
from app.modules.territories.schemas import (
    TerritoryCompareResponse,
    TerritoryListResponse,
)
from app.modules.territories.service import TerritoryService

router = APIRouter(prefix="/api/territories", tags=["Territories"])


def _get_service(
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> TerritoryService:
    return TerritoryService(supabase, settings)


@router.get("", response_model=TerritoryListResponse)
async def list_territories(
    service: TerritoryService = Depends(_get_service),
) -> TerritoryListResponse:
    """Return all available territories for the comparison picker.

    Public endpoint — no authentication required.
    """
    return service.list_territories()


@router.get("/compare", response_model=TerritoryCompareResponse)
async def compare_territories(
    territories: str = Query(
        ...,
        description="Comma-separated territory labels (max 4)",
    ),
    currency: str = Query("GBP", description="Display currency for crew costs"),
    user: AuthUser = Depends(RequirePlan("professional")),
    service: TerritoryService = Depends(_get_service),
) -> TerritoryCompareResponse:
    """Compare up to 4 territories side-by-side.

    Requires Professional plan or higher.
    Returns incentive data, crew costs, highlights, and restrictions
    for each territory.
    """
    labels = [t.strip() for t in territories.split(",") if t.strip()][:4]
    return service.compare_territories(labels, currency)
