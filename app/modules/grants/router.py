from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.database_client import DatabaseClient

from app.core.dependencies import get_supabase
from app.modules.grants.schemas import GrantOpportunity
from app.modules.grants.service import GrantsService

router = APIRouter(prefix="/api/grants", tags=["Grants"])


def get_grants_service(supabase: DatabaseClient = Depends(get_supabase)) -> GrantsService:
    return GrantsService(supabase)


@router.get("", response_model=list[GrantOpportunity])
async def list_grants(
    territory: str | None = Query(None),
    service: GrantsService = Depends(get_grants_service),
):
    """List grant opportunities, optionally filtered by territory."""
    try:
        return service.get_grants(territory=territory)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch grants")

