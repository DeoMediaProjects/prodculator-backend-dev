"""fix_bc_pstc_notes_and_avec_tier

Revision ID: h5i6j7k8l9m0
Revises: g4h5i6j7k8l9
Create Date: 2026-03-28

ROOT CAUSE — BC PSTC filing requirement overstated
--------------------------------------------------
BC PSTC eligibility_notes = NULL.  The eligibility_rules_json entry
"Accredited production corporation registered with Creative BC required"
is technically correct but ambiguous — the AI paraphrases it as implying
a Canadian entity or co-producer is needed.

In reality: a foreign-owned company can register directly with Creative BC
as an accredited production corporation, provided it has a permanent
establishment in British Columbia.  No Canadian co-producer is required.
This is the core distinction from BC FIBC (which does require a
Canadian-controlled corporation).

The warnings_json already contains "Foreign-owned corporations are directly
eligible" but the AI re-states the eligibility_rules_json requirement in ways
that lose this nuance.  Adding an explicit eligibility_notes entry puts the
clarification at the HEAD of the requirements list (builder appends it first),
making it harder for the AI to overlook.

ROOT CAUSE — AVEC rate_tier_json rate_net inconsistency
-------------------------------------------------------
AVEC rate_tier_json has a single tier:
  {"label": "All qualifying UK expenditure...", "rate_gross": 34, "rate_net": 34}

The rate_net value 34 is wrong — net after 25% UK corporation tax is 25.5%.
The programme-level rate_net = 25.5 is correct.  The tier rate_net is never
read by the validator (requires ≥2 tiers) or the AI (builder does not include
rate_tier_json in the prompt skeleton), so this has zero functional impact.
Fixed here for data consistency.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h5i6j7k8l9m0"
down_revision = "g4h5i6j7k8l9"
branch_labels = None
depends_on = None

_BC_PSTC = "BC Production Services Tax Credit (PSTC)"
_AVEC = "Audio-Visual Expenditure Credit (AVEC)"

# BC PSTC eligibility_notes — under 240 chars so it survives prompt trimming.
_PSTC_NOTES = (
    "No Canadian co-producer required. Foreign-owned companies qualify by "
    "registering as an accredited production corporation with Creative BC \u2014 "
    "permanent BC establishment suffices. Distinct from BC FIBC which requires "
    "Canadian-controlled corps."
)

_AVEC_TIER_JSON = (
    '[{"label":"All qualifying UK expenditure (single flat rate)",'
    '"rate_gross":34,"rate_net":25.5}]'
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. BC PSTC — add eligibility_notes clarifying no co-producer needed ──
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = :notes,
            last_verified_at  = '2026-03-28'
        WHERE territory = 'British Columbia'
          AND program   = :program
    """), {"notes": _PSTC_NOTES, "program": _BC_PSTC})

    # ── 2. AVEC — fix rate_net in rate_tier_json (34 → 25.5) ─────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_tier_json   = :tiers,
            last_verified_at = '2026-03-28'
        WHERE territory = 'United Kingdom'
          AND program   = :program
    """), {"tiers": _AVEC_TIER_JSON, "program": _AVEC})


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = NULL
        WHERE territory = 'British Columbia'
          AND program   = :program
    """), {"program": _BC_PSTC})

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_tier_json = '[{"label":"All qualifying UK expenditure (single flat rate)","rate_gross":34,"rate_net":34}]'
        WHERE territory = 'United Kingdom'
          AND program   = :program
    """), {"program": _AVEC})
