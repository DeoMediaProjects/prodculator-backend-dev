from fastapi import APIRouter, Depends, HTTPException
from app.core.database_client import DatabaseClient

from app.core.dependencies import get_supabase
from app.modules.festivals.schemas import FilmFestival
from app.modules.festivals.service import FestivalsService

router = APIRouter(prefix="/api/festivals", tags=["Festivals"])


def get_festivals_service(supabase: DatabaseClient = Depends(get_supabase)) -> FestivalsService:
    return FestivalsService(supabase)


@router.get("", response_model=list[FilmFestival])
async def list_festivals(service: FestivalsService = Depends(get_festivals_service)):
    """List film festivals sorted by upcoming submission deadline."""
    try:
        return service.get_festivals()
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch festivals")

