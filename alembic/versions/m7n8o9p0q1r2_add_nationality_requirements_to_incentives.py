"""add_nationality_requirements_to_incentives

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-03-13 10:05:00.000000

Adds nationality_requirements, co_production_eligible, co_production_treaties,
and spv_eligible columns to incentive_programs so the report can reason about
producer eligibility per territory.
"""

from alembic import op
import sqlalchemy as sa

revision = "m7n8o9p0q1r2"
down_revision = "l6m7n8o9p0q1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}

    if "nationality_requirements" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("nationality_requirements", sa.Text(), nullable=True),
        )

    if "co_production_eligible" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("co_production_eligible", sa.Boolean(), nullable=True),
        )

    if "co_production_treaties" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("co_production_treaties", sa.Text(), nullable=True),
        )

    if "spv_eligible" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("spv_eligible", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}

    for col in (
        "spv_eligible",
        "co_production_treaties",
        "co_production_eligible",
        "nationality_requirements",
    ):
        if col in existing_cols:
            op.drop_column("incentive_programs", col)
