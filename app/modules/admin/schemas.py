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

