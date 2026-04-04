"""fix_bc_fibc_rate_text_sync

Revision ID: d1e2f3g4h5i6
Revises: c0d1e2f3g4h5
Create Date: 2026-03-27

ROOT CAUSE
----------
Migration p7q8r9s0t1u2 correctly set BC FIBC rate_gross = 35.0 and rate_net = 35.0
(per Creative BC's published 35% basic credit rate). However, it only updated the
numeric columns — the rate TEXT field was not touched and still reads
'40% of qualified BC labour' (set by d5e6f7g8h9i0).

This split state ('rate' text says 40%, numeric rate_gross = 35) caused every
subsequent fix (b9c0d1e2f3g4, c0d1e2f3g4h5) to fail silently: both used
AND rate_gross = 40.0 as a guard, which no longer matched since p7q8r9s0t1u2
had already set rate_gross = 35.0.

The ReportBuilder indexes stacking rates by the 'rate' text field. The AI prompt
is instructed to use rates from this stacking data, so it cited "40% of qualified
BC labour" despite the numeric value being 35%.

FIX
---
Set rate = '35% of qualified BC labour' unconditionally (territory + program only).
No rate_gross guard — the numeric values are already correct at 35.0.

SOURCE
------
Creative BC — FIBC programme page (creativebc.com)
"35% basic credit on qualified BC labour expenditure"
(p7q8r9s0t1u2 already verified and set rate_gross = 35.0 from this source)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1e2f3g4h5i6"
down_revision = "c0d1e2f3g4h5"
branch_labels = None
depends_on = None

_TERRITORY = "British Columbia"
_PROGRAM = "BC Film Incentive BC Tax Credit (FIBC)"


def upgrade() -> None:
    conn = op.get_bind()

    # Sync rate text to match the numeric rate_gross = 35.0 already in the DB.
    # No rate_gross guard — all previous fixes that used AND rate_gross = 40.0
    # failed silently because p7q8r9s0t1u2 had already set rate_gross = 35.0.
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate             = '35% of qualified BC labour', "
            "    rate_gross       = 35.0, "
            "    rate_net         = 35.0, "
            "    last_verified_at = '2026-03-27' "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {"territory": _TERRITORY, "program": _PROGRAM},
    )

    # Also fix Canada PSTC stackable_with / warnings_json references that
    # still mention "BC FIBC (40%)" — p7q8r9s0t1u2 and t1u2v3w4x5y6 fixed
    # "36%" → "35%" but a later migration (d5e6f7g8h9i0) may have introduced
    # a fresh "40%" reference in some fields.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(warnings_json, 'BC FIBC (40%)', 'BC FIBC (35%)')
        WHERE territory = 'Canada'
          AND program LIKE '%PSTC%'
          AND warnings_json LIKE '%BC FIBC (40%)%'
    """))

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET stackable_with = REPLACE(stackable_with, 'BC FIBC (40%)', 'BC FIBC (35%)')
        WHERE territory = 'Canada'
          AND program LIKE '%PSTC%'
          AND stackable_with LIKE '%BC FIBC (40%)%'
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # Restore the split state that existed before this migration
    # (rate text '40%', numeric 35.0) — mirrors p7q8r9s0t1u2's partial update
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '40% of qualified BC labour' "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {"territory": _TERRITORY, "program": _PROGRAM},
    )
