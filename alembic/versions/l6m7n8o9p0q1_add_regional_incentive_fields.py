"""add_regional_incentive_fields

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-03-13 10:00:00.000000

Adds scope, parent_territory, stacking_group, stackable_with columns to
incentive_programs to support regional incentive stacking (e.g. Creative
Scotland stacks on UK AVEC).  Backfills existing rows with scope='national'.
"""

from alembic import op
import sqlalchemy as sa

revision = "l6m7n8o9p0q1"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}

    if "scope" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("scope", sa.Text(), nullable=True),
        )

    if "parent_territory" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("parent_territory", sa.Text(), nullable=True),
        )

    if "stacking_group" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("stacking_group", sa.Text(), nullable=True),
        )

    if "stackable_with" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("stackable_with", sa.Text(), nullable=True),
        )

    # Backfill existing rows: all current incentives are national-level
    conn.execute(
        sa.text(
            "UPDATE incentive_programs SET scope = 'national' WHERE scope IS NULL"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}

    for col in ("stackable_with", "stacking_group", "parent_territory", "scope"):
        if col in existing_cols:
            op.drop_column("incentive_programs", col)
