"""add_atomic_incentive_columns

Revision ID: k3l4m5n6o7p8
Revises: j2k3l4m5n6o7
Create Date: 2026-03-28

ROOT CAUSE
----------
Critical single-value facts were buried at the end of long `eligibility_notes`
strings and silently truncated by service.py's MAX_PROMPT_TEXT_CHARS = 240
before reaching the AI.  The fix in f3g4h5i6j7k8 worked around this by
reordering strings — but reordering strings to survive truncation is a design
smell, not a fix.

The root cause is using a single free-text field (`eligibility_notes`) as a
catch-all for structured facts that the AI needs to act on precisely.

FIX
---
Add three dedicated atomic columns to `incentive_programs`:

  net_rate_pct   FLOAT      — investor-facing net rate after local tax
                              (e.g. UK VFX 29.25% = 39% gross × 0.75)
  payee_note     TEXT       — who receives the rebate payment
                              (e.g. France TRIP: French PSC, not foreign producer)
  filing_note    TEXT       — filing/entity requirement clarification
                              (e.g. BC PSTC: no Canadian co-producer needed)

The builder creates dedicated first-class skeleton keys from these columns
(`netRatePct`, `payeeNote`, `filingNote`), bypassing the string-trimming
problem entirely. These values are always visible to the AI regardless of
the length of any associated `eligibility_notes` text.

DATA POPULATED
--------------
- UK VFX Expenditure Credit: net_rate_pct = 29.25
- France TRIP: payee_note = "Rebate paid to French PSC..."
- BC PSTC: filing_note = "No Canadian co-producer required..."
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration

revision = "k3l4m5n6o7p8"
down_revision = "j2k3l4m5n6o7"
branch_labels = None
depends_on = None

_NEW_COLS = [
    ("net_rate_pct", sa.Float()),
    ("payee_note",   sa.Text()),
    ("filing_note",  sa.Text()),
]

# Authoritative values
_UK_VFX_NET_RATE = 29.25

_FRANCE_TRIP_PAYEE = (
    "Rebate paid to French production services company (PSC/société de production "
    "française), NOT directly to the foreign producer. Foreign producer receives "
    "the economic benefit via their service agreement with the French entity."
)

_BC_PSTC_FILING = (
    "No Canadian co-producer required. Foreign-owned corporations are directly "
    "eligible by registering as an accredited production corporation with Creative "
    "BC — permanent establishment in BC suffices. Distinct from BC FIBC which "
    "requires a Canadian-controlled corporation."
)


def upgrade() -> None:
    conn = op.get_bind()

    # Add columns (skip if they already exist — idempotent)
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("incentive_programs")}
    for col_name, col_type in _NEW_COLS:
        if col_name not in existing:
            op.add_column(
                "incentive_programs",
                sa.Column(col_name, col_type, nullable=True),
            )

    # ── UK VFX — net_rate_pct ────────────────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET net_rate_pct     = :rate,
            last_verified_at = '2026-03-28'
        WHERE territory = 'United Kingdom'
          AND program   = 'VFX Expenditure Credit (Uplift)'
    """), {"rate": _UK_VFX_NET_RATE})
    assert_migration(
        conn, "incentive_programs",
        "territory = 'United Kingdom' AND program = 'VFX Expenditure Credit (Uplift)'",
        {"net_rate_pct": _UK_VFX_NET_RATE},
        migration_id=revision,
    )

    # ── France TRIP — payee_note ─────────────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payee_note       = :note,
            last_verified_at = '2026-03-28'
        WHERE territory = 'France'
          AND program   = 'TRIP (Tax Rebate for International Production)'
    """), {"note": _FRANCE_TRIP_PAYEE})
    assert_migration(
        conn, "incentive_programs",
        "territory = 'France' AND program = 'TRIP (Tax Rebate for International Production)'",
        {"payee_note": _FRANCE_TRIP_PAYEE},
        migration_id=revision,
    )

    # ── BC PSTC — filing_note ────────────────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET filing_note      = :note,
            last_verified_at = '2026-03-28'
        WHERE territory = 'British Columbia'
          AND program   = 'BC Production Services Tax Credit (PSTC)'
    """), {"note": _BC_PSTC_FILING})
    assert_migration(
        conn, "incentive_programs",
        "territory = 'British Columbia' AND program = 'BC Production Services Tax Credit (PSTC)'",
        {"filing_note": _BC_PSTC_FILING},
        migration_id=revision,
    )


def downgrade() -> None:
    for col_name, _ in _NEW_COLS:
        op.drop_column("incentive_programs", col_name)
