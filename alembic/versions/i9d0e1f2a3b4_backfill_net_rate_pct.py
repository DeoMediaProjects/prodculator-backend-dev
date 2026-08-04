"""backfill_net_rate_pct

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-08-04

PROD-FIX-007, follow-on. Found by the new refresh-completeness guard test
rather than by report review — net_rate_pct is a second column the v4 refresh
(ab2c3d4e5f61) silently reset, alongside is_supplementary.

net_rate_pct is the atomic investor-facing net rate added by k3l4m5n6o7p8.
ReportBuilder reads it directly into the `netRatePct` skeleton key, bypassing
the string-trimming applied to free-text fields, precisely so the AI narrative
always sees an exact number. Since the refresh it has been NULL on every row,
so that key has simply been absent from every report skeleton.

The value is the numeric net rate, which the refresh already parses into
rate_net from the same v4 source field — so this backfills from rate_net rather
than re-deriving it, and the refresh now writes both.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "i9d0e1f2a3b4"
down_revision = "h8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET net_rate_pct = rate_net
        WHERE net_rate_pct IS NULL
          AND rate_net IS NOT NULL
    """))

    # A zero-rate or status-gated programme legitimately has no net rate, so
    # assert on the programmes that do carry one rather than on every row.
    remaining = conn.execute(sa.text("""
        SELECT COUNT(*) FROM incentive_programs
        WHERE rate_net IS NOT NULL AND net_rate_pct IS NULL
    """)).scalar()
    assert not remaining, (
        f"{remaining} row(s) still have rate_net but no net_rate_pct"
    )


def downgrade() -> None:
    # No-op: the prior state was NULL through a data-loss bug, not by intent,
    # and restoring NULLs would reintroduce it.
    pass
