"""baseline_core_tables

Revision ID: 000000000001
Revises:
Create Date: 2026-07-10

Foundation for a fresh database. The application's core tables (users,
subscriptions, reports, comparable_productions, territory_watchlist) were
historically created by SQLModel's create_all (AUTO_CREATE_DB_SCHEMA) and
never by a migration — so `alembic upgrade head` on an empty database
crashed at the first migration that ALTERs them (e.g. a1b2c3d4e5f6 on
reports). This baseline creates them in their ORIGINAL pre-history shape;
columns added later in the chain (is_blocked, google_uid, email_verified,
billing geo, pending_plan, stripe_schedule_id, request_metadata,
downloaded, project_details, tmdb_id, source, ...) are deliberately absent
here so the historical ALTER migrations replay cleanly.

All statements are IF NOT EXISTS, so databases provisioned the old way
(create_all + stamped) are untouched. c0f8fa0737aa now revises this
baseline instead of None.
"""
from __future__ import annotations

from alembic import op

revision = "000000000001"
down_revision = None
branch_labels = None
depends_on = None

_DDL = """
CREATE TABLE IF NOT EXISTS users (
	id VARCHAR NOT NULL, 
	email VARCHAR NOT NULL, 
	password_hash VARCHAR, 
	name VARCHAR, 
	company VARCHAR, 
	role VARCHAR, 
	user_type VARCHAR NOT NULL, 
	credits_remaining INTEGER NOT NULL, 
	plan VARCHAR NOT NULL, 
	last_active TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email);

CREATE TABLE IF NOT EXISTS subscriptions (
	id VARCHAR NOT NULL, 
	user_id VARCHAR, 
	stripe_customer_id VARCHAR, 
	stripe_subscription_id VARCHAR, 
	plan_type VARCHAR, 
	status VARCHAR, 
	report_limit INTEGER, 
	amount_cents INTEGER, 
	currency VARCHAR, 
	current_period_start TIMESTAMP WITHOUT TIME ZONE, 
	current_period_end TIMESTAMP WITHOUT TIME ZONE, 
	cancel_at_period_end BOOLEAN NOT NULL, 
	cancelled_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_subscriptions_stripe_customer_id ON subscriptions (stripe_customer_id);

CREATE INDEX IF NOT EXISTS ix_subscriptions_stripe_subscription_id ON subscriptions (stripe_subscription_id);

CREATE INDEX IF NOT EXISTS ix_subscriptions_user_id ON subscriptions (user_id);

CREATE TABLE IF NOT EXISTS reports (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	script_title VARCHAR NOT NULL, 
	script_file_path VARCHAR, 
	status VARCHAR NOT NULL, 
	report_type VARCHAR NOT NULL, 
	share_token VARCHAR, 
	report_data JSON, 
	pdf_url VARCHAR, 
	completed_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_reports_share_token ON reports (share_token);

CREATE INDEX IF NOT EXISTS ix_reports_user_id ON reports (user_id);

CREATE TABLE IF NOT EXISTS comparable_productions (
	id VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	year INTEGER, 
	budget_usd INTEGER, 
	primary_territory VARCHAR, 
	incentive_used VARCHAR, 
	genre JSON, 
	production_company VARCHAR, 
	director VARCHAR, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS territory_watchlist (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	territory VARCHAR NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_territory_watchlist_territory ON territory_watchlist (territory);

CREATE INDEX IF NOT EXISTS ix_territory_watchlist_user_id ON territory_watchlist (user_id);
"""


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import text

    for statement in _DDL.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(text(statement))


def downgrade() -> None:
    # Baseline of pre-existing application tables — never dropped.
    pass
