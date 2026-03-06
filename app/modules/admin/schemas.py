from typing import Any

from pydantic import BaseModel, ConfigDict


class AdminUser(BaseModel):
    id: str
    email: str
    name: str | None = None


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


class ProductionSignalsResponse(BaseModel):
    items: list[dict[str, Any]]
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

