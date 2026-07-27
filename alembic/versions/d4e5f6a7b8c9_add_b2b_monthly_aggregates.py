"""add b2b monthly aggregates

Monthly is the atomic unit of Business Intelligence reporting (Implementation
Plan section 3): quarterly composes from three stored months rather than
re-querying signals, and yearly unlocks once twelve stored months exist.

`facts` stores RAW, UNSUPPRESSED counters. Privacy floors are applied at
compose/render time, never at storage time -- otherwise a segment appearing
4x in each of three months (12 total, above the floor of 5) would be
permanently invisible at quarterly level.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "b2b_monthly_aggregates" not in set(inspector.get_table_names()):
        op.create_table(
            "b2b_monthly_aggregates",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("product_type", sa.String(), nullable=False),
            # First day of the month this aggregate covers.
            sa.Column("period_month", sa.Date(), nullable=False),
            sa.Column("signal_count", sa.Integer(), nullable=False, server_default="0"),
            # RAW unsuppressed counters; floors applied downstream.
            sa.Column("facts", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("b2b_monthly_aggregates")}
    # Idempotent recompute: re-running a month upserts rather than duplicating.
    if "uq_b2b_monthly_aggregates_product_month" not in indexes:
        op.create_index(
            "uq_b2b_monthly_aggregates_product_month",
            "b2b_monthly_aggregates",
            ["product_type", "period_month"],
            unique=True,
        )
    if "ix_b2b_monthly_aggregates_period_month" not in indexes:
        op.create_index(
            "ix_b2b_monthly_aggregates_period_month",
            "b2b_monthly_aggregates",
            ["period_month"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "b2b_monthly_aggregates" in set(inspector.get_table_names()):
        for idx in inspector.get_indexes("b2b_monthly_aggregates"):
            op.drop_index(idx["name"], table_name="b2b_monthly_aggregates")
        op.drop_table("b2b_monthly_aggregates")
