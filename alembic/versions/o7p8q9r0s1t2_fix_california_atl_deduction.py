"""fix_california_atl_deduction

Revision ID: o7p8q9r0s1t2
Revises: n6o7p8q9r0s1
Create Date: 2026-03-28

ROOT CAUSE
----------
California Program 4.0 (and New Mexico) use rate_type = 'refundable_tax_credit'.
TAX_CREDIT_RATE_TYPES in helpers.py contained only {"tax_credit", "enhanced_tax_credit"},
so the apply_atl guard in _compute_corrected_rebate evaluated False for California —
no ATL deduction was applied and the full budget was used as qualifying spend.

IMPACT
------
For a $23M feature drama with 34 principal cast:
    Incorrect: $23,000,000 × 35% = $8,050,000
    Correct:   $23,000,000 × 0.85 × 35% = $6,842,500  (approx, using 15% ATL estimate)

The statutory basis for the correction is California Revenue and Taxation Code
§ 17053.98(b)(21)(B), which categorically excludes from "qualified wages":
    "Expenses, including wages, paid per person per qualified motion picture for
    writers, directors, music directors, music composers, music supervisors,
    producers, and performers, other than background actors with no scripted lines."

The LAO confirmed: "California is the only program in our comparison that does not
allow any so-called 'above-the-line spending', such as wages for directors, writers,
and actors, to count toward expenditures for credit purposes."

Refundability is a payment mechanic (cash refund vs. offset against tax liability).
It has no bearing on what costs qualify. The fix adds "refundable_tax_credit" to
TAX_CREDIT_RATE_TYPES in helpers.py so the ATL deduction applies correctly.

NO DATA CHANGES REQUIRED
-------------------------
This bug was purely in application logic (helpers.py), not in the DB. The migration
exists solely to record the fix in the version trail and make the root cause auditable.
The assert below confirms California and New Mexico are still active, giving confidence
the programs this fix targets exist in the DB.

Source: California R&TC § 17053.98(b)(21)(B), LAO 2025-26 Budget Report
Last Verified: 2026-03-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "o7p8q9r0s1t2"
down_revision = "n6o7p8q9r0s1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # No DB changes — fix is in helpers.py TAX_CREDIT_RATE_TYPES.
    # Assert the affected programmes exist so a schema rename would surface here.
    assert_migration_count(
        conn,
        "incentive_programs",
        "rate_type = 'refundable_tax_credit' AND status = 'active'",
        expected_min=1,
        migration_id=revision,
    )


def downgrade() -> None:
    # No DB changes to reverse. To revert the logic change, remove
    # "refundable_tax_credit" from TAX_CREDIT_RATE_TYPES in helpers.py.
    pass
