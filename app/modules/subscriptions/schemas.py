from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SubscriptionRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    user_id: str | None = None
    status: str | None = None


class ActiveSubscriptionResponse(BaseModel):
    subscription: SubscriptionRecord | None = None


class CanGenerateResponse(BaseModel):
    can_generate: bool
    reason: str


class SubscriptionStatusResponse(BaseModel):
    plan: str
    subscription: SubscriptionRecord | None = None
    can_generate: bool
    reason: str


# --- Plan-change endpoints --------------------------------------------------


class PreviewChangeRequest(BaseModel):
    target_price_id: str


class PreviewChangeResponse(BaseModel):
    direction: str  # "upgrade" | "downgrade" | "same"
    target_plan: str
    immediate_total: int
    proration_credit: int
    next_invoice_total: int
    currency: str
    effective_date: datetime | None = None
    billing_cycle_changes: bool = False


class ChangePlanRequest(BaseModel):
    target_price_id: str
    idempotency_key: str


class ChangePlanResponse(BaseModel):
    status: str  # "applied" (upgrade) | "scheduled" (downgrade)
    direction: str  # "upgrade" | "downgrade"
    target_plan: str
    effective_at: datetime | None = None


class CurrentSubscriptionResponse(BaseModel):
    subscription: SubscriptionRecord | None = None
    plan: str  # normalized current plan from user.plan
    pending_plan: str | None = None  # set when a downgrade is scheduled
    past_due_since: datetime | None = None
    cancel_at_period_end: bool = False


class UsageResponse(BaseModel):
    plan: str
    reports_used: int
    reports_limit: int | None  # None means unlimited
    reports_remaining: int | None  # None means unlimited
    credits_remaining: int
    period_start: datetime | None = None
    period_end: datetime | None = None
    can_generate: bool
    reason: str


class CancelScheduledChangeResponse(BaseModel):
    cancelled: bool


class InvoiceItem(BaseModel):
    id: str
    number: str | None = None
    status: str
    amount_paid: int  # pence/cents
    amount_due: int
    currency: str
    created: int | None = None  # Unix timestamp
    period_start: int | None = None  # Unix timestamp
    period_end: int | None = None  # Unix timestamp
    hosted_invoice_url: str | None = None
    invoice_pdf: str | None = None
    description: str | None = None


class InvoicesResponse(BaseModel):
    invoices: list[InvoiceItem]
    has_more: bool = False

