"""What-If Calculator API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import RequirePlan, get_optional_user, get_supabase
from app.modules.auth.schemas import AuthUser
from app.modules.calculator.schemas import ScenarioRequest, ScenarioResponse
from app.modules.calculator.service import CalculatorService

router = APIRouter(prefix="/api/calculator", tags=["Calculator"])

# Fields the public lead-magnet page (/what-if) advertises as locked:
# currency advantage, net saving, minimum spend, payment timelines.
# Everything else (programme, rates, rebate, qualifying spend, tier ratings)
# is the teaser. (Crew day-rate fields removed from the platform 2026-07.)
_TEASER_REDACTIONS: dict = {
    "currency_advantage_score": 0,
    "currency_advantage_warning": None,
    "fx_rate": None,
    "fx_rate_date": None,
    "net_saving": 0.0,
    "net_saving_display": "Sign up to view",
    "payment_timeline": None,
    "min_spend": None,
    "financial_return_score": None,
    "financial_return_verdict": None,
    "bankability_label": None,
}


def _has_full_access(user: AuthUser | None) -> bool:
    if user is None:
        return False
    from app.models.enums import normalize_plan

    hierarchy = RequirePlan._PLAN_HIERARCHY
    return hierarchy.get(normalize_plan(user.plan), 0) >= hierarchy.get("professional", 0)


def _redact_for_teaser(response: ScenarioResponse) -> ScenarioResponse:
    for territory in response.territories:
        for field_name, value in _TEASER_REDACTIONS.items():
            setattr(territory, field_name, value)
    return response


@router.post("/scenario", response_model=ScenarioResponse)
async def compute_scenario(
    request: ScenarioRequest,
    user: AuthUser | None = Depends(get_optional_user),
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> ScenarioResponse:
    """Compute What-If scenario comparing financial returns across territories.

    Two tiers of output from the same deterministic engine (handoff §7):
    - Anonymous / below-Professional: teaser — programme, rates, rebate and
      qualifying spend per territory; the premium fields are redacted. This
      serves the public /what-if lead magnet.
    - Professional and above: the full scenario.
    """
    service = CalculatorService(supabase, settings)
    result = service.compute_scenario(request)
    if not _has_full_access(user):
        return _redact_for_teaser(result)
    return result
