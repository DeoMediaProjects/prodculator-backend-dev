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
    last_active: datetime | None = None
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
    pdf_url: str | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TerritoryWatchlist(SQLModel, table=True):
    __tablename__: ClassVar[str] = "territory_watchlist"  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    territory: str = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
