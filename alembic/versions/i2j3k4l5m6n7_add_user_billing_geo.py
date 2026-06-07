"""add_user_billing_geo

Adds billing country / state to users so the admin Business Metrics dashboard
can compute geographic distribution. Populated going-forward from the Stripe
checkout session (see WebhookHandler) and backfilled via
scripts/backfill_user_geo.py.

Revision ID: i2j3k4l5m6n7
Revises: h1i2j3k4l5m6
Create Date: 2026-06-07

"""
from alembic import op
import sqlalchemy as sa

revision = "i2j3k4l5m6n7"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("country", sa.String(), nullable=True))
    op.add_column("users", sa.Column("state", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "state")
    op.drop_column("users", "country")
