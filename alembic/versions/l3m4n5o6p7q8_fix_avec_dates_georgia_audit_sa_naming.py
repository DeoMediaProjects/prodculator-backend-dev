"""fix_avec_dates_georgia_audit_sa_naming

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6p7
Create Date: 2026-03-23

Fixes three confirmed errors traced to LLM-seeded incorrect data:

1. AVEC eligibility_notes + warnings_json — wrong replacement date
   The Audio-Visual Expenditure Credit (AVEC) replaced Film Tax Relief (FTR)
   from 1 January 2024, not "1 April 2025" as stored in the DB.
   The April 2025 date appears to relate to a phase-in window for transitional
   productions but is NOT the AVEC commencement date and misleads producers
   into thinking FTR was still valid until recently.
   Fix: correct both eligibility_notes and the warnings_json entry.
   Also adds a clear taxable-income explanation: the 34% gross credit is
   taxable income subject to 25% UK corporation tax → effective 25.5% net.
   Source: HMRC AVEC guidance, Finance (No.2) Act 2023.

2. Georgia DOR audit — obsolete $2.5M threshold removed
   The $2.5M per-production threshold for mandatory DOR audit was removed
   effective 1 January 2023. ALL productions with a certified Georgia tax
   credit must now complete a mandatory DOR audit before the credit can be
   used or transferred. The old threshold language understates the audit
   burden for smaller productions.
   Source: Georgia DOR, HB 1302 (2022), Georgia DOR official guidance.

3. South Africa — correct programme name to "Foreign Film & TV Production
   Incentive"
   The 25% programme is the DTIC Foreign Film & TV Production Incentive,
   accessible to foreign (non-SA) productions. The DB row has been
   named "South Africa Film & TV Production Incentive" since initial
   seeding — this is ambiguous and conflicts with the distinct domestic
   35% programme (South African Film & TV Production Incentive, for
   SA-majority-owned productions). As of March 2026, both programmes are
   operationally suspended (no new LOAs), but correct naming avoids
   confusion when the programme eventually resumes.
   Source: DTIC Incentive Guidelines 2022.
"""
from __future__ import annotations

import json
import sqlalchemy as sa
from alembic import op

revision = "l3m4n5o6p7q8"
down_revision = "k2l3m4n5o6p7"
branch_labels = None
depends_on = None

# ── Constants ────────────────────────────────────────────────────────────────

AVEC_ELIGIBILITY_NOTES = (
    "AVEC is a single flat 34% rate applied to ALL qualifying UK expenditure — "
    "there is NO separate ATL/BTL split (that existed under the old Film Tax Relief "
    "which AVEC replaced from 1 January 2024). "
    "TAX NOTE: The 34% gross credit is taxable income subject to 25% UK corporation tax — "
    "effective net benefit is 25.5% (34% × 0.75). Do NOT present 34% as the take-home rate. "
    "VFX expenditure: separate 39% VFX Expenditure Credit available for qualifying UK VFX "
    "costs from 1 January 2025 spend. "
    "HETV strand (TV series): minimum £1M qualifying UK spend per broadcast hour. "
    "AVEC and IFTC are mutually exclusive — choose one per project. "
    "Minimum 10% core expenditure must be incurred in the UK."
)

AVEC_WARNINGS_JSON = json.dumps([
    "AVEC is a FLAT 34% — applies equally to all qualifying UK expenditure (no ATL/BTL split)",
    "TAX: The 34% AVEC receipt is taxable UK income — net benefit after 25% UK CT is 25.5% "
    "(34% × 0.75). Always present 25.5% as the effective rate to clients.",
    "The old 25%/34% ATL/BTL split was Film Tax Relief (FTR), which AVEC replaced from "
    "1 January 2024",
    "Separate 39% VFX Expenditure Credit available for qualifying UK VFX costs — see VFX row",
    "Mutually exclusive with IFTC",
    "HETV strand requires minimum £1M qualifying UK spend per broadcast hour",
    "BFI cultural test certification required before HMRC claim (allow 12-16 weeks)",
])

