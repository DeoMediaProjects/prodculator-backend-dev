"""add_production_milestones_and_tasks

Revision ID: m1l2s3t4n5e6
Revises: u3v4w5x6y7z8
Create Date: 2026-03-31

Adds production_milestones and milestone_tasks tables for the
Production Timeline feature.
"""
from alembic import op
import sqlalchemy as sa

revision = "m1l2s3t4n5e6"
down_revision = "u3v4w5x6y7z8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_milestones",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("report_id", sa.String(), nullable=True, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="upcoming"),
        sa.Column("due_date", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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

    op.create_table(
        "milestone_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("milestone_id", sa.String(), nullable=False, index=True),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("territory", sa.String(100), nullable=True),
        sa.Column("deadline", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("milestone_tasks")
    op.drop_table("production_milestones")
