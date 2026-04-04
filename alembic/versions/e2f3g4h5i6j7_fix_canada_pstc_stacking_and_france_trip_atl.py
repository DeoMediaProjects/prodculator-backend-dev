"""fix_canada_pstc_stacking_and_france_trip_atl

Revision ID: e2f3g4h5i6j7
Revises: d1e2f3g4h5i6
Create Date: 2026-03-28

ROOT CAUSE — Canada PSTC stackable_with references BC FIBC
----------------------------------------------------------
Canada Federal PSTC has stackable_with = '["BC Film Incentive BC Tax Credit (FIBC)",
"Ontario OPSTC","Quebec QPSTC"]'.

BC FIBC is restricted to Canadian-controlled productions
(nationality_requirements=['CA'], spv_eligible=False). Foreign productions filing
under Canada PSTC cannot stack BC FIBC — they must use BC PSTC (Production Services
Tax Credit), which is available to international/foreign productions.

Replacing "BC Film Incentive BC Tax Credit (FIBC)" with
"BC Production Services Tax Credit (PSTC)" in Canada PSTC's stackable_with corrects
the programme recommended for foreign-production BC stacking.

ROOT CAUSE — France TRIP ATL deduction
---------------------------------------
France TRIP (Tax Rebate for International Production) has rate_type = 'tax_credit'
and atl_exempt = NULL.  The validator's _compute_corrected_rebate() applies a
blanket 15% ATL deduction to any programme where rate_type is in
TAX_CREDIT_RATE_TYPES and atl_exempt is not True.

TRIP does not operate an ATL/BTL distinction — it applies to all qualifying French
local spend without carving out above-the-line costs.  The 15% deduction is a
Canadian-style assumption that does not apply to TRIP.

FIX: set atl_exempt = True on France TRIP.

SOURCES
-------
CNC TRIP official guidance — all qualifying French expenditure counts regardless of
ATL/BTL classification (no per-person cap; no ATL exclusion for foreign productions).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e2f3g4h5i6j7"
down_revision = "d1e2f3g4h5i6"
branch_labels = None
depends_on = None

_CANADA_PSTC = "Canada Federal PSTC (Production Services Tax Credit)"
_FRANCE_TRIP = "TRIP (Tax Rebate for International Production)"


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Canada PSTC — replace BC FIBC with BC PSTC in stackable_with ──────
    # BC FIBC is Canadian-controlled only; foreign productions using Canada PSTC
    # should stack with BC PSTC instead.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET stackable_with    = REPLACE(
                stackable_with,
                'BC Film Incentive BC Tax Credit (FIBC)',
                'BC Production Services Tax Credit (PSTC)'
            ),
            last_verified_at  = '2026-03-28'
        WHERE territory = 'Canada'
          AND program   = :program
          AND stackable_with LIKE '%BC Film Incentive BC Tax Credit (FIBC)%'
    """), {"program": _CANADA_PSTC})

    # ── 2. France TRIP — set atl_exempt = True ────────────────────────────────
    # TRIP has no ATL/BTL distinction — all qualifying French local spend counts.
    # atl_exempt = True prevents the validator from applying a 15% ATL deduction.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET atl_exempt       = TRUE,
            last_verified_at = '2026-03-28'
        WHERE territory = 'France'
          AND program   = :program
    """), {"program": _FRANCE_TRIP})


def downgrade() -> None:
    conn = op.get_bind()

    # Restore BC FIBC reference in Canada PSTC stackable_with
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET stackable_with = REPLACE(
                stackable_with,
                'BC Production Services Tax Credit (PSTC)',
                'BC Film Incentive BC Tax Credit (FIBC)'
            )
        WHERE territory = 'Canada'
          AND program   = :program
          AND stackable_with LIKE '%BC Production Services Tax Credit (PSTC)%'
    """), {"program": _CANADA_PSTC})

    # Restore France TRIP atl_exempt to NULL
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET atl_exempt = NULL
        WHERE territory = 'France'
          AND program   = :program
    """), {"program": _FRANCE_TRIP})
