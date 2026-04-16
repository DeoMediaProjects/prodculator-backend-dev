"""normalize_single_to_professional

Revision ID: d6e7f8a9b0c1
Revises: n1o2p3q4r5s6
Create Date: 2026-04-16

Rename legacy plan name 'single' to 'professional' in users and subscriptions.
No schema change — both columns are already str.
"""
from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "n1o2p3q4r5s6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET plan = 'professional' WHERE plan = 'single'")
    op.execute("UPDATE subscriptions SET plan_type = 'professional' WHERE plan_type = 'single'")


def downgrade() -> None:
    op.execute("UPDATE users SET plan = 'single' WHERE plan = 'professional'")
    op.execute("UPDATE subscriptions SET plan_type = 'single' WHERE plan_type = 'professional'")
