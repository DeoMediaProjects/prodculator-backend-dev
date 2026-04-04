"""fix_france_trip_rate_type

Revision ID: i1j2k3l4m5n6
Revises: h5i6j7k8l9m0
Create Date: 2026-03-28

ROOT CAUSE
----------
France TRIP has rate_type = 'tax_credit'. TAX_CREDIT_RATE_TYPES in helpers.py
is {"tax_credit", "enhanced_tax_credit"} — so the validator's apply_atl guard
triggers and applies a 15% ATL deduction to TRIP's qualifying spend.

The previous fix (e2f3g4h5i6j7) patched this by setting atl_exempt = True,
which prevents the deduction.  But atl_exempt is an override flag for
edge-case exceptions within the tax_credit type — it should not be required
when the rate_type itself is simply wrong.

TRIP is a cash rebate programme (the French production services company
receives a cash payment from the French tax authority, not a tax credit offset).
Setting rate_type = 'cash_rebate' makes cash_rebate NOT in TAX_CREDIT_RATE_TYPES,
so apply_atl = False by design, and atl_exempt is unnecessary.

FIX
---
Set rate_type = 'cash_rebate', clear atl_exempt = NULL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration

revision = "i1j2k3l4m5n6"
down_revision = "h5i6j7k8l9m0"
branch_labels = None
depends_on = None

_PROGRAM = "TRIP (Tax Rebate for International Production)"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_type        = 'cash_rebate',
            atl_exempt       = NULL,
            last_verified_at = '2026-03-28'
        WHERE territory = 'France'
          AND program   = :program
    """), {"program": _PROGRAM})
    assert_migration(
        conn, "incentive_programs",
        "territory = 'France' AND program = 'TRIP (Tax Rebate for International Production)'",
        {"rate_type": "cash_rebate", "atl_exempt": None},
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_type  = 'tax_credit',
            atl_exempt = TRUE
        WHERE territory = 'France'
          AND program   = :program
    """), {"program": _PROGRAM})
