"""create_grant_opportunities_table

Revision ID: 0de65d194977
Revises: 156155142665
Create Date: 2026-03-01 11:29:43.568855
"""
from alembic import op
import sqlalchemy as sa


revision = "0de65d194977"
down_revision = "156155142665"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "grant_opportunities" in inspector.get_table_names():
        return

    op.create_table(
        "grant_opportunities",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("territory", sa.Text(), nullable=True),
        sa.Column("funding_body", sa.Text(), nullable=True),
        sa.Column("max_amount", sa.Text(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("application_opens", sa.Text(), nullable=True),
        sa.Column("application_deadline", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("days_until_deadline", sa.Integer(), nullable=True),
        sa.Column("eligibility", sa.JSON(), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("data_source", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=True),
        sa.Column("is_new", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("grant_opportunities")
