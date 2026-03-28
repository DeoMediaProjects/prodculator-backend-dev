"""fix_iftc_atl_and_stacking_text

Revision ID: t1u2v3w4x5y6
Revises: s0t1u2v3w4x5
Create Date: 2026-03-25

Fixes three confirmed data errors:

1. IFTC missing atl_exempt flag
   The UK Independent Film Tax Credit (IFTC) applies to ALL qualifying UK
   expenditure with no ATL/BTL distinction — identical treatment to AVEC.
   Without atl_exempt=true, the rebate calculator applies a spurious 15%
   ATL deduction, understating qualifying spend and producing a contradictory
   report (ATL deduction shown but methodology says no ATL split applies).

2. IFTC cap_basis stored but unused — set to 'core_costs'
   The £23.5M budget threshold for IFTC eligibility is measured against
   total core expenditure (per BFI guidance), not total budget. This is
   already set in the DB but the validator ignores it. This migration
   is a no-op for cap_basis but ensures the field value is correct.

3. Canada Federal PSTC stackable_with text references BC FIBC at 36%
   The FIBC basic rate was corrected to 35% in migration p7q8r9s0t1u2
   but the stackable_with text on the Federal PSTC still says 36%.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t1u2v3w4x5y6"
down_revision = "s0t1u2v3w4x5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1 — Set atl_exempt=true on IFTC (same treatment as AVEC)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET atl_exempt       = true,
            last_verified_at = '2026-03-25'
        WHERE territory = 'United Kingdom'
          AND program = 'UK Independent Film Tax Credit (IFTC)'
          AND (status = 'active' OR status IS NULL)
    """))

    # 2 — Fix stackable_with text on Canada Federal PSTC: 36% → 35%
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET stackable_with = REPLACE(stackable_with, 'BC FIBC (36%)', 'BC FIBC (35%)'),
            last_verified_at = '2026-03-25'
        WHERE territory = 'Canada'
          AND program LIKE '%PSTC%'
          AND stackable_with LIKE '%36%%'
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET atl_exempt = NULL
        WHERE territory = 'United Kingdom'
          AND program = 'UK Independent Film Tax Credit (IFTC)'
    """))

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET stackable_with = REPLACE(stackable_with, 'BC FIBC (35%)', 'BC FIBC (36%)')
        WHERE territory = 'Canada'
          AND program LIKE '%PSTC%'
          AND stackable_with LIKE '%35%%'
    """))
