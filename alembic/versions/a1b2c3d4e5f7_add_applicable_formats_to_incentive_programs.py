"""Add applicable_formats to incentive_programs; restrict IFTC to Feature Film.

TV series productions (TV Series, Limited Series, Mini-Series, etc.) must use
AVEC (34%), not IFTC (53%).  IFTC is only available to independent feature films
with a budget up to £15M / £20M cap — it is not available for TV productions.

Without this column the selection logic always picks the highest rate_gross,
so TV reports were incorrectly applying the 53 % IFTC rate instead of the
correct 34 % AVEC rate — a £2.4M difference on a £20M budget.

Revision ID: a1b2c3d4e5f7
Revises: z3d4e5f6g7h8
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f7"
down_revision = "7d17c136f7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the new column — NULL means "applicable to all formats"
    op.add_column(
        "incentive_programs",
        sa.Column("applicable_formats", sa.Text(), nullable=True),
    )

    # IFTC is only for independent feature films, not TV series or other formats
    op.execute(
        """
        UPDATE incentive_programs
        SET applicable_formats = '["Feature Film"]'
        WHERE territory = 'United Kingdom'
          AND program LIKE '%Independent Film Tax Credit%'
        """
    )


def downgrade() -> None:
    op.drop_column("incentive_programs", "applicable_formats")