# Georgia — full updated warnings replacing the stale $2.5M threshold version
GEORGIA_WARNINGS_JSON = json.dumps([
    "Transferable credit \u2014 sold at ~88-90\u00a2 on the dollar",
    "Political risk: periodic legislative challenges to programme",
    "$500K cap applies to W-2 EMPLOYEES ONLY \u2014 wages above $500,000 per W-2 individual "
    "do not count as qualifying spend. IMPORTANT: Loan-outs, personal service companies, "
    "and 1099 arrangements are NOT subject to this per-person cap, provided Georgia income "
    "tax withholding is remitted by the production company. Most ATL talent (principal cast, "
    "directors) on high-budget features is structured via loan-out \u2014 verify your specific "
    "payroll with a Georgia CPA before reducing your modelled credit for ATL costs.",
    "MANDATORY DOR AUDIT: ALL productions with a certified Georgia Entertainment Industry "
    "Investment Act tax credit must complete a mandatory Georgia DOR audit before the credit "
    "can be used or transferred. There is NO minimum threshold \u2014 the requirement applies "
    "to every certified production regardless of credit amount. Typical timeline: 2\u20134 "
    "months (can exceed this for large or complex productions). Factor audit timeline into "
    "your cash-flow model. Source: Georgia DOR (dor.georgia.gov), effective 1 January 2023.",
])


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. AVEC — fix replacement date + add taxable income explanation ─────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = :notes,
            warnings_json     = :warnings,
            last_verified_at  = '2026-03-23'
        WHERE territory = 'United Kingdom'
          AND program   = 'Audio-Visual Expenditure Credit (AVEC)'
          AND status    = 'active'
    """), {"notes": AVEC_ELIGIBILITY_NOTES, "warnings": AVEC_WARNINGS_JSON})

    # ── 2. Georgia — remove obsolete $2.5M DOR audit threshold ──────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json    = :warnings,
            last_verified_at = '2026-03-23'
        WHERE territory = 'Georgia (USA)'
          AND program   = 'Georgia Entertainment Industry Investment Act'
          AND status    = 'active'
    """), {"warnings": GEORGIA_WARNINGS_JSON})

    # ── 3. South Africa — correct programme name ─────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET program          = 'Foreign Film & TV Production Incentive',
            last_verified_at = '2026-03-23'
        WHERE territory = 'South Africa'
          AND program   = 'South Africa Film & TV Production Incentive'
          AND status    = 'active'
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # Restore AVEC original (incorrect) text
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = 'AVEC is a single flat 34% rate applied to ALL qualifying UK expenditure — there is NO separate ATL/BTL split (that existed under the old Film Tax Relief which AVEC replaced from 1 April 2025). VFX expenditure: separate 39% VFX Expenditure Credit available for qualifying UK VFX costs from 1 January 2025 spend. HETV strand (TV series): minimum £1M qualifying UK spend per broadcast hour. AVEC and IFTC are mutually exclusive — choose one per project. Minimum 10% core expenditure must be incurred in the UK.',
            warnings_json     = '["AVEC is a FLAT 34% — applies equally to all qualifying UK expenditure (no ATL/BTL split)","The old 25%/34% ATL/BTL split was Film Tax Relief (FTR), which AVEC replaced April 2025","Separate 39% VFX Expenditure Credit available for qualifying UK VFX costs — see VFX row","Mutually exclusive with IFTC","HETV strand requires minimum £1M qualifying UK spend per broadcast hour","BFI cultural test certification required before HMRC claim (allow 12-16 weeks)"]'
        WHERE territory = 'United Kingdom'
          AND program   = 'Audio-Visual Expenditure Credit (AVEC)'
          AND status    = 'active'
    """))

    # Restore Georgia old warning with $2.5M threshold
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = '["Transferable credit \u2014 sold at ~88-90\u00a2 on the dollar","Political risk: periodic legislative challenges to programme","$500K cap applies to W-2 EMPLOYEES ONLY \u2014 wages above $500,000 per W-2 individual do not count as qualifying spend. IMPORTANT: Loan-outs, personal service companies, and 1099 arrangements are NOT subject to this per-person cap, provided Georgia income tax withholding is remitted by the production company. Most ATL talent (principal cast, directors) on high-budget features is structured via loan-out \u2014 verify your specific payroll with a Georgia CPA before reducing your modelled credit for ATL costs.","MANDATORY DOR AUDIT: All productions claiming >$2.5M in credits must complete a mandatory Georgia DOR audit before credits can be used or transferred. Typical timeline: 2\u20134 months (can exceed this for large or complex productions). Factor audit timeline into your cash-flow model. Effective January 2023. Source: Georgia DOR (dor.georgia.gov)."]'
        WHERE territory = 'Georgia (USA)'
          AND program   = 'Georgia Entertainment Industry Investment Act'
          AND status    = 'active'
    """))

    # Restore South Africa programme name
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET program = 'South Africa Film & TV Production Incentive'
        WHERE territory = 'South Africa'
          AND program   = 'Foreign Film & TV Production Incentive'
          AND status    = 'active'
    """))
