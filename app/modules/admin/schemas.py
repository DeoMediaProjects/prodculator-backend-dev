from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class AdminUser(BaseModel):
    id: str
    email: str
    name: str | None = None
    # Fail CLOSED: a missing/blank role grants ZERO permissions (an empty role
    # is not a key in ROLE_PERMISSIONS, so has_permission() returns False for
    # everything). A row that predates the role column, or has a NULL role,
    # must never be silently treated as a master_admin.
    role: str = ""


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


# ── Audit trail (handoff §4.4/§4.5) ───────────────────────────────────────────

class AuditLogEntry(BaseModel):
    """One recorded admin mutation. Read-only — nothing accepts this as input."""

    id: str
    actor_id: str | None = None
    actor_email: str | None = None
    actor_role: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    before_json: Any = None
    after_json: Any = None
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    # None when the request never produced a status (recorded mid-flight).
    succeeded: bool | None = None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogEntry]
    total: int
    limit: int
    offset: int


class AuditLogActorFacet(BaseModel):
    actor_id: str | None = None
    actor_email: str | None = None
    count: int


class AuditLogFacets(BaseModel):
    actors: list[AuditLogActorFacet] = []
    actions: list[str] = []
    resource_types: list[str] = []


class AuditRetentionResponse(BaseModel):
    retention_days: int
    retains_indefinitely: bool
    total_entries: int
    failed_entries: int
    oldest_entry_at: str | None = None
    newest_entry_at: str | None = None


class BusinessMetricsResponse(BaseModel):
    total_users: int
    active_subscriptions: int
    total_reports: int
    reports_this_month: int
    mrr_usd: float
    #: How many active subscriptions were valued at plan list price because no
    #: amount was recorded on the row. Non-zero means the figure is partly
    #: imputed and must not be presented as billed.
    mrr_estimated_subscriptions: int = 0
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
    # live check: operational | degraded | down
    # configuration check: configured | not_configured
    status: str
    # What the row is based on. "live" means a probe ran during the request;
    # "configuration" means only credential presence was inspected. Without this
    # the UI cannot tell the reader which it is, and a config row reads as a
    # health result.
    check: Literal["live", "configuration"] = "live"
    # One line on what was measured, e.g. "PING acknowledged".
    detail: str | None = None
    # Null on configuration rows: nothing was checked, so there is no time at
    # which it was checked. Previously these carried `now`, which fabricated
    # freshness for a probe that never ran.
    last_checked: str | None = None


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


# ── Business Metrics dashboard ────────────────────────────────────────────────

class CurrencyAmount(BaseModel):
    currency: str
    amount: float


class PlanCount(BaseModel):
    plan: str
    count: int


class RoleCount(BaseModel):
    role: str
    count: int


class GeoCountry(BaseModel):
    country_code: str
    country: str
    users: int
    percentage: float
    revenue_usd: float


class GeoState(BaseModel):
    state_code: str
    state: str
    users: int
    revenue_usd: float


class BusinessMetricsDashboardResponse(BaseModel):
    total_users: int
    total_paid_users: int
    active_subscriptions: int
    mrr_usd: float
    arr_usd: float
    # Active subscriptions valued at plan list price because the row carried
    # no amount. Lets the dashboard label the figure as partly estimated.
    mrr_estimated_subscriptions: int = 0
    mrr_by_currency: list[CurrencyAmount]
    monthly_churn_percent: float
    free_to_paid_percent: float
    avg_days_to_convert: float | None = None
    activation_rate_percent: float
    plan_distribution: list[PlanCount]
    role_distribution: list[RoleCount]
    geo_available: bool
    geographic: list[GeoCountry]
    us_states: list[GeoState]
