"""add_is_pdf_to_scrape_sources

Revision ID: c627bbdae874
Revises: e5f6g7h8i9j0
Create Date: 2026-03-06 18:17:57.396412
"""
from alembic import op
import sqlalchemy as sa


revision = "c627bbdae874"
down_revision = "e5f6g7h8i9j0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {c["name"] for c in inspector.get_columns("scrape_sources")}
    if "is_pdf" not in columns:
        op.add_column(
            "scrape_sources",
            sa.Column("is_pdf", sa.Boolean(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("scrape_sources", "is_pdf")
