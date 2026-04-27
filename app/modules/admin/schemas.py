from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class AdminUser(BaseModel):
    id: str
    email: str
    name: str | None = None
    role: str = "master_admin"


class AdminTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AdminUser


class AdminRecord(BaseModel):
    model_config = ConfigDict(extra="allow")


class AdminListResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class AdminUpsertRequest(BaseModel):
    payload: dict[str, Any]


class BusinessMetricsResponse(BaseModel):
    total_users: int
    active_subscriptions: int
    total_reports: int
    reports_this_month: int
    mrr_usd: float
    conversion_rate_percent: float


class ProductionSignalRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | UUID
    script_id: str | UUID | None = None
    territory: str | None = None
    state: str | None = None
    submission_date: str | date | datetime | None = None
    camera_equipment: Any | None = None
    crew_size: int | None = None
    principal_cast: int | None = None
    supporting_cast: int | None = None
    background_extras: int | None = None
    budget_range: str | None = None
    format: str | None = None
    genres: Any | None = None


class ProductionSignalsResponse(BaseModel):
    items: list[ProductionSignalRecord]
    total: int


class SyncStatusResponse(BaseModel):
    territoriesSyncing: int
    pendingChanges: int
    daysSinceLastCheck: int
    nextScheduledCheck: str | None = None


class PendingChangeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    territory: str
    field: str
    currentValue: str | None = None
    detectedValue: str
    confidence: str
    source: str | None = None
    status: str = "pending"
    createdAt: str | None = None
    resourceId: str | None = None
    recordLabel: str | None = None
    resolvedAt: str | None = None


class SyncSettingsResponse(BaseModel):
    schedule: str | None = None
    enabled: bool = True
    lastSyncAt: str | None = None
    nextScheduledCheck: str | None = None


class SyncSettingsUpdateRequest(BaseModel):
    schedule: str | None = None
    enabled: bool | None = None


class AdminUserDetail(BaseModel):
    id: str
    email: str
    name: str | None = None
    role: str
    last_login: str | None = None
    created_at: str


class AdminUserListResponse(BaseModel):
    items: list[AdminUserDetail]
    total: int
    limit: int
    offset: int


class AdminUserCreateRequest(BaseModel):
    name: str | None = None
    email: EmailStr
    role: str


class AdminUserUpdateRequest(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    role: str | None = None
    password: str | None = None


class AdminUserCreateResponse(BaseModel):
    admin: AdminUserDetail
    temporary_password: str


# ── Activity feed ─────────────────────────────────────────────────────────────

class ActivityItem(BaseModel):
    id: str
    type: str  # "report_generated" | "user_registered" | "subscription_activated"
    description: str
    user_email: str | None = None
    timestamp: str | None = None


class ActivityResponse(BaseModel):
    items: list[ActivityItem]


# ── System status ─────────────────────────────────────────────────────────────

class ServiceStatusItem(BaseModel):
    name: str
    status: str  # "operational" | "degraded" | "down" | "unknown"
    last_checked: str


class SystemStatusResponse(BaseModel):
    services: list[ServiceStatusItem]
    checked_at: str


# ── Derived tasks ─────────────────────────────────────────────────────────────

class TaskItem(BaseModel):
    task: str
    priority: str  # "high" | "medium" | "low"
    due: str


class TasksResponse(BaseModel):
    items: list[TaskItem]
