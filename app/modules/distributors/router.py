from fastapi import APIRouter, Depends, HTTPException

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.modules.distributors.schemas import Distributor
from app.modules.distributors.service import DistributorsService

router = APIRouter(prefix="/api/distributors", tags=["Distributors"])


def get_distributors_service(supabase: DatabaseClient = Depends(get_supabase)) -> DistributorsService:
    return DistributorsService(supabase)


@router.get("", response_model=list[Distributor])
async def list_distributors(service: DistributorsService = Depends(get_distributors_service)):
    """List film distributors sorted by name."""
    try:
        return service.get_distributors()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch distributors")
