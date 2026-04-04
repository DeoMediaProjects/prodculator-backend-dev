"""fix_bfi_timeline_bc_rate_sa_date_canada_pstc

Revision ID: b9c0d1e2f3g4
Revises: a8b9c0d1e2f3
Create Date: 2026-03-27

Corrects six confirmed data errors identified in report accuracy review:

1. BFI cultural test timeline overstated as "12-16 weeks" across all UK
   incentive programmes.
   The BFI's own guidance states: "Overall, we advise that it can take 6 to
   8 weeks from receiving the application to issuing a certificate."
   12 weeks is possible only in complex/contested cases, not the standard.
   Source: BFI certification guidance (bfi.org.uk)
   Affected: AVEC, IFTC, VFX Expenditure Credit (Uplift)

2. VFX Expenditure Credit eligibility_notes does not disclose the net rate
   after UK corporation tax.  AVEC eligibility_notes already has this note
   ("34% gross / 25.5% net") but the VFX credit row was missing the
   equivalent.  The gross rate is 39%; net after 25% corp tax = 29.25%.

3. France TRIP eligibility_notes does not disclose that the rebate is paid
   to the French production services company (société de production
   française), not to the foreign producer directly.  This has legal and
   structuring implications — the foreign producer receives the benefit via
   their service contract with the French entity.
   Source: CNC / Film France official programme documentation

4. South Africa payment_timeline_notes reads "FROZEN as of March 2026",
   which implies the freeze only began in March 2026.  The freeze has been
   in effect since early 2024 (no new LOAs since March 2024 — over two
   years by the time of this correction).  The phrase "as of March 2026"
   is the date the DB entry was last updated, not when the freeze started.

5. BC Film Incentive BC Tax Credit (FIBC) rate is stored as 40%, set by
   migration d5e6f7g8h9i0 citing creativebc.com.  This appears to have
   confused the enhanced/regional uplift rate with the basic credit rate.
   Migration c4d5e6f7g8h9 (the preceding migration) correctly documented
   the rate as 36% effective January 2025 from the same source.  The
   accuracy review confirms the correct basic rate is 36%.
   Source: Creative BC (creativebc.com), January 2025 update
   Also updates Canada PSTC warnings_json text which still referenced
   the now-stale "BC FIBC (35%)" string (per migration t1u2v3w4x5y6).

6. Canada Federal PSTC payment_timeline_notes says "4-12 months".
   CAVCO's published service standard is 176 calendar days (~6 months),
   met 85% of the time.  The 4-12 month range is imprecise and not tied
   to the official standard.
   Source: CAVCO service standards (canada.ca/cavco)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b9c0d1e2f3g4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None

# ── Shared constants ──────────────────────────────────────────────────────────

_BFI_TIMELINE_OLD = "BFI certification (12-16 weeks) required first."
_BFI_TIMELINE_NEW = "BFI cultural test certification typically 6-8 weeks (standard); allow up to 12 weeks for complex cases — apply early."

_BFI_WARN_OLD = "allow 12-16 weeks"
_BFI_WARN_NEW = "allow 6-8 weeks (standard); complex cases up to 12 weeks"

# Alternate form used in some rows
_BFI_WARN_OLD2 = "BFI cultural test certification required before HMRC claim (allow 12-16 weeks)"
_BFI_WARN_NEW2 = "BFI cultural test certification required before HMRC claim (allow 6-8 weeks; complex cases up to 12 weeks)"


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. BFI certification timeline — all UK incentive programmes ───────────

    # Update payment_timeline_notes (used in report payment speed field)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = REPLACE(
                payment_timeline_notes,
                'BFI certification (12-16 weeks) required first.',
                'BFI cultural test certification typically 6-8 weeks (standard); allow up to 12 weeks for complex cases — apply early.'
            ),
            last_verified_at = '2026-03-27'
        WHERE territory = 'United Kingdom'
          AND payment_timeline_notes LIKE '%12-16 weeks%'
          AND (status = 'active' OR status IS NULL)
    """))

    # Update warnings_json string references (two variant forms)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(
                warnings_json,
                'allow 12-16 weeks',
                'allow 6-8 weeks (standard); complex cases up to 12 weeks'
            )
        WHERE territory = 'United Kingdom'
          AND warnings_json LIKE '%12-16 weeks%'
    """))

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(
                warnings_json,
                'BFI cultural test certification required before HMRC claim (allow 12-16 weeks)',
                'BFI cultural test certification required before HMRC claim (allow 6-8 weeks; complex cases up to 12 weeks)'
            )
        WHERE territory = 'United Kingdom'
          AND warnings_json LIKE '%allow 12-16 weeks%'
    """))

    # Also fix the eligibility_notes form: "12-16 weeks" used in some rows
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = REPLACE(
                eligibility_notes,
                '12-16 weeks',
                '6-8 weeks (standard); up to 12 weeks for complex cases'
            )
        WHERE territory = 'United Kingdom'
          AND eligibility_notes LIKE '%12-16 weeks%'
    """))

    # ── 2. VFX Expenditure Credit — add net rate disclosure ───────────────────

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = eligibility_notes || ' NET RATE: 29.25% (gross 39% less 25% UK corporation tax) — present the net rate to investors, not the gross rate.',
            last_verified_at = '2026-03-27'
        WHERE territory = 'United Kingdom'
          AND program = 'VFX Expenditure Credit (Uplift)'
          AND eligibility_notes NOT LIKE '%NET RATE%'
          AND (status = 'active' OR status IS NULL)
    """))

    # ── 3. France TRIP — add rebate payee clarification ───────────────────────

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = eligibility_notes || ' REBATE PAYEE: The TRIP rebate is granted to and paid to the French production services company (société de production française), NOT directly to the foreign producer. The foreign producer receives the economic benefit via their service contract with the French entity — structure this correctly before principal photography.',
            last_verified_at = '2026-03-27'
        WHERE territory = 'France'
          AND program = 'TRIP (Tax Rebate for International Production)'
          AND eligibility_notes NOT LIKE '%REBATE PAYEE%'
          AND (status = 'active' OR status IS NULL)
    """))

    # ── 4. South Africa — correct freeze start date wording ───────────────────

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = REPLACE(
                payment_timeline_notes,
                'PROGRAMME EFFECTIVELY FROZEN as of March 2026.',
                'PROGRAMME EFFECTIVELY FROZEN since early 2024 (no new LOAs issued since March 2024 — over two years as of this update).'
            ),
            last_verified_at = '2026-03-27'
        WHERE territory = 'South Africa'
          AND payment_timeline_notes LIKE '%FROZEN as of March 2026%'
    """))

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = REPLACE(
                eligibility_notes,
                'operationally suspended as of March 2026',
                'operationally suspended since early 2024 (no new LOAs since March 2024)'
            )
        WHERE territory = 'South Africa'
          AND eligibility_notes LIKE '%operationally suspended as of March 2026%'
    """))

    # ── 5. BC FIBC — revert rate 40% → 36% (correct basic rate Jan 2025) ──────

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate       = '36% of qualified BC labour',
            rate_gross = 36.0,
            rate_net   = 36.0,
            last_verified_at = '2026-03-27'
        WHERE territory = 'British Columbia'
          AND program   = 'BC Film Incentive BC Tax Credit (FIBC)'
          AND rate_gross = 40.0
          AND (status = 'active' OR status IS NULL)
    """))

    # Update Canada PSTC warnings text to reflect corrected 36% BC FIBC rate
    # (migration t1u2v3w4x5y6 had previously set this to 35%, which was also wrong)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(warnings_json, 'BC FIBC (35%)', 'BC FIBC (36%)')
        WHERE territory = 'Canada'
          AND program LIKE '%PSTC%'
          AND warnings_json LIKE '%BC FIBC (35%)%'
    """))

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(warnings_json, 'BC FIBC (40%)', 'BC FIBC (36%)')
        WHERE territory = 'Canada'
          AND program LIKE '%PSTC%'
          AND warnings_json LIKE '%BC FIBC (40%)%'
    """))

    # ── 6. Canada PSTC — update payment timeline notes ────────────────────────

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = 'Approximately 6 months (CAVCO service standard: 176 calendar days, 85% performance target). Allow 4-12 months in backlog periods — do not treat as investor-bankable without a confirmed gap facility.',
            payment_timeline_days_min = 120,
            payment_timeline_days_max = 365,
            last_verified_at = '2026-03-27'
        WHERE territory = 'Canada'
          AND program = 'Canada Federal PSTC (Production Services Tax Credit)'
          AND (status = 'active' OR status IS NULL)
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # 1. BFI timeline — restore 12-16 weeks
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = REPLACE(
                payment_timeline_notes,
                'BFI cultural test certification typically 6-8 weeks (standard); allow up to 12 weeks for complex cases — apply early.',
                'BFI certification (12-16 weeks) required first.'
            )
        WHERE territory = 'United Kingdom'
          AND payment_timeline_notes LIKE '%6-8 weeks%'
    """))
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(
                warnings_json,
                'allow 6-8 weeks (standard); complex cases up to 12 weeks',
                'allow 12-16 weeks'
            )
        WHERE territory = 'United Kingdom'
    """))
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(
                warnings_json,
                'BFI cultural test certification required before HMRC claim (allow 6-8 weeks; complex cases up to 12 weeks)',
                'BFI cultural test certification required before HMRC claim (allow 12-16 weeks)'
            )
        WHERE territory = 'United Kingdom'
    """))
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = REPLACE(
                eligibility_notes,
                '6-8 weeks (standard); up to 12 weeks for complex cases',
                '12-16 weeks'
            )
        WHERE territory = 'United Kingdom'
    """))

    # 2. VFX net rate — remove appended sentence
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = REPLACE(
                eligibility_notes,
                ' NET RATE: 29.25% (gross 39% less 25% UK corporation tax) — present the net rate to investors, not the gross rate.',
                ''
            )
        WHERE territory = 'United Kingdom'
          AND program = 'VFX Expenditure Credit (Uplift)'
    """))

    # 3. France TRIP — remove rebate payee note
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = REPLACE(
                eligibility_notes,
                ' REBATE PAYEE: The TRIP rebate is granted to and paid to the French production services company (société de production française), NOT directly to the foreign producer. The foreign producer receives the economic benefit via their service contract with the French entity — structure this correctly before principal photography.',
                ''
            )
        WHERE territory = 'France'
          AND program = 'TRIP (Tax Rebate for International Production)'
    """))

    # 4. South Africa — restore original wording
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = REPLACE(
                payment_timeline_notes,
                'PROGRAMME EFFECTIVELY FROZEN since early 2024 (no new LOAs issued since March 2024 — over two years as of this update).',
                'PROGRAMME EFFECTIVELY FROZEN as of March 2026.'
            )
        WHERE territory = 'South Africa'
    """))
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = REPLACE(
                eligibility_notes,
                'operationally suspended since early 2024 (no new LOAs since March 2024)',
                'operationally suspended as of March 2026'
            )
        WHERE territory = 'South Africa'
    """))

    # 5. BC FIBC — restore 40%
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
        SET warnings_json = REPLACE(warnings_json, 'BC FIBC (36%)', 'BC FIBC (35%)')
        WHERE territory = 'Canada' AND program LIKE '%PSTC%'
    """))

    # 6. Canada PSTC — restore original notes
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_timeline_notes = '4-12 months. CRA processing times vary — priority review available for qualifying productions.',
            payment_timeline_days_min = 120,
            payment_timeline_days_max = 365
        WHERE territory = 'Canada'
          AND program = 'Canada Federal PSTC (Production Services Tax Credit)'
    """))
