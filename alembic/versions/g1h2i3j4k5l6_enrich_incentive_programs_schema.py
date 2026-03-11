"""enrich_incentive_programs_schema

Revision ID: g1h2i3j4k5l6
Revises: b7e2f1a9c3d4
Create Date: 2026-03-10 12:00:00.000000

Add structured fields for rate tiers, payment timelines, eligibility rules,
caps, currency, expiry dates, warnings, and source attribution to eliminate
AI hallucination of territory-specific factual data.
"""
from alembic import op
import sqlalchemy as sa


revision = "g1h2i3j4k5l6"
down_revision = "b7e2f1a9c3d4"
branch_labels = None
depends_on = None

_NEW_COLUMNS = [
    ("rate_gross", sa.Float()),
    ("rate_net", sa.Float()),
    ("rate_type", sa.Text()),
    ("rate_tier_json", sa.Text()),
    ("cap_amount", sa.Float()),
    ("cap_currency", sa.Text()),
    ("cap_per_person", sa.Float()),
    ("cap_per_person_currency", sa.Text()),
    ("qualifying_spend_min", sa.Float()),
    ("qualifying_spend_cap_pct", sa.Float()),
    ("qualifying_spend_currency", sa.Text()),
    ("payment_timeline_days_min", sa.Integer()),
    ("payment_timeline_days_max", sa.Integer()),
    ("payment_timeline_notes", sa.Text()),
    ("eligibility_rules_json", sa.Text()),
    ("expiry_date", sa.Date()),
    ("currency", sa.Text()),
    ("warnings_json", sa.Text()),
    ("last_verified_at", sa.DateTime()),
    ("source_name", sa.Text()),
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("incentive_programs")}
    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing_cols:
            op.add_column("incentive_programs", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("incentive_programs")}
    for col_name, _ in _NEW_COLUMNS:
        if col_name in existing_cols:
            op.drop_column("incentive_programs", col_name)
