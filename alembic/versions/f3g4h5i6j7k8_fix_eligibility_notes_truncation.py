"""fix_eligibility_notes_truncation

Revision ID: f3g4h5i6j7k8
Revises: e2f3g4h5i6j7
Create Date: 2026-03-28

ROOT CAUSE — eligibility_notes truncated at 240 chars in prompt
---------------------------------------------------------------
service.py trims all string values to MAX_PROMPT_TEXT_CHARS = 240 before
passing the skeleton to the AI.  Two DB rows had critical information appended
at the END of long strings — both were truncated before reaching the AI.

ISSUE 1 — UK VFX Expenditure Credit: net rate 29.25% never seen by AI
  The existing eligibility_notes string (360+ chars) put "NET RATE: 29.25%"
  at position ~310.  After trimming to 240 chars, the AI never saw the net rate.
  The AI therefore cited the gross rate (39%) instead of the investor-facing
  net rate (29.25%).  Fix: rewrite eligibility_notes with NET RATE first.
  "CANNOT be combined with IFTC" is preserved — the builder uses a regex on
  this exact phrase to detect mutual exclusivity.

ISSUE 2 — France TRIP: rebate payee never written to DB
  Migration b9c0d1e2f3g4 used:
      SET eligibility_notes = eligibility_notes || ' REBATE PAYEE: ...'
  BUT eligibility_notes was NULL for TRIP.  In PostgreSQL NULL || text = NULL,
  so the concatenation silently produced NULL.  eligibility_notes remained NULL
  after the migration.  Fix: SET unconditionally with payee info leading.

ISSUE 3 — France TRIP: VFX 40% uplift mechanics wrong
  Current rate_tier_json and warnings_json describe the 40% rate as applying
  to "VFX expenditure".  This is incorrect.  Under TRIP (Article 220
  quaterdecies CGI) the 40% rate applies to ALL qualifying French expenditure
  once the VFX qualifying conditions are met (French VFX spend > €2M AND
  shot ratio requirements).  The 40% is not restricted to the VFX portion —
  it replaces the 30% rate on the entire qualifying French spend base.
  This distinction is strategically significant: on a €5M French spend,
  it is the difference between €1.5M (30%) and €2.0M (40%) rebate.

SOURCES
-------
CNC TRIP official programme notes; filmfrance.net; Article 220 quaterdecies CGI.
"""
from __future__ import annotations

import json
import sqlalchemy as sa
from alembic import op

revision = "f3g4h5i6j7k8"
down_revision = "e2f3g4h5i6j7"
branch_labels = None
depends_on = None

_UK_VFX_PROGRAM = "VFX Expenditure Credit (Uplift)"
_FRANCE_TRIP = "TRIP (Tax Rebate for International Production)"

# New UK VFX eligibility_notes — NET RATE first, within 240 chars,
# "CANNOT be combined with IFTC" preserved for mutual-exclusivity regex.
_UK_VFX_NOTES = (
    "NET RATE: 29.25% (39% gross less 25% UK corp tax) — always present net to "
    "investors. Stacks with AVEC on VFX budget portions. CANNOT be combined with "
    "IFTC. BFI cultural test required. VFX must be UK-performed. Available from "
    "1 Jan 2025."
)

# France TRIP eligibility_notes — payee first, within 240 chars.
_FRANCE_TRIP_NOTES = (
    "REBATE PAYEE: Paid to French PSC (société de production française), not the "
    "foreign producer directly. Foreign producer receives benefit via service "
    "contract. Structure correctly before principal photography."
)

# France TRIP corrected rate_tier_json — 40% applies to ALL French spend,
# not just VFX spend, once the VFX threshold conditions are met.
_FRANCE_TRIP_RATE_TIERS = json.dumps([
    {
        "label": "Standard qualifying spend",
        "rate_gross": 30,
    },
    {
        "label": (
            "VFX-intensive production: 40% applies to ALL qualifying French "
            "expenditure (not just VFX costs) when qualifying French VFX spend "
            "exceeds €2M AND ≥15% of shots are digitally processed or ≥1.5 "
            "digital shots/min of finished work"
        ),
        "rate_gross": 40,
    },
], ensure_ascii=False)

