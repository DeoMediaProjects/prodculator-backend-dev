"""What-If Calculator API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.modules.calculator.schemas import ScenarioRequest, ScenarioResponse
from app.modules.calculator.service import CalculatorService

router = APIRouter(prefix="/api/calculator", tags=["Calculator"])


@router.post("/scenario", response_model=ScenarioResponse)
async def compute_scenario(
    request: ScenarioRequest,
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> ScenarioResponse:
    """Compute What-If scenario comparing financial returns across territories.

    Public endpoint — no authentication required.
    Accepts budget, currency, VFX allocation, format, and priority mode.
    Returns per-territory incentive estimates, crew costs, currency advantages,
    and an overall weighted score.
    """
    service = CalculatorService(supabase, settings)
    return service.compute_scenario(request)
