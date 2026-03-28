"""fix_data_accuracy_v4

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
Create Date: 2026-03-24

Fixes three data accuracy issues identified in v4 report review:

1. VFX Expenditure Credit — remove incorrect qualifying_spend_cap_pct=80.
   BFI guidance confirms that UK VFX Additional Credit qualifying expenditure
   is NOT subject to the 80% qualifying spend cap that applies to AVEC.
   The cap field was inherited from the AVEC row during initial seeding.
   Source: HMRC CREC023000 / BFI VFX Additional Credit guidance.

2. BC Film Incentive BC Tax Credit (FIBC) — correct rate_gross/net 40% → 35%.
   Creative BC's published FIBC rate is 35% basic credit. The 40% figure in
   the DB was incorrect; uplift credits (Regional 12.5%, Training 30%,
   Distant Location 6%) are additive and programme-specific, not part of
   the base rate. As FIBC is now marked is_supplementary=true the rate has
   limited impact, but must be accurate for the rates context table.
   Source: Creative BC FIBC programme page.

3. South Africa visa_requirements — correct UK nationals stay from 30 to 90 days.
   South Africa allows UK passport holders visa-free entry for up to 90 days
   (not 30 days as previously seeded). The 30-day figure was incorrect.
   Source: South African Home Affairs / UK FCDO travel advice.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "p7q8r9s0t1u2"
down_revision = "o6p7q8r9s0t1"
branch_labels = None
depends_on = None

_SA_VISA_NOTES_CORRECT = (
    "UK nationals do not require a visa for South Africa for stays up to 90 days. "
    "Film crew work permits (General Work Visa) required for extended shoots — "
    "apply through South African High Commission. Allow 6–12 weeks."
)

_SA_VISA_NOTES_OLD = (
    "UK nationals do not require a visa for South Africa for stays up to 30 days. "
    "Film crew work permits (General Work Visa) required for extended shoots — "
    "apply through South African High Commission. Allow 6–12 weeks."
)

# Also update Western Cape which has the same incorrect note
_WC_VISA_NOTES_CORRECT = (
    "UK nationals do not require a visa for South Africa for stays up to 90 days. "
    "Film crew work permits required for extended shoots. "
    "Apply through South African High Commission. Allow 6–12 weeks."
)

_WC_VISA_NOTES_OLD = (
    "UK nationals do not require a visa for South Africa for stays up to 30 days. "
    "Film crew work permits required for extended shoots. "
    "Apply through South African High Commission. Allow 6–12 weeks."
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. VFX Expenditure Credit — remove 80% qualifying spend cap ───────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_cap_pct = NULL,
            last_verified_at         = '2026-03-24'
        WHERE territory = :territory
          AND program   = :program
    """), {
        "territory": "United Kingdom",
        "program": "VFX Expenditure Credit (Uplift)",
    })

    # ── 2. BC FIBC — correct base rate 40% → 35% ──────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross        = 35.0,
            rate_net          = 35.0,
            last_verified_at  = '2026-03-24'
        WHERE territory = :territory
          AND program   = :program
    """), {
        "territory": "British Columbia",
        "program": "BC Film Incentive BC Tax Credit (FIBC)",
    })

    # ── 3. South Africa + Western Cape visa — 30 days → 90 days ───────────────
    conn.execute(sa.text("""
        UPDATE visa_requirements
        SET notes            = :notes,
            last_verified_at = '2026-03-24'
        WHERE base_country = :base_country
          AND destination  = :destination
    """), {
        "notes": _SA_VISA_NOTES_CORRECT,
        "base_country": "United Kingdom",
        "destination": "South Africa",
    })

    conn.execute(sa.text("""
        UPDATE visa_requirements
        SET notes            = :notes,
            last_verified_at = '2026-03-24'
        WHERE base_country = :base_country
          AND destination  = :destination
    """), {
        "notes": _WC_VISA_NOTES_CORRECT,
        "base_country": "United Kingdom",
        "destination": "Western Cape",
    })


def downgrade() -> None:
    conn = op.get_bind()

    # Restore VFX 80% cap
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_cap_pct = 80.0
        WHERE territory = :territory
          AND program   = :program
    """), {
        "territory": "United Kingdom",
        "program": "VFX Expenditure Credit (Uplift)",
    })

    # Restore BC FIBC 40%
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross = 40.0,
            rate_net   = 40.0
        WHERE territory = :territory
          AND program   = :program
    """), {
        "territory": "British Columbia",
        "program": "BC Film Incentive BC Tax Credit (FIBC)",
    })

    # Restore 30-day visa notes
    conn.execute(sa.text("""
        UPDATE visa_requirements
        SET notes = :notes
        WHERE base_country = :base_country
          AND destination  = :destination
    """), {
        "notes": _SA_VISA_NOTES_OLD,
        "base_country": "United Kingdom",
        "destination": "South Africa",
    })

    conn.execute(sa.text("""
        UPDATE visa_requirements
        SET notes = :notes
        WHERE base_country = :base_country
          AND destination  = :destination
    """), {
        "notes": _WC_VISA_NOTES_OLD,
        "base_country": "United Kingdom",
        "destination": "Western Cape",
    })