# France TRIP corrected VFX uplift warning.
_VFX_UPLIFT_WARNING_OLD = (
    "40% VFX UPLIFT OPPORTUNITY: rate increases from 30% to 40% when qualifying "
    "French VFX expenditure exceeds €2M. Period productions using digital set "
    "extension, digital crowd augmentation, or environment enhancement should "
    "model both scenarios — the uplift can meaningfully increase the total rebate."
)
_VFX_UPLIFT_WARNING_NEW = (
    "40% VFX UPLIFT OPPORTUNITY: when qualifying French VFX spend exceeds €2M "
    "(AND meets shot ratio requirements), the 40% rate applies to ALL qualifying "
    "French expenditure — not just the VFX portion. For a €5M French spend this "
    "is the difference between €1.5M (30%) and €2.0M (40%) rebate. Always model "
    "both scenarios for VFX-heavy or period productions with digital environments."
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. UK VFX — rewrite eligibility_notes with NET RATE first ────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = :notes,
            last_verified_at  = '2026-03-28'
        WHERE territory = 'United Kingdom'
          AND program   = :program
    """), {"notes": _UK_VFX_NOTES, "program": _UK_VFX_PROGRAM})

    # ── 2. France TRIP — set eligibility_notes (was NULL; cannot concatenate) ─
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = :notes,
            last_verified_at  = '2026-03-28'
        WHERE territory = 'France'
          AND program   = :program
    """), {"notes": _FRANCE_TRIP_NOTES, "program": _FRANCE_TRIP})

    # ── 3. France TRIP — correct rate_tier_json (40% on ALL French spend) ─────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_tier_json   = :tiers,
            last_verified_at = '2026-03-28'
        WHERE territory = 'France'
          AND program   = :program
    """), {"tiers": _FRANCE_TRIP_RATE_TIERS, "program": _FRANCE_TRIP})

    # ── 4. France TRIP — correct VFX uplift warning in warnings_json ──────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json    = REPLACE(warnings_json, :old_warn, :new_warn),
            last_verified_at = '2026-03-28'
        WHERE territory = 'France'
          AND program   = :program
          AND warnings_json LIKE '%40% VFX UPLIFT OPPORTUNITY%'
    """), {
        "old_warn": _VFX_UPLIFT_WARNING_OLD,
        "new_warn": _VFX_UPLIFT_WARNING_NEW,
        "program": _FRANCE_TRIP,
    })


def downgrade() -> None:
    conn = op.get_bind()

    # 1. UK VFX — restore original eligibility_notes
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = (
            '39% credit on qualifying UK VFX expenditure (from 1 January 2025 '
            'spend, claimable from 1 April 2025). This is a SEPARATE credit from '
            'AVEC — it can be COMBINED with AVEC on the same production for VFX '
            'portions of the budget. CANNOT be combined with IFTC — mutually '
            'exclusive. Must pass BFI cultural test. VFX work must be physically '
            'performed in the UK. NET RATE: 29.25% (gross 39% less 25% UK '
            'corporation tax) — present the net rate to investors, not the gross rate.'
        )
        WHERE territory = 'United Kingdom'
          AND program   = :program
    """), {"program": _UK_VFX_PROGRAM})

    # 2. France TRIP — remove eligibility_notes
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = NULL
        WHERE territory = 'France'
          AND program   = :program
    """), {"program": _FRANCE_TRIP})

    # 3. France TRIP — restore original rate_tier_json
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_tier_json = '[{"label": "Standard qualifying spend", "rate_gross": 30}, {"label": "VFX expenditure (requires: French VFX spend exceeding 2 million EUR AND at least 15% of shots digitally processed or at least 1.5 digitally processed shots per minute of finished work)", "rate_gross": 40}]'
        WHERE territory = 'France'
          AND program   = :program
    """), {"program": _FRANCE_TRIP})

    # 4. France TRIP — restore original VFX uplift warning
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = REPLACE(warnings_json, :new_warn, :old_warn)
        WHERE territory = 'France'
          AND program   = :program
          AND warnings_json LIKE '%40% VFX UPLIFT OPPORTUNITY%'
    """), {
        "old_warn": _VFX_UPLIFT_WARNING_OLD,
        "new_warn": _VFX_UPLIFT_WARNING_NEW,
        "program": _FRANCE_TRIP,
    })
