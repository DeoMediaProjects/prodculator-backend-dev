from pydantic import BaseModel


class PlanDistributionItem(BaseModel):
    plan: str
    user_count: int
    revenue: float


class SubscriberMetricsResponse(BaseModel):
    total_paid_users: int
    mrr_usd: float
    mrr_gbp: float
    reports_this_month_total: int
    reports_this_month_free: int
    reports_this_month_paid: int
    avg_reports_per_user: float
    plan_distribution: list[PlanDistributionItem]


class StatusCounts(BaseModel):
    active: int
    past_due: int
    canceled: int


class SubscriberItem(BaseModel):
    id: str
    name: str | None = None
    email: str
    company: str | None = None
    plan: str
    status: str
    reports_this_month: int
    report_limit: int | None = None
    monthly_spend: float
    payment_currency: str
    join_date: str
    last_active: str | None = None
    total_reports_generated: int


class SubscriberListResponse(BaseModel):
    items: list[SubscriberItem]
    total: int
    limit: int
    offset: int
    counts: StatusCounts


class CreditAdjustRequest(BaseModel):
    adjustment: int
    reason: str | None = None
