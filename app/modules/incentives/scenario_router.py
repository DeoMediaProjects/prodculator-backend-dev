"""Scenario question endpoint for the analysis wizard.

The wizard sends the jurisdictions the producer has selected and the production
structure mode, and receives one question set per jurisdiction. It renders what
comes back. It must not decide which questions belong to which territory, because
that is programme data and changing it should not need a deployment.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_user, get_supabase
from app.modules.auth.schemas import AuthUser
from app.modules.incentives.v2_contracts import STRUCTURE_MODES
from app.modules.incentives.v2_jurisdictions import (
    UnknownJurisdiction,
    all_scenario_jurisdictions,
)
from app.modules.incentives.v2_scenario_service import (
    ScenarioLimitExceeded,
    ScenarioQuestionService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scenarios", tags=["Scenarios"])


def _get_service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> ScenarioQuestionService:
    return ScenarioQuestionService(supabase)


@router.get("/jurisdictions")
async def list_jurisdictions() -> dict:
    """Every jurisdiction a scenario may name, with its canonical IDs.

    The picker needs these to send canonical IDs rather than labels. Free text
    must never resolve to a financial programme, so the client is given the exact
    set it may choose from.
    """
    return {
        "jurisdictions": [
            {
                "label": j.label,
                "territoryId": j.territory_id,
                "subdivisionId": j.subdivision_id,
                "scenarioKey": j.scenario_key,
                "isStatutorySubdivision": j.is_statutory_subdivision,
            }
            for j in all_scenario_jurisdictions()
        ]
    }


@router.get("/questions")
async def scenario_questions(
    territories: str = Query(
        ...,
        description=(
            "Comma-separated canonical territory labels, ISO codes or "
            "subdivision codes, for example 'GB,CA-BC,US-CA'"
        ),
    ),
    mode: str = Query(
        "comparison",
        description="comparison | coproduction | undecided",
    ),
    user: AuthUser = Depends(get_current_user),
    service: ScenarioQuestionService = Depends(_get_service),
) -> dict:
    """Which statutory inputs to ask for, per selected jurisdiction."""
    if mode not in STRUCTURE_MODES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"mode must be one of {', '.join(STRUCTURE_MODES)}, got {mode!r}"
            ),
        )

    names = [t.strip() for t in territories.split(",") if t.strip()]
    plan = getattr(user, "plan", None) or "free"

    try:
        return service.question_sets(names, mode=mode, plan=plan)
    except UnknownJurisdiction as exc:
        # 422 rather than 404: the request is malformed for a financial lookup,
        # and the client needs to send a canonical ID instead of retrying.
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ScenarioLimitExceeded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception:
        logger.exception(
            "Scenario question resolution failed: territories=%s mode=%s",
            names, mode,
        )
        raise HTTPException(
            status_code=500, detail="Could not resolve scenario questions",
        ) from None
