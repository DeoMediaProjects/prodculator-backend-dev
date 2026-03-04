"""add_sync_settings_and_pending_changes

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-02 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6g7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    if "sync_settings" not in existing:
        op.create_table(
            "sync_settings",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("resource_type", sa.Text(), nullable=False),
            sa.Column("schedule", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("last_sync_at", sa.DateTime(), nullable=True),
            sa.Column("next_scheduled", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("resource_type"),
        )

    if "pending_changes" not in existing:
        op.create_table(
            "pending_changes",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("resource_type", sa.Text(), nullable=False),
            sa.Column("resource_id", sa.Text(), nullable=True),
            sa.Column("territory", sa.Text(), nullable=False),
            sa.Column("field", sa.Text(), nullable=False),
            sa.Column("current_value", sa.Text(), nullable=True),
            sa.Column("detected_value", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Text(), nullable=False),
            sa.Column("source", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_by", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("pending_changes")
    op.drop_table("sync_settings")
