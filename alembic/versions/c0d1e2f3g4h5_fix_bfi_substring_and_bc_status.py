"""fix_bfi_substring_and_bc_status

Revision ID: c0d1e2f3g4h5
Revises: b9c0d1e2f3g4
Create Date: 2026-03-27

Corrects three silent failures in migrations a8b9c0d1e2f3 and b9c0d1e2f3g4:

FAILURE 1 — BFI timeline "12-16 weeks" still appearing in reports
-------------------------------------------------------------------
b9c0d1e2f3g4 tried to replace the BFI certification timeline across all UK
incentive rows using:
    REPLACE(payment_timeline_notes, 'BFI certification (12-16 weeks) required first.', ...)

However, migration g8h9i0j1k2l3 had previously written the AVEC row with:
    'BFI cultural test certification (12-16 weeks) required first.'
(note the extra "cultural test" words). The exact substring in b9c0d1e2f3g4
did not match the actual DB value, so REPLACE was a silent no-op even though
the WHERE LIKE '%12-16 weeks%' clause matched correctly.

This migration uses the correct "cultural test" substring for AVEC and also
covers the shorter form ('BFI certification (12-16 weeks)') used in the VFX
row and any seed-data rows not rewritten by g8h9i0j1k2l3.

FAILURE 2 — BC FIBC rate still stored as 40% (not 36%)
-------------------------------------------------------
b9c0d1e2f3g4 updated BC FIBC with:
    WHERE territory = 'British Columbia'
      AND program   = 'BC Film Incentive BC Tax Credit (FIBC)'
      AND rate_gross = 40.0
      AND (status = 'active' OR status IS NULL)

The service layer (service.py) Python filter includes rows where
status = '' (empty string) in addition to 'active' and NULL. If the BC FIBC
row has status = '', the SQL WHERE clause does not match it (empty string
satisfies neither 'active' nor IS NULL), so the UPDATE silently affected 0
rows.

This migration drops the status condition entirely — there is only one BC FIBC
row, identified unambiguously by territory + program + rate_gross = 40.0.

FAILURE 3 — IFTC rebate_cap_amount may not have been set
---------------------------------------------------------
a8b9c0d1e2f3 set rebate_cap_amount = £6,360,000 on IFTC with:
    WHERE territory = 'United Kingdom'
      AND program   = 'UK Independent Film Tax Credit (IFTC)'
      AND (status = 'active' OR status IS NULL)

If IFTC has status = '' (empty string), the same status filter bug applies:
SQL excludes empty string, Python service filter accepts it. This migration
re-applies the cap with no status filter as a failsafe.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c0d1e2f3g4h5"
down_revision = "b9c0d1e2f3g4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1a. BFI timeline — AVEC form (with "cultural test") ──────────────────
    # g8h9i0j1k2l3 set AVEC payment_timeline_notes to:
    #   "...BFI cultural test certification (12-16 weeks) required first."
    # b9c0d1e2f3g4 searched for "BFI certification (12-16 weeks)..." → no match.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = REPLACE(
                payment_timeline_notes,
                'BFI cultural test certification (12-16 weeks) required first.',
                'BFI cultural test certification typically 6-8 weeks (standard); allow up to 12 weeks for complex cases — apply early.'
            ),
            last_verified_at = '2026-03-27'
        WHERE territory = 'United Kingdom'
          AND payment_timeline_notes LIKE '%BFI cultural test certification (12-16 weeks)%'
    """))

    # ── 1b. BFI timeline — shorter form without "cultural test" ──────────────
    # VFX Expenditure Credit row (inserted by g8h9i0j1k2l3) uses:
    #   "6-8 weeks from HMRC claim. BFI certification required first."
    # (no "12-16 weeks" — would not have triggered the WHERE in b9c0d1e2f3g4
    # at all, so it is already correct and this UPDATE is a safe no-op for it).
    # Any remaining seed-data rows with "BFI certification (12-16 weeks)" form:
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = REPLACE(
                payment_timeline_notes,
                'BFI certification (12-16 weeks) required first.',
                'BFI cultural test certification typically 6-8 weeks (standard); allow up to 12 weeks for complex cases — apply early.'
            ),
            last_verified_at = '2026-03-27'
        WHERE territory = 'United Kingdom'
          AND payment_timeline_notes LIKE '%BFI certification (12-16 weeks)%'
          AND payment_timeline_notes NOT LIKE '%cultural test%'
    """))

    # ── 2. BC FIBC — correct rate 40% → 36%, no status filter ────────────────
    # Drops the AND (status = 'active' OR status IS NULL) predicate that caused
    # the previous UPDATE to match 0 rows when status = '' (empty string).
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate             = '36% of qualified BC labour',
            rate_gross       = 36.0,
            rate_net         = 36.0,
            last_verified_at = '2026-03-27'
        WHERE territory = 'British Columbia'
          AND program   = 'BC Film Incentive BC Tax Credit (FIBC)'
          AND rate_gross = 40.0
    """))

    # ── 3. IFTC rebate cap — failsafe with no status filter ───────────────────
    # a8b9c0d1e2f3 set this with (status = 'active' OR status IS NULL).
    # Re-apply with no status condition to catch status = '' rows.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_amount   = 6360000.0,
            rebate_cap_currency = 'GBP',
            last_verified_at    = '2026-03-27'
        WHERE territory = 'United Kingdom'
          AND program   = 'UK Independent Film Tax Credit (IFTC)'
    """))

    # Also fix Canada PSTC warnings if they still reference "BC FIBC (40%)"
    # (b9c0d1e2f3g4 already attempted this without a status filter, so this
    # is a safety net in case the PSTC row itself has status = '').
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(warnings_json, 'BC FIBC (40%)', 'BC FIBC (36%)')
        WHERE territory = 'Canada'
          AND program LIKE '%PSTC%'
          AND warnings_json LIKE '%BC FIBC (40%)%'
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # 1a. Restore AVEC "cultural test" form
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = REPLACE(
                payment_timeline_notes,
                'BFI cultural test certification typically 6-8 weeks (standard); allow up to 12 weeks for complex cases — apply early.',
                'BFI cultural test certification (12-16 weeks) required first.'
            )
        WHERE territory = 'United Kingdom'
          AND payment_timeline_notes LIKE '%BFI cultural test certification typically%'
    """))

    # 1b. Restore shorter form (seed data rows)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = REPLACE(
                payment_timeline_notes,
                'BFI cultural test certification typically 6-8 weeks (standard); allow up to 12 weeks for complex cases — apply early.',
                'BFI certification (12-16 weeks) required first.'
            )
        WHERE territory = 'United Kingdom'
          AND payment_timeline_notes LIKE '%BFI cultural test certification typically%'
    """))

    # 3. IFTC rebate cap — remove
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_amount   = NULL,
            rebate_cap_currency = NULL
        WHERE territory = 'United Kingdom'
          AND program   = 'UK Independent Film Tax Credit (IFTC)'
          AND rebate_cap_amount = 6360000.0
    """))

    # 2. BC FIBC — restore 40%
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate       = '40% of qualified BC labour',
            rate_gross = 40.0,
            rate_net   = 40.0
        WHERE territory = 'British Columbia'
          AND program   = 'BC Film Incentive BC Tax Credit (FIBC)'
          AND rate_gross = 36.0
    """))

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(warnings_json, 'BC FIBC (36%)', 'BC FIBC (40%)')
        WHERE territory = 'Canada'
          AND program LIKE '%PSTC%'
          AND warnings_json LIKE '%BC FIBC (36%)%'
    """))
