"""add_project_details_to_reports

Revision ID: h1i2j3k4l5m6
Revises: g0h1i2j3k4l5
Create Date: 2026-05-02

"""
from alembic import op
import sqlalchemy as sa

revision = "h1i2j3k4l5m6"
down_revision = "g0h1i2j3k4l5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("project_details", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "project_details")
