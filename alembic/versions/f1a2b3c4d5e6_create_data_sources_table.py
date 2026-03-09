"""create_data_sources_table

Revision ID: f1a2b3c4d5e6
Revises: a5dcb3d855ee
Create Date: 2026-03-07 14:00:00.000000
"""
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "a5dcb3d855ee"
branch_labels = None
depends_on = None


def upgrade() -> None:
    data_sources = op.create_table(
        "data_sources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("endpoint", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("credential_mode", sa.String(), nullable=False, server_default="backend_env"),
        sa.Column("is_implemented", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_result", sa.String(), nullable=True),
        sa.Column("last_test_message", sa.String(), nullable=True),
        sa.Column("sync_schedule", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_data_sources_slug", "data_sources", ["slug"], unique=True)

    op.bulk_insert(
        data_sources,
        [
            {
                "id": str(uuid4()),
                "name": "Anthropic Claude",
                "slug": "anthropic",
                "category": "ai",
                "description": "Server-side script analysis and data extraction",
                "endpoint": "/api/scripts/analyze",
                "enabled": True,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": True,
                "sync_schedule": "on-demand",
            },
            {
                "id": str(uuid4()),
                "name": "PostgreSQL Database",
                "slug": "database",
                "category": "database",
                "description": "Primary data store for all application data",
                "endpoint": "/api/health",
                "enabled": True,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": True,
                "sync_schedule": None,
            },
            {
                "id": str(uuid4()),
                "name": "TMDB API",
                "slug": "tmdb",
                "category": "data",
                "description": "Film metadata and comparable production data",
                "endpoint": "/api/admin/comparables/sync-tmdb",
                "enabled": True,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": True,
                "sync_schedule": "on-demand",
            },
            {
                "id": str(uuid4()),
                "name": "Bureau of Labor Statistics",
                "slug": "bls",
                "category": "data",
                "description": "Crew wage and labor cost data for production budgeting",
                "endpoint": "/api/admin/crew-costs",
                "enabled": True,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": True,
                "sync_schedule": "on-demand",
            },
            {
                "id": str(uuid4()),
                "name": "Stripe Payments",
                "slug": "stripe",
                "category": "payments",
                "description": "Payment processing for subscriptions and one-time purchases",
                "endpoint": "/api/payments",
                "enabled": True,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": True,
                "sync_schedule": "on-demand",
            },
            {
                "id": str(uuid4()),
                "name": "SendGrid Email",
                "slug": "sendgrid",
                "category": "email",
                "description": "Transactional email delivery for notifications and alerts",
                "endpoint": "/api/admin/email",
                "enabled": True,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": True,
                "sync_schedule": "on-demand",
            },
            {
                "id": str(uuid4()),
                "name": "Redis Cache",
                "slug": "redis",
                "category": "cache",
                "description": "Token blocklist and session caching",
                "endpoint": "/api/health",
                "enabled": True,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": True,
                "sync_schedule": None,
            },
            {
                "id": str(uuid4()),
                "name": "Google Maps Platform",
                "slug": "google_maps",
                "category": "data",
                "description": "Geocoding and location data for production locations",
                "endpoint": None,
                "enabled": False,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": False,
                "sync_schedule": None,
            },
            {
                "id": str(uuid4()),
                "name": "ExchangeRate API",
                "slug": "exchange_rate",
                "category": "data",
                "description": "Currency conversion for international production budgets",
                "endpoint": None,
                "enabled": False,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": False,
                "sync_schedule": None,
            },
            {
                "id": str(uuid4()),
                "name": "Grantify",
                "slug": "grantify",
                "category": "data",
                "description": "Grant opportunity discovery and affiliate referrals",
                "endpoint": None,
                "enabled": False,
                "status": "unknown",
                "credential_mode": "backend_env",
                "is_implemented": False,
                "sync_schedule": None,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_data_sources_slug", table_name="data_sources")
    op.drop_table("data_sources")
