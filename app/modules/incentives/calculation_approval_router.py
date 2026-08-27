"""Admin endpoints for the calculation approval gate.

The queue and the promotion live behind admin auth because promoting a programme
changes what every future report is willing to state as a figure. Nothing here is
reachable by a producer.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.audit import AuditedAPIRoute
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_admin, get_supabase
from app.modules.admin.schemas import AdminUser
from app.modules.incentives.calculation_approval import (
    ALLOWED_STATUSES,
    ApprovalRefused,
    CalculationApprovalService,
)

logger = logging.getLogger(__name__)

# Audited like every other admin router. Promoting a programme changes what every
# future report is willing to state as a figure, so the mutation must leave a
# trace independent of the note recorded on the row itself.
router = APIRouter(
    prefix="/api/admin/calculation-approval",
    tags=["Admin"],
    route_class=AuditedAPIRoute,
)


def _service(
    supabase: DatabaseClient = Depends(get_supabase),
) -> CalculationApprovalService:
    return CalculationApprovalService(supabase)


class SetStatusRequest(BaseModel):
    status: str = Field(..., description="ready | conditional | blocked")
    #: What was checked. Required by the service, not merely by the schema: an
    #: unexplained approval cannot be reviewed later.
    note: str = Field(..., min_length=1)
    #: Override the pre-flight checks. A reviewer with the statute open may
    #: legitimately know better; refusing outright would push the change into raw
    #: SQL where nothing is recorded at all. Recorded in the note.
    force: bool = False


@router.get("/queue")
async def approval_queue(
    status: str = Query("blocked", description="Gate value to list"),
    _: AdminUser = Depends(get_current_admin),
    service: CalculationApprovalService = Depends(_service),
) -> dict:
    """Programmes at a gate value, with what each still needs before approval."""
    if status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {', '.join(ALLOWED_STATUSES)}",
        )
    try:
        entries = service.queue(status)
    except Exception:
        logger.exception("Failed to load calculation approval queue: status=%s", status)
        raise HTTPException(
            status_code=500, detail="Could not load the approval queue",
        ) from None
    return {
        "status": status,
        "total": len(entries),
        "readyForReview": sum(1 for e in entries if e["canApprove"]),
        "programmes": entries,
    }


@router.post("/{programme_id}")
async def set_calculation_status(
    programme_id: str,
    body: SetStatusRequest,
    admin: AdminUser = Depends(get_current_admin),
    service: CalculationApprovalService = Depends(_service),
) -> dict:
    """Promote or demote one programme's calculation gate.

    The reviewer is taken from the authenticated admin rather than from the body:
    a caller must not be able to attribute an approval to somebody else.
    """
    reviewer = getattr(admin, "email", None) or getattr(admin, "id", None) or ""
    try:
        return service.set_status(
            programme_id,
            status=body.status,
            reviewer=str(reviewer),
            note=body.note,
            force=body.force,
        )
    except ApprovalRefused as exc:
        # 422 rather than 409: the reviewer needs to change the request or supply
        # the missing data, not retry it.
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        logger.exception(
            "Failed to set calculation gate: programme=%s status=%s",
            programme_id, body.status,
        )
        raise HTTPException(
            status_code=500, detail="Could not change the calculation gate",
        ) from None
