"""add_scrape_sources_and_runs

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-03-04 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6g7h8i9j0"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    if "scrape_sources" not in existing:
        op.create_table(
            "scrape_sources",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("resource_type", sa.Text(), nullable=False),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("label", sa.Text(), nullable=True),
            sa.Column("territory", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("use_bls_api", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("is_pdf", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("last_scraped_at", sa.DateTime(), nullable=True),
            sa.Column("last_status", sa.Text(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        # Add is_pdf column if the table already exists without it
        columns = {c["name"] for c in inspector.get_columns("scrape_sources")}
        if "is_pdf" not in columns:
            op.add_column(
                "scrape_sources",
                sa.Column("is_pdf", sa.Boolean(), nullable=False, server_default="0"),
            )

    if "scrape_runs" not in existing:
        op.create_table(
            "scrape_runs",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("triggered_by", sa.Text(), nullable=False),
            sa.Column("resource_type", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("pages_scraped", sa.Integer(), nullable=True),
            sa.Column("changes_detected", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("scrape_runs")
    op.drop_table("scrape_sources")
