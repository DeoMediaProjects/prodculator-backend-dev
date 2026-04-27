"""Add producer plan — normalize legacy single data.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-04-19
"""

from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Normalize any remaining legacy 'single' plan references.
    # The previous migration (d6e7f8a9b0c1) already handled the main rename;
    # this migration is a safety net for any data created between migrations.
    op.execute("UPDATE users SET plan = 'professional' WHERE plan = 'single'")
    op.execute("UPDATE subscriptions SET plan_type = 'professional' WHERE plan_type = 'single'")
    # No schema changes — plan columns are already free-form strings.


def downgrade() -> None:
    pass
