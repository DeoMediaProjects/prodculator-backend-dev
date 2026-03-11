"""enrich_crew_costs_schema

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-03-10 12:10:00.000000

Add currency, source_url, budget_band, rate_notes, and last_verified_at
to crew_costs table so rates can be stored in local currency with proper
attribution and FX conversion at display time.
"""
from alembic import op
import sqlalchemy as sa


revision = "h2i3j4k5l6m7"
down_revision = "g1h2i3j4k5l6"
branch_labels = None
depends_on = None

_NEW_COLUMNS = [
    ("currency", sa.Text()),
    ("source_url", sa.Text()),
    ("budget_band", sa.Text()),
    ("rate_notes", sa.Text()),
    ("last_verified_at", sa.DateTime()),
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "crew_costs" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("crew_costs")}
    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing_cols:
            op.add_column("crew_costs", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "crew_costs" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("crew_costs")}
    for col_name, _ in _NEW_COLUMNS:
        if col_name in existing_cols:
            op.drop_column("crew_costs", col_name)
