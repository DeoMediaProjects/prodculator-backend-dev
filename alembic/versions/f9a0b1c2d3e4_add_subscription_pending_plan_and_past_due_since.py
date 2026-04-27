"""Add pending_plan and past_due_since to subscriptions.

pending_plan tracks scheduled downgrades (set when /change is called with a
lower-tier price; cleared by the subscription.updated webhook on rollover).

past_due_since records when payment first failed so the dunning grace task can
downgrade to free after 7 days.

Revision ID: f9a0b1c2d3e4
Revises: e7f8a9b0c1d2
Create Date: 2026-04-26
"""

import sqlalchemy as sa
from alembic import op

revision = "f9a0b1c2d3e4"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("pending_plan", sa.String(), nullable=True))
    op.add_column("subscriptions", sa.Column("past_due_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("subscriptions", "past_due_since")
    op.drop_column("subscriptions", "pending_plan")
