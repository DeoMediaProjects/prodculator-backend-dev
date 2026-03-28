"""fix_avec_net_rate_and_vfx_supplementary

Revision ID: j1k2l3m4n5o6
Revises: i0j1k2l3m4n5
Create Date: 2026-03-21

Fixes two systematic errors discovered in live report output:

1. AVEC rate_net corrected from 34.0 → 25.5
   AVEC (Audio-Visual Expenditure Credit) receipts are taxable income subject
   to UK corporation tax (25%). Net benefit = 34% × 0.75 = 25.5%.
   The prior value of 34.0 inflated all UK rebate estimates.

2. Add is_supplementary column + mark VFX Expenditure Credit as supplementary
   The VFX Expenditure Credit (39%) applies only to qualifying VFX costs. It is
   a supplementary credit that stacks with AVEC — it is NOT a replacement for
   AVEC. Without this flag, when IFTC is capped out (budget > £23.5M), the
   cap-switching logic selected VFX Credit (39% > AVEC 34%) as the primary
   incentive and applied it to the full production budget — producing an
   overstated rebate figure and a mislabelled report section.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "j1k2l3m4n5o6"
down_revision = "i0j1k2l3m4n5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1 — Add is_supplementary column (default FALSE — no existing rows affected)
    op.add_column(
        "incentive_programs",
        sa.Column("is_supplementary", sa.Boolean(), nullable=True, server_default="false"),
    )

    # 2 — Fix AVEC rate_net: 34.0 → 25.5 (34% gross × 0.75 after 25% UK corp tax)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_net = 25.5,
            last_verified_at = '2026-03-21'
        WHERE territory = 'United Kingdom'
          AND program = 'Audio-Visual Expenditure Credit (AVEC)'
          AND status = 'active'
    """))

    # 3 — Mark VFX Expenditure Credit as supplementary
    # This credit applies only to qualifying UK VFX expenditure — it supplements
    # AVEC rather than replacing it. Setting is_supplementary prevents it from
    # being selected as the primary territory incentive.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET is_supplementary = true,
            last_verified_at = '2026-03-21'
        WHERE territory = 'United Kingdom'
          AND program = 'VFX Expenditure Credit (Uplift)'
          AND status = 'active'
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # Restore AVEC rate_net to previous (incorrect) value
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_net = 34.0
        WHERE territory = 'United Kingdom'
          AND program = 'Audio-Visual Expenditure Credit (AVEC)'
          AND status = 'active'
    """))

    # Clear is_supplementary flag on VFX credit
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET is_supplementary = false
        WHERE territory = 'United Kingdom'
          AND program = 'VFX Expenditure Credit (Uplift)'
    """))

    # Drop column
    op.drop_column("incentive_programs", "is_supplementary")
