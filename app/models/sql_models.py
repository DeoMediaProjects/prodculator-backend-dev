from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class Admin(SQLModel, table=True):
    __tablename__: ClassVar[str] = "admins"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    email: str = Field(index=True, nullable=False, unique=True)
    password_hash: str
    name: str | None = None
    role: str = Field(default="master_admin")
    last_login: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class User(SQLModel, table=True):
    __tablename__: ClassVar[str] = "users"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    email: str = Field(index=True, nullable=False, unique=True)
    password_hash: str | None = None
    name: str | None = None
    company: str | None = None
    role: str | None = None
    user_type: str = Field(default="free")
    credits_remaining: int = 0
    plan: str = Field(default="free")
    is_blocked: bool = Field(default=False)
    blocked_at: datetime | None = None
    last_active: datetime | None = None
    # Billing geography captured from Stripe (ISO-3166 country code, state/province
    # code) — drives the admin Business Metrics geographic distribution.
    country: str | None = None
    state: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Subscription(SQLModel, table=True):
    __tablename__: ClassVar[str] = "subscriptions"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str | None = Field(default=None, index=True)
    stripe_customer_id: str | None = Field(default=None, index=True)
    stripe_subscription_id: str | None = Field(default=None, index=True)
    plan_type: str | None = None
    status: str | None = None
    report_limit: int | None = None
    amount_cents: int | None = None
    currency: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    cancelled_at: datetime | None = None
    pending_plan: str | None = None
    past_due_since: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Report(SQLModel, table=True):
    __tablename__: ClassVar[str] = "reports"  # type: ignore[assignment]
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    script_title: str
    script_file_path: str | None = None
    status: str = Field(default="processing")
    report_type: str = Field(default="free")
    share_token: str | None = Field(default=None, index=True)
    request_metadata: dict[str, Any] | None = Field(default=None, sa_column=Column("request_metadata", JSON))
    report_data: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    project_details: dict[str, Any] | None = Field(default=None, sa_column=Column("project_details", JSON))
    pdf_url: str | None = None
    downloaded: bool = Field(default=False)
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TerritoryWatchlist(SQLModel, table=True):
    __tablename__: ClassVar[str] = "territory_watchlist"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    territory: str = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ComparableProduction(SQLModel, table=True):
    __tablename__: ClassVar[str] = "comparable_productions"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    title: str
    year: int | None = None
    budget_usd: int | None = None
    primary_territory: str | None = None
    incentive_used: str | None = None
    genre: list[str] | None = Field(default=None, sa_column=Column(JSON))
    production_company: str | None = None
    director: str | None = None
    tmdb_id: str | None = Field(default=None, index=True)
    source: str = Field(default="Manual")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EmailGatingRecord(SQLModel, table=True):
    __tablename__: ClassVar[str] = "email_gating_records"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    email: str = Field(index=True, nullable=False)
    report_generated: bool = Field(default=False)
    blocked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DataSource(SQLModel, table=True):
    __tablename__: ClassVar[str] = "data_sources"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    category: str
    description: str | None = None
    endpoint: str | None = None
    enabled: bool = Field(default=True)
    status: str = Field(default="unknown")
    credential_mode: str = Field(default="backend_env")
    is_implemented: bool = Field(default=True)
    last_tested_at: datetime | None = None
    last_test_result: str | None = None
    last_test_message: str | None = None
    sync_schedule: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProductionMilestone(SQLModel, table=True):
    __tablename__: ClassVar[str] = "production_milestones"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    report_id: str | None = Field(default=None, index=True)
    title: str
    description: str | None = None
    status: str = Field(default="upcoming")  # completed, in-progress, upcoming
    due_date: str | None = None
    sort_order: int = Field(default=0)
    is_template: bool = Field(default=False)
    is_custom: bool = Field(default=False)
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MilestoneTask(SQLModel, table=True):
    __tablename__: ClassVar[str] = "milestone_tasks"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    milestone_id: str = Field(index=True, nullable=False)
    text: str
    completed: bool = Field(default=False)
    territory: str | None = None
    deadline: str | None = None
    sort_order: int = Field(default=0)
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProcessedWebhookEvent(SQLModel, table=True):
    """Deduplication table for Stripe webhook events (at-least-once delivery)."""

    __tablename__: ClassVar[str] = "processed_webhook_events"  # type: ignore[assignment]

    event_id: str = Field(primary_key=True)
    processed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TerritoryProfile(SQLModel, table=True):
    __tablename__: ClassVar[str] = "territory_profiles"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    territory: str = Field(index=True, unique=True, nullable=False)
    iso_code: str | None = None
    crew_depth_tier: str = Field(default="emerging")
    crew_depth_score: int = Field(default=30)
    crew_depth_notes: str | None = None
    infrastructure_tier: str = Field(default="emerging")
    infrastructure_score: int = Field(default=30)
    infrastructure_notes: str | None = None
    hemisphere: str = Field(default="northern")
    intl_productions_3yr: int | None = None
    intl_productions_source: str | None = None
    last_reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    review_notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
