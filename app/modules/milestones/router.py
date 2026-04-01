"""Milestone CRUD API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_user, get_supabase
from app.core.schemas import SuccessResponse
from app.modules.auth.schemas import AuthUser
from app.modules.milestones.schemas import (
    MilestoneCreate,
    MilestoneUpdate,
    MilestoneListResponse,
    MilestoneResponse,
    TaskCreate,
    TaskUpdate,
    TaskResponse,
)
from app.modules.milestones.service import MilestoneService

router = APIRouter(prefix="/api/milestones", tags=["Milestones"])


def _get_service(supabase: DatabaseClient = Depends(get_supabase)) -> MilestoneService:
    return MilestoneService(supabase)


# ── Milestones ────────────────────────────────────────────────────────────────


@router.get("", response_model=MilestoneListResponse)
async def list_milestones(
    report_id: str | None = Query(None, description="Filter by report ID"),
    user: AuthUser = Depends(get_current_user),
    service: MilestoneService = Depends(_get_service),
) -> MilestoneListResponse:
    """List all milestones for the current user."""
    milestones = service.list_milestones(user.id, report_id)
    return MilestoneListResponse(milestones=milestones)


@router.post("", response_model=MilestoneResponse, status_code=201)
async def create_milestone(
    data: MilestoneCreate,
    user: AuthUser = Depends(get_current_user),
    service: MilestoneService = Depends(_get_service),
) -> MilestoneResponse:
    """Create a custom milestone."""
    return service.create_milestone(user.id, data)


@router.patch("/{milestone_id}", response_model=MilestoneResponse)
async def update_milestone(
    milestone_id: str,
    data: MilestoneUpdate,
    user: AuthUser = Depends(get_current_user),
    service: MilestoneService = Depends(_get_service),
) -> MilestoneResponse:
    """Update a milestone's title, description, status, or due date."""
    result = service.update_milestone(user.id, milestone_id, data)
    if result is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return result


@router.delete("/{milestone_id}", response_model=SuccessResponse)
async def delete_milestone(
    milestone_id: str,
    user: AuthUser = Depends(get_current_user),
    service: MilestoneService = Depends(_get_service),
) -> SuccessResponse:
    """Delete a custom milestone. Template milestones cannot be deleted."""
    deleted = service.delete_milestone(user.id, milestone_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Milestone not found or is a template milestone",
        )
    return SuccessResponse(message="Milestone deleted")


# ── Tasks ─────────────────────────────────────────────────────────────────────


@router.post("/{milestone_id}/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    milestone_id: str,
    data: TaskCreate,
    user: AuthUser = Depends(get_current_user),
    service: MilestoneService = Depends(_get_service),
) -> TaskResponse:
    """Add a task to a milestone."""
    result = service.create_task(user.id, milestone_id, data)
    if result is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return result


@router.patch("/{milestone_id}/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    milestone_id: str,
    task_id: str,
    data: TaskUpdate,
    user: AuthUser = Depends(get_current_user),
    service: MilestoneService = Depends(_get_service),
) -> TaskResponse:
    """Update a task (toggle completion, edit text, etc.)."""
    result = service.update_task(user.id, milestone_id, task_id, data)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@router.delete("/{milestone_id}/tasks/{task_id}", response_model=SuccessResponse)
async def delete_task(
    milestone_id: str,
    task_id: str,
    user: AuthUser = Depends(get_current_user),
    service: MilestoneService = Depends(_get_service),
) -> SuccessResponse:
    """Delete a task from a milestone."""
    deleted = service.delete_task(user.id, milestone_id, task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return SuccessResponse(message="Task deleted")


# ── Seeding ───────────────────────────────────────────────────────────────────


@router.post("/seed/{report_id}", response_model=MilestoneListResponse)
async def seed_milestones(
    report_id: str,
    user: AuthUser = Depends(get_current_user),
    service: MilestoneService = Depends(_get_service),
) -> MilestoneListResponse:
    """Auto-generate milestones from a report's analysis data.

    Replaces any existing template milestones for this report.
    """
    milestones = service.seed_from_report(user.id, report_id)
    if not milestones:
        raise HTTPException(
            status_code=404,
            detail="Report not found or has no analysis data",
        )
    return MilestoneListResponse(milestones=milestones)
