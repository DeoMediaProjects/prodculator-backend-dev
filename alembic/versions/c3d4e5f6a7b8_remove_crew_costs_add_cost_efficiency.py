"""Remove crew-cost day rates; add curated cost-efficiency columns.

Owner-approved removal (2026-07-17): crew COST day rates leave the platform
entirely (dev handoff §1). Crew DEPTH quality tiers are untouched.

Cost Efficiency remains a scoring dimension but is no longer derived from
day-rate arithmetic. Per the canonical territory_scorecard_composite.json,
no sourced cost data currently exists — every territory's score is NULL and
consumers fall back to a neutral 50 ("no fabricated numbers" rule). The new
columns let sourced scores be added through the admin later.

Revision ID: c3d4e5f6a7b8
Revises: b2b1v2signal
Create Date: 2026-07-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2b1v2signal"
branch_labels = None
depends_on = None

_NEUTRAL_NOTE = (
    "No sourced cost data — neutral 50 default per the no-fabricated-numbers "
    "rule (see territory_scorecard_composite.json). Set a score here only "
    "with a verifiable source."
)


def _existing_columns(bind, table: str) -> set[str]:
    insp = sa.inspect(bind)
    try:
        return {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()

    profile_cols = _existing_columns(bind, "territory_profiles")
    if profile_cols:
        if "cost_efficiency_score" not in profile_cols:
            op.add_column(
                "territory_profiles",
                sa.Column("cost_efficiency_score", sa.Integer(), nullable=True),
            )
        if "cost_efficiency_source" not in profile_cols:
            op.add_column(
                "territory_profiles",
                sa.Column("cost_efficiency_source", sa.String(), nullable=True),
            )
            op.execute(
                sa.text(
                    "UPDATE territory_profiles SET cost_efficiency_source = :note "
                    "WHERE cost_efficiency_source IS NULL"
                ).bindparams(note=_NEUTRAL_NOTE)
            )

    # Owner-approved: crew day-rate records leave the platform.
    insp = sa.inspect(bind)
    if "crew_costs" in insp.get_table_names():
        op.drop_table("crew_costs")

    # Remove the crew-cost scrape sources seeded at boot (the scraper no longer
    # registers a crew_costs resource type).
    if "scrape_sources" in insp.get_table_names():
        op.execute("DELETE FROM scrape_sources WHERE resource_type = 'crew_costs'")


def downgrade() -> None:
    bind = op.get_bind()
    profile_cols = _existing_columns(bind, "territory_profiles")
    for col in ("cost_efficiency_source", "cost_efficiency_score"):
        if col in profile_cols:
            op.drop_column("territory_profiles", col)
    # crew_costs data is not restorable; recreate empty shell is intentionally omitted.
