"""Milestone and task schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


# ── Task schemas ──────────────────────────────────────────────────────────────


class TaskCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    territory: str | None = None
    deadline: str | None = None  # ISO date string


class TaskUpdate(BaseModel):
    text: str | None = None
    completed: bool | None = None
    territory: str | None = None
    deadline: str | None = None


class TaskResponse(BaseModel):
    id: str
    milestone_id: str
    text: str
    completed: bool
    territory: str | None = None
    deadline: str | None = None
    sort_order: int
    completed_at: str | None = None
    created_at: str


# ── Milestone schemas ─────────────────────────────────────────────────────────


class MilestoneCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    due_date: str | None = None  # ISO date string
    report_id: str | None = None


class MilestoneUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: Literal["completed", "in-progress", "upcoming"] | None = None
    due_date: str | None = None
    sort_order: int | None = None


class MilestoneResponse(BaseModel):
    id: str
    user_id: str
    report_id: str | None = None
    title: str
    description: str | None = None
    status: str
    due_date: str | None = None
    sort_order: int
    is_template: bool
    is_custom: bool
    completed_at: str | None = None
    created_at: str
    updated_at: str
    tasks: list[TaskResponse] = []


class MilestoneListResponse(BaseModel):
    milestones: list[MilestoneResponse]
