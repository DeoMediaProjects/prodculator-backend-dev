"""fix_iftc_rebate_cap

Revision ID: a8b9c0d1e2f3
Revises: z7a8b9c0d1e2
Create Date: 2026-03-27

The IFTC (Independent Film Tax Credit) was missing a rebate_cap_amount, which
caused the validator to apply 53% to the full qualifying spend of any budget up
to the £23.5M eligibility threshold.

Example: $25M budget (~£18.7M) → qualifying spend £14.95M → 53% = £7.92M gross
rebate.  This is wrong.

The correct maximum gross credit is £6.36M, calculated as:
  £15M (max core expenditure for enhanced rate) × 80% (qualifying spend %) × 53%
  = £12M qualifying spend × 53% = £6,360,000

Source: HMRC / BFI — "The maximum credit value is £6.36M (£15M × 80% × 53%)"
  https://www.gov.uk/guidance/audio-visual-expenditure-credit

Note: cap_amount (£23.5M) remains unchanged — it is the budget eligibility
threshold, not the rebate ceiling.  rebate_cap_amount is the ceiling on the
computed rebate value, enforced in ReportValidator._compute_corrected_rebate()
step 5.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a8b9c0d1e2f3"
down_revision = "z7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_amount   = 6360000.0,
            rebate_cap_currency = 'GBP',
            last_verified_at    = '2026-03-27'
        WHERE territory = 'United Kingdom'
          AND program   = 'UK Independent Film Tax Credit (IFTC)'
          AND (status = 'active' OR status IS NULL)
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_amount   = NULL,
            rebate_cap_currency = NULL
        WHERE territory = 'United Kingdom'
          AND program   = 'UK Independent Film Tax Credit (IFTC)'
          AND (status = 'active' OR status IS NULL)
    """))
