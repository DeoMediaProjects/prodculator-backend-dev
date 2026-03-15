"""migrate_crew_costs_schema

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-03-14 14:00:00.000000

Add new columns to crew_costs for government-stats-sourced rate data:
country (ISO 2-letter), region, role_category, department, union_rate_cents,
non_union_rate_cents, rate_currency, working_day_hours, fringe_rate_pct,
fringe_description, source_name, source_type, confidence_score,
effective_from, notes.

Backfills new columns from existing data where possible.
Old columns (territory, day_rate, week_rate, category, etc.) are kept
temporarily for backward compatibility.
"""
from alembic import op
import sqlalchemy as sa


revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None

_NEW_COLUMNS = [
    ("country", sa.Text()),
    ("region", sa.Text()),
    ("role_category", sa.Text()),
    ("department", sa.Text()),
    ("union_rate_cents", sa.Integer()),
    ("non_union_rate_cents", sa.Integer()),
    ("rate_currency", sa.Text()),
    ("working_day_hours", sa.Float()),
    ("fringe_rate_pct", sa.Float()),
    ("fringe_description", sa.Text()),
    ("source_name", sa.Text()),
    ("source_type", sa.Text()),
    ("confidence_score", sa.Integer()),
    ("effective_from", sa.Text()),
    ("notes", sa.Text()),
]

# Mapping from existing territory full names to ISO 2-letter codes
_TERRITORY_TO_ISO = {
    "United States": "US",
    "Canada": "CA",
    "United Kingdom": "GB",
    "Ireland": "IE",
    "France": "FR",
    "Germany": "DE",
    "Spain": "ES",
    "Italy": "IT",
    "Australia": "AU",
    "New Zealand": "NZ",
    "South Africa": "ZA",
    "Czech Republic": "CZ",
    "Hungary": "HU",
    "Iceland": "IS",
    "Malta": "MT",
    "Nigeria": "NG",
}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "crew_costs" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("crew_costs")}
    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing_cols:
            op.add_column("crew_costs", sa.Column(col_name, col_type, nullable=True))

    # Backfill country from territory
    for full_name, iso_code in _TERRITORY_TO_ISO.items():
        conn.execute(
            sa.text(
                "UPDATE crew_costs SET country = :iso "
                "WHERE territory = :name AND (country IS NULL OR country = '')"
            ),
            {"iso": iso_code, "name": full_name},
        )

    # Backfill union_rate_cents from day_rate (float dollars → integer cents)
    conn.execute(
        sa.text(
            "UPDATE crew_costs SET union_rate_cents = CAST(ROUND(day_rate * 100) AS INTEGER) "
            "WHERE day_rate IS NOT NULL AND union_rate_cents IS NULL"
        )
    )

    # Backfill non_union_rate_cents from week_rate (float dollars → integer cents)
    conn.execute(
        sa.text(
            "UPDATE crew_costs SET non_union_rate_cents = CAST(ROUND(week_rate * 100) AS INTEGER) "
            "WHERE week_rate IS NOT NULL AND non_union_rate_cents IS NULL"
        )
    )

    # Backfill rate_currency from currency
    conn.execute(
        sa.text(
            "UPDATE crew_costs SET rate_currency = currency "
            "WHERE currency IS NOT NULL AND (rate_currency IS NULL OR rate_currency = '')"
        )
    )

    # Backfill role_category from category
    conn.execute(
        sa.text(
            "UPDATE crew_costs SET role_category = category "
            "WHERE category IS NOT NULL AND (role_category IS NULL OR role_category = '')"
        )
    )

    # Backfill source_name from source
    conn.execute(
        sa.text(
            "UPDATE crew_costs SET source_name = source "
            "WHERE source IS NOT NULL AND (source_name IS NULL OR source_name = '')"
        )
    )

    # Default department to 'day' for existing rows
    conn.execute(
        sa.text(
            "UPDATE crew_costs SET department = 'day' "
            "WHERE department IS NULL"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "crew_costs" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("crew_costs")}
    for col_name, _ in _NEW_COLUMNS:
        if col_name in existing_cols:
            op.drop_column("crew_costs", col_name)
