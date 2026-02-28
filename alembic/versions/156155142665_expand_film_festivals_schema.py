"""expand_film_festivals_schema

Revision ID: 156155142665
Revises: c0f8fa0737aa
Create Date: 2026-02-28 17:43:30.010979
"""
from alembic import op
import sqlalchemy as sa


revision = '156155142665'
down_revision = 'c0f8fa0737aa'
branch_labels = None
depends_on = None

# Columns to add when the table already exists (old minimal schema)
_NEW_COLUMNS = [
    ("year", sa.Integer()),
    ("genres", sa.JSON()),
    ("budget_tiers", sa.JSON()),
    ("festival_dates", sa.Text()),
    ("premiere_requirement", sa.Text()),
    ("tier", sa.Text()),
    ("acceptance_rate", sa.Float()),
    ("data_source", sa.Text()),
    ("verified", sa.Boolean()),
    ("is_new", sa.Boolean()),
    ("deadlines", sa.JSON()),
    ("notable_alumni", sa.JSON()),
    ("average_budget_of_accepted_films", sa.Text()),
    ("notes", sa.Text()),
    ("last_verified_at", sa.DateTime()),
    ("created_at", sa.DateTime()),
    ("updated_at", sa.DateTime()),
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "film_festivals" not in inspector.get_table_names():
        # Table doesn't exist yet — create it with the full schema
        op.create_table(
            "film_festivals",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=True),
            sa.Column("location", sa.Text(), nullable=True),
            sa.Column("submission_deadline", sa.Text(), nullable=True),
            sa.Column("website_url", sa.Text(), nullable=True),
            sa.Column("filmfreeway_url", sa.Text(), nullable=True),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("genres", sa.JSON(), nullable=True),
            sa.Column("budget_tiers", sa.JSON(), nullable=True),
            sa.Column("festival_dates", sa.Text(), nullable=True),
            sa.Column("premiere_requirement", sa.Text(), nullable=True),
            sa.Column("tier", sa.Text(), nullable=True),
            sa.Column("acceptance_rate", sa.Float(), nullable=True),
            sa.Column("data_source", sa.Text(), nullable=True),
            sa.Column("verified", sa.Boolean(), nullable=True),
            sa.Column("is_new", sa.Boolean(), nullable=True),
            sa.Column("deadlines", sa.JSON(), nullable=True),
            sa.Column("notable_alumni", sa.JSON(), nullable=True),
            sa.Column("average_budget_of_accepted_films", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("last_verified_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        # Table exists with the old minimal schema — add only missing columns
        existing = {col["name"] for col in inspector.get_columns("film_festivals")}
        for col_name, col_type in _NEW_COLUMNS:
            if col_name not in existing:
                op.add_column("film_festivals", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    op.drop_table("film_festivals")
