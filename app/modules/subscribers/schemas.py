from pydantic import BaseModel


class PlanDistributionItem(BaseModel):
    plan: str
    user_count: int
    revenue: float


class SubscriberMetricsResponse(BaseModel):
    total_paid_users: int
    mrr_usd: float
    mrr_gbp: float
    #: How many active subscriptions were valued at plan list price because no
    #: amount was recorded on the row.
    mrr_estimated_subscriptions: int = 0
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
    #: True when no amount was recorded on the subscription and the plan's list
    #: price stood in, so the UI can mark the figure as estimated rather than
    #: presenting an imputed number as billed.
    monthly_spend_estimated: bool = False
    payment_currency: str
    join_date: str
    last_active: str | None = None
    total_reports_generated: int


class SubscriberListResponse(BaseModel):
    items: list[SubscriberItem]
    #: Rows the API could not render, usually an unexpected NULL in a column the
    #: response model requires. Reported rather than swallowed: a silently short
    #: list reads as "these are all the subscribers", which is worse than an
    #: error, and the server log names the offending user_id.
    unreadable: int = 0
    total: int
    limit: int
    offset: int
    counts: StatusCounts


class CreditAdjustRequest(BaseModel):
    adjustment: int
    reason: str | None = None
