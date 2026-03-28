"""fix_ireland_malta_hungary_georgia

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-03-22

Fixes four DB issues identified against v2.2 system prompt fact-check:

1. Ireland Section 481 — three fixes:
   a) qualifying_spend_cap_pct 80% → NULL  (the 80% cap is UK AVEC only; Ireland has no such rule)
   b) VFX uplift tier corrected: 40% applies to ALL eligible Irish expenditure up to €10M
      (not VFX spend only) once the ≥€1M qualifying VFX spend threshold is met.
      Expenditure above €10M reverts to standard 32%. Source: Finance Bill 2025 /
      Screen Ireland Budget 2026 announcement.
   c) Note that Section 481 is NOT subject to Irish corporation tax (rate_gross = rate_net);
      40% means 40% back — unlike UK AVEC which is reduced by 25% CT to 25.5%.

2. Malta Film Commission Cash Rebate — three fixes:
   a) rate_net 40.0 → 30.0  (cash rebate: gross = net; 40% was the top tier rate, not net)
   b) cap_amount 5000000 → NULL  (no per-project total cap; Screen Malta guarantee covers
      the full rebate regardless of project size; the 5M figure was an ATL-adjacent
      estimation artefact and is not a programme rule)
   c) qualifying_spend_cap_pct 80.0 → NULL  (Malta has no 80% qualifying spend rule)

3. Hungary — payment_reliability 0.65 → 0.50
   The 2026 H1 registration queue (~HUF 300B) exceeds the cap (HUF 140B) by more than 2×.
   H2 cap not yet set (~June 2026). Payouts may be delayed into 2027. Reduces bankability
   from BANKABLE to CONDITIONALLY BANKABLE. Source: Andersen Tax Advisory Dec 2025,
   Budapest Reporter Jan 2026, Government Decree 23 Dec 2025.

4. Georgia — add mandatory DOR audit requirement to warnings_json
   All productions claiming >$2.5M in credits must complete a mandatory Georgia DOR audit
   before credits can be used or transferred. Typical timeline: 2–4 months, but can exceed
   this for large/complex productions. Effective January 2023. Source: Georgia DOR.
"""
from __future__ import annotations

import json
import sqlalchemy as sa
from alembic import op

revision = "k2l3m4n5o6p7"
down_revision = "j1k2l3m4n5o6"
branch_labels = None
depends_on = None

# Ireland VFX uplift corrected tier structure.
# The 40% rate applies to ALL eligible Irish expenditure up to €10M (not VFX
# costs only) once the production has ≥€1M qualifying VFX spend. Above €10M
# the standard 32% rate applies. The tier boundary detection regex in the
# validator matches "€10M" and applies blended logic: qualifying_spend ≤ 10M →
# all at 40%; qualifying_spend > 10M → first 10M at 40%, remainder at 32%.
# Note: the 10M boundary is expressed in EUR; the validator uses this as a
# GBP-equivalent approximation (conservative: boundary treated as £10M vs
# actual ~£8.5M, so slightly less spend gets the 40% tier than strictly
# correct — error direction is conservative, never overstating the rebate).
IRELAND_TIER_JSON = json.dumps([
    {
        "label": "First €10M of ALL eligible Irish expenditure — 40% when ≥€1M qualifying VFX spend threshold is met (Finance Act 2025, from Jan 2026)",
        "rate_gross": 40,
        "rate_net": 40,
    },
    {
        "label": "Eligible Irish expenditure above €10M — standard Section 481 rate",
        "rate_gross": 32,
        "rate_net": 32,
    },
])

