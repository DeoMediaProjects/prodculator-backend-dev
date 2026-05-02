"""Add stripe_schedule_id to subscriptions.

Stores the Stripe Subscription Schedule ID created when a downgrade is deferred
to the end of the current billing period. Using a Subscription Schedule keeps
the subscription item on the current (paid) plan until period end, so the
customer.subscription.updated webhook fires at the natural rollover rather
than immediately — preventing premature access revocation.

Cleared when the schedule completes (rollover applies the new plan) or is
released (scheduled downgrade cancelled via the API).

Revision ID: g0h1i2j3k4l5
Revises: f9a0b1c2d3e4
Create Date: 2026-05-01
"""

import sqlalchemy as sa
from alembic import op

revision = "g0h1i2j3k4l5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("stripe_schedule_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "stripe_schedule_id")
