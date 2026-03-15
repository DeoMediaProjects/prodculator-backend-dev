from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.database_client import DatabaseClient

from app.core.dependencies import get_current_user, get_supabase
from app.core.schemas import SuccessResponse
from app.core.territories import resolve_territory
from app.modules.auth.schemas import AuthUser
from app.modules.watchlist.schemas import WatchlistAddRequest, WatchlistResponse
from app.modules.watchlist.service import WatchlistService

router = APIRouter(prefix="/api/watchlist", tags=["Watchlist"])


def get_watchlist_service(supabase: DatabaseClient = Depends(get_supabase)) -> WatchlistService:
    return WatchlistService(supabase)


@router.get("", response_model=WatchlistResponse)
async def get_watchlist(
    user: AuthUser = Depends(get_current_user),
    service: WatchlistService = Depends(get_watchlist_service),
):
    """Get the current user's territory watchlist."""
    try:
        territories = service.get_watchlist(user.id)
        return WatchlistResponse(territories=territories)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch watchlist")


@router.post("", response_model=SuccessResponse)
async def add_to_watchlist(
    body: WatchlistAddRequest,
    user: AuthUser = Depends(get_current_user),
    service: WatchlistService = Depends(get_watchlist_service),
):
    """Add a territory to the current user's watchlist."""
    try:
        service.add_territory(user.id, body.territory)
        return SuccessResponse(message="Territory added to watchlist")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to add territory")


@router.delete("", response_model=SuccessResponse)
async def remove_from_watchlist(
    territory: str = Query(...),
    user: AuthUser = Depends(get_current_user),
    service: WatchlistService = Depends(get_watchlist_service),
):
    """Remove a territory from the current user's watchlist."""
    try:
        t = resolve_territory(territory)
        if t:
            territory = t.label
        service.remove_territory(user.id, territory)
        return SuccessResponse(message="Territory removed from watchlist")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to remove territory")

