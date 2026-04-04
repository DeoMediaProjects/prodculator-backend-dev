"""populate_rebate_cap_amount

Revision ID: s0t1u2v3w4x5
Revises: r9s0t1u2v3w4
Create Date: 2026-03-24

Populates rebate_cap_amount / rebate_cap_currency for five territories whose
per-project rebate caps were stored only in cap_amount / cap_currency.

cap_amount is semantically a budget threshold that triggers programme
switching (e.g. UK IFTC → AVEC at £23.5M). rebate_cap_amount is the hard
ceiling on the computed rebate (grant) per project. The distinction matters
in _compute_corrected_rebate().

Affected territories:
  - Netherlands:  €3M per project
  - Portugal:     €1.5M per project
  - South Korea:  KRW 200M per project
  - India:        INR 300M per project
  - Japan:        JPY 1B per project

For these territories, cap_amount is NOT a budget threshold — it IS the
per-project rebate ceiling. So we copy cap_amount → rebate_cap_amount and
clear cap_amount (since there is no separate budget threshold).

South Africa already has rebate_cap_amount correctly set (R25M ZAR) from
migration e6f7g8h9i0j1.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s0t1u2v3w4x5"
down_revision = "r9s0t1u2v3w4"
branch_labels = None
depends_on = None

# Territories whose cap_amount is actually a per-project rebate cap
_TERRITORIES = [
    "Netherlands",
    "Portugal",
    "South Korea",
    "India",
    "Japan",
]


def upgrade() -> None:
    conn = op.get_bind()

    for territory in _TERRITORIES:
        # Copy cap_amount → rebate_cap_amount, cap_currency → rebate_cap_currency
        # Then clear cap_amount since it's not a budget threshold for these
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET rebate_cap_amount   = cap_amount,
                rebate_cap_currency = cap_currency,
                cap_amount          = NULL,
                last_verified_at    = '2026-03-24'
            WHERE territory = :territory
              AND cap_amount IS NOT NULL
              AND (rebate_cap_amount IS NULL OR rebate_cap_amount = 0)
              AND (status = 'active' OR status IS NULL)
        """), {"territory": territory})


def downgrade() -> None:
    conn = op.get_bind()

    for territory in _TERRITORIES:
        # Restore: rebate_cap_amount → cap_amount
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET cap_amount          = rebate_cap_amount,
                cap_currency        = rebate_cap_currency,
                rebate_cap_amount   = NULL,
                rebate_cap_currency = NULL
            WHERE territory = :territory
              AND rebate_cap_amount IS NOT NULL
              AND cap_amount IS NULL
              AND (status = 'active' OR status IS NULL)
        """), {"territory": territory})
