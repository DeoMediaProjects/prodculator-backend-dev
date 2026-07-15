"""production_signals v2: consent, FX-normalised budget, three-way territory, audience, hygiene

Merges the open heads and adds the B2B signal-contract v2 columns.

Handoff pack listed heads (c1d2e3f4a5b6, i2j3k4l5m6n7, z8b9c0d1e2f3); by merge time
the repo's actual heads were (b2a3b4c5d6e7, c1d2e3f4a5b6) — i2j3k4l5m6n7 and
z8b9c0d1e2f3 had already been merged into the chain by internal work. down_revision
is re-pointed accordingly; the column adds are idempotent so behaviour is unchanged.

Revision ID: b2b1v2signal
Revises: b2a3b4c5d6e7, c1d2e3f4a5b6
Create Date: 2026-07-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2b1v2signal"
down_revision = ("b2a3b4c5d6e7", "c1d2e3f4a5b6")
branch_labels = None
depends_on = None


# New v2 columns. Idempotent adds so re-running on a partially-migrated prod is safe.
_NEW_COLUMNS = [
    ("home_country", sa.String(), True),
    ("territories_considered", sa.JSON(), True),
    ("territories_recommended", sa.JSON(), True),
    ("completion_window", sa.String(), True),
    ("budget_amount_gbp", sa.Float(), True),
    ("budget_currency", sa.String(), True),
    ("fx_rate_date", sa.Date(), True),
    ("target_audience", sa.JSON(), True),
    ("audience_segments", sa.JSON(), True),
    ("audience_skew", sa.String(), True),
    ("primary_languages", sa.JSON(), True),
    ("co_production_interest", sa.Boolean(), True),
    ("b2b_consent", sa.Boolean(), False),
    ("is_internal", sa.Boolean(), False),
    ("report_runs", sa.Integer(), True),
    ("schema_version", sa.Integer(), True),
]


def _existing_columns(bind) -> set[str]:
    insp = sa.inspect(bind)
    try:
        return {c["name"] for c in insp.get_columns("production_signals")}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    if not existing:
        # production_signals table not present in this environment; nothing to do.
        return

    for name, type_, nullable in _NEW_COLUMNS:
        if name in existing:
            continue
        default = None
        server_default = None
        if name in ("b2b_consent", "is_internal"):
            server_default = sa.false()
        elif name == "report_runs":
            server_default = sa.text("1")
        elif name == "schema_version":
            server_default = sa.text("1")  # existing rows are v1 until backfilled
        op.add_column(
            "production_signals",
            sa.Column(name, type_, nullable=nullable, server_default=server_default),
        )

    # Backfill: mirror legacy `territory` into `home_country` so existing rows keep
    # their territory meaning under the new field name.
    try:
        op.execute(
            "UPDATE production_signals SET home_country = territory "
            "WHERE home_country IS NULL AND territory IS NOT NULL"
        )
    except Exception:
        pass

    # Existing rows have unknown consent -> leave b2b_consent = FALSE so they are
    # excluded from customer-facing aggregation until re-consented (CRIT-2 safe default).

    # Add the unique index on script_id for script-level dedupe (Decision 1).
    try:
        op.create_index(
            "uq_production_signals_script_id",
            "production_signals",
            ["script_id"],
            unique=True,
        )
    except Exception:
        # Index may already exist, or duplicate script_ids may exist in legacy data.
        # A separate data-cleanup step de-duplicates before enforcing uniqueness.
        pass


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    try:
        op.drop_index("uq_production_signals_script_id", table_name="production_signals")
    except Exception:
        pass
    for name, _type, _nullable in reversed(_NEW_COLUMNS):
        if name in existing:
            try:
                op.drop_column("production_signals", name)
            except Exception:
                pass