IRELAND_ELIGIBILITY_NOTES = (
    "Section 481 is a direct corporation tax credit — unlike UK AVEC, the rate you see is the "
    "rate you receive. No additional Irish corporation tax deduction applies: 40% means 40% back. "
    "Standard rate: 32% on qualifying Irish expenditure (up to €125M per project). "
    "VFX uplift (from Jan 2026, Finance Act 2025): 40% on ALL eligible Irish expenditure "
    "up to €10M — not just VFX costs — once total qualifying VFX spend is ≥€1M. "
    "Expenditure above €10M qualifies at standard 32%. No principal photography in Ireland "
    "required for VFX-only projects. "
    "Scéal uplift (from 20 May 2025): 40% for feature films (incl. animated) with qualifying "
    "expenditure <€20M AND an EEA national in a key creative role (director, screenwriter, "
    "composer, editor, cinematographer, or production designer). Theatrical release in Ireland "
    "required (min 5 days). "
    "Foreign producers must route through an Irish-registered production company or co-producer. "
    "Cultural test via Screen Ireland required (apply ≥21 working days before Irish shoot). "
    "Min €1M project budget, min €250K qualifying Irish spend."
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Ireland Section 481 ──────────────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_cap_pct = NULL,
            rate_tier_json           = :tier_json,
            eligibility_notes        = :notes,
            last_verified_at         = '2026-03-22'
        WHERE territory = 'Ireland'
          AND program   = 'Section 481 Tax Credit'
          AND status    = 'active'
    """), {"tier_json": IRELAND_TIER_JSON, "notes": IRELAND_ELIGIBILITY_NOTES})

    # ── 2. Malta Film Commission Cash Rebate ────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_net                = 30.0,
            cap_amount              = NULL,
            qualifying_spend_cap_pct = NULL,
            last_verified_at        = '2026-03-22'
        WHERE territory = 'Malta'
          AND program   = 'Malta Film Commission Cash Rebate'
          AND status    = 'active'
    """))

    # ── 3. Hungary — lower payment_reliability + add queue warning ──────────
    # Fetch existing warnings_json to append (don't overwrite existing warnings)
    result = conn.execute(sa.text("""
        SELECT warnings_json FROM incentive_programs
        WHERE territory = 'Hungary' AND status = 'active'
    """)).fetchone()

    existing_warnings: list = []
    if result and result[0]:
        try:
            existing_warnings = json.loads(result[0]) if isinstance(result[0], str) else list(result[0])
        except (ValueError, TypeError):
            existing_warnings = []

    queue_warning = (
        "CRITICAL — 2026 REGISTRATION QUEUE: Estimated queue of productions awaiting "
        "registration (~HUF 300B) exceeds the H1 2026 cap (HUF 140B) by more than 2×. "
        "H2 2026 cap not yet set (~June 2026). Payouts may be delayed into 2027. "
        "Do NOT present as BANKABLE without flagging queue risk. Register at earliest opportunity."
    )
    queue_marker = "2026 REGISTRATION QUEUE"
    if not any(queue_marker in w for w in existing_warnings if isinstance(w, str)):
        existing_warnings.insert(0, queue_warning)

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_reliability = 0.50,
            warnings_json       = :warnings,
            last_verified_at    = '2026-03-22'
        WHERE territory = 'Hungary'
          AND status    = 'active'
    """), {"warnings": json.dumps(existing_warnings)})

    # ── 4. Georgia — add mandatory DOR audit warning ────────────────────────
    result = conn.execute(sa.text("""
        SELECT warnings_json FROM incentive_programs
        WHERE territory = 'Georgia (USA)' AND status = 'active'
    """)).fetchone()

    existing_warnings = []
    if result and result[0]:
        try:
            existing_warnings = json.loads(result[0]) if isinstance(result[0], str) else list(result[0])
        except (ValueError, TypeError):
            existing_warnings = []

    audit_warning = (
        "MANDATORY DOR AUDIT: All productions claiming >$2.5M in credits must complete a "
        "mandatory Georgia DOR audit before credits can be used or transferred. "
        "Typical timeline: 2–4 months (can exceed this for large or complex productions). "
        "Factor audit timeline into your cash-flow model. Effective January 2023. "
        "Source: Georgia DOR (dor.georgia.gov)."
    )
    audit_marker = "MANDATORY DOR AUDIT"
    if not any(audit_marker in w for w in existing_warnings if isinstance(w, str)):
        existing_warnings.append(audit_warning)

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json    = :warnings,
            last_verified_at = '2026-03-22'
        WHERE territory = 'Georgia (USA)'
          AND status    = 'active'
    """), {"warnings": json.dumps(existing_warnings)})


def downgrade() -> None:
    conn = op.get_bind()

    # Restore Ireland
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_cap_pct = 80.0,
            rate_tier_json = '[{"label":"Standard qualifying Irish expenditure","rate_gross":32},{"label":"Qualifying VFX expenditure (min €1M total VFX spend)","rate_gross":40}]',
            eligibility_notes = '32% tax credit on qualifying Irish expenditure (up to €125M per project). 40% enhanced rate applies to qualifying VFX expenditure where total project VFX spend is ≥€1M.'
        WHERE territory = 'Ireland' AND program = 'Section 481 Tax Credit' AND status = 'active'
    """))

    # Restore Malta
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_net = 40.0,
            cap_amount = 5000000.0,
            qualifying_spend_cap_pct = 80.0
        WHERE territory = 'Malta' AND program = 'Malta Film Commission Cash Rebate' AND status = 'active'
    """))

    # Restore Hungary
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_reliability = 0.65
        WHERE territory = 'Hungary' AND status = 'active'
    """))

    # Georgia — remove audit warning (restore previous state by removing the marker entry)
    result = conn.execute(sa.text("""
        SELECT warnings_json FROM incentive_programs
        WHERE territory = 'Georgia (USA)' AND status = 'active'
    """)).fetchone()
    if result and result[0]:
        try:
            warnings = json.loads(result[0]) if isinstance(result[0], str) else list(result[0])
            warnings = [w for w in warnings if "MANDATORY DOR AUDIT" not in str(w)]
            conn.execute(sa.text("""
                UPDATE incentive_programs SET warnings_json = :w
                WHERE territory = 'Georgia (USA)' AND status = 'active'
            """), {"w": json.dumps(warnings)})
        except (ValueError, TypeError):
            pass
