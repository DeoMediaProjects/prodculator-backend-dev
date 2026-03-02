"""add_request_metadata_to_reports

Revision ID: a1b2c3d4e5f6
Revises: 0de65d194977
Create Date: 2026-03-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "0de65d194977"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("request_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "request_metadata")
