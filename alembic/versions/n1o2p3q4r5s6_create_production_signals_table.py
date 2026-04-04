"""create_production_signals_table

Revision ID: n1o2p3q4r5s6
Revises: m1l2s3t4n5e6
Create Date: 2026-04-01

Adds the production_signals table used by:
GET /api/admin/production-signals
"""
from alembic import op
import sqlalchemy as sa


revision = "n1o2p3q4r5s6"
down_revision = "m1l2s3t4n5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "production_signals" not in inspector.get_table_names():
        op.create_table(
            "production_signals",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("script_id", sa.String(), nullable=True),
            sa.Column("territory", sa.String(length=100), nullable=True),
            sa.Column("state", sa.String(length=100), nullable=True),
            sa.Column("submission_date", sa.Date(), nullable=True),
            sa.Column("camera_equipment", sa.JSON(), nullable=True),
            sa.Column("crew_size", sa.Integer(), nullable=True),
            sa.Column("principal_cast", sa.Integer(), nullable=True),
            sa.Column("supporting_cast", sa.Integer(), nullable=True),
            sa.Column("background_extras", sa.Integer(), nullable=True),
            sa.Column("budget_range", sa.String(length=120), nullable=True),
            sa.Column("format", sa.String(length=120), nullable=True),
            sa.Column("genres", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("production_signals")}
    if "ix_production_signals_territory" not in indexes:
        op.create_index(
            "ix_production_signals_territory",
            "production_signals",
            ["territory"],
            unique=False,
        )
    if "ix_production_signals_submission_date" not in indexes:
        op.create_index(
            "ix_production_signals_submission_date",
            "production_signals",
            ["submission_date"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "production_signals" not in inspector.get_table_names():
        return

    indexes = {idx["name"] for idx in inspector.get_indexes("production_signals")}
    if "ix_production_signals_submission_date" in indexes:
        op.drop_index("ix_production_signals_submission_date", table_name="production_signals")
    if "ix_production_signals_territory" in indexes:
        op.drop_index("ix_production_signals_territory", table_name="production_signals")

    op.drop_table("production_signals")
