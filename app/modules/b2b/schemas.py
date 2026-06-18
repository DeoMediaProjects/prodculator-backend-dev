from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


B2BProductType = Literal[
    "camera_equipment",
    "production_services",
    "crew_casting",
    "production_trend",
    "enterprise",
]
B2BCurrency = Literal["gbp", "usd"]
B2BDeliveryFrequency = Literal["monthly", "quarterly"]


class B2BProductResponse(BaseModel):
    product_type: B2BProductType
    title: str
    audience: str
    description: str
    features: list[str]
    price_gbp_cents: int | None = None
    price_usd_cents: int | None = None
    self_service: bool
    stripe_price_configured: dict[str, bool]


class B2BCheckoutRequest(BaseModel):
    product_type: B2BProductType
    currency: B2BCurrency = "gbp"
    delivery_frequency: B2BDeliveryFrequency = "monthly"
    extra_recipient_email: EmailStr | None = None


class B2BCheckoutResponse(BaseModel):
    session_id: str
    url: str


class B2BIntelligenceRequestCreate(BaseModel):
    product_type: B2BProductType
    period_start: date
    period_end: date
    extra_recipient_email: EmailStr | None = None

    @model_validator(mode="after")
    def validate_period(self) -> "B2BIntelligenceRequestCreate":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class B2BSubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    product_type: B2BProductType
    status: str
    source: str
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    price_id: str | None = None
    amount_cents: int | None = None
    currency: str | None = None
    delivery_frequency: str
    extra_recipient_email: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    next_delivery_at: datetime | None = None
    cancel_at_period_end: bool = False
    company_name: str | None = None
    admin_notes: str | None = None
    created_at: datetime
    updated_at: datetime


class B2BIntelligenceRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    b2b_subscription_id: str | None = None
    product_type: B2BProductType
    status: str
    request_type: str
    period_start: date
    period_end: date
    recipient_email: str
    extra_recipient_email: str | None = None
    pdf_url: str | None = None
    download_url: str | None = None
    metrics: dict[str, Any] | None = None
    error_message: str | None = None
    delivered_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class B2BSubscriptionListResponse(BaseModel):
    items: list[B2BSubscriptionResponse]


class B2BIntelligenceRequestListResponse(BaseModel):
    items: list[B2BIntelligenceRequestResponse]
    total: int


class AdminB2BManualSubscriptionCreate(BaseModel):
    user_email: EmailStr
    product_type: B2BProductType
    delivery_frequency: B2BDeliveryFrequency = "monthly"
    extra_recipient_email: EmailStr | None = None
    status: str = "active"
    company_name: str | None = None
    admin_notes: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed = {"active", "trialing", "past_due", "cancelled", "inactive"}
        if value not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return value


class AdminB2BSubscriptionUpdate(BaseModel):
    status: str | None = None
    delivery_frequency: B2BDeliveryFrequency | None = None
    extra_recipient_email: EmailStr | None = None
    next_delivery_at: datetime | None = None
    company_name: str | None = None
    admin_notes: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = {"active", "trialing", "past_due", "cancelled", "inactive"}
        if value not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return value


class AdminB2BRequestListResponse(BaseModel):
    items: list[B2BIntelligenceRequestResponse]
    total: int


class AdminB2BResendResponse(BaseModel):
    sent: bool
    recipients: list[str]

