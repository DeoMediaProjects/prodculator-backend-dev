"""fix_portugal_cap_and_italy_reform

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
Create Date: 2026-03-24

Fixes two confirmed data errors identified by official-source verification:

1. Portugal — Cash Rebate per-project cap: €6M → €1.5M
   Migration h9i0j1k2l3m4 set cap_amount=6,000,000 EUR described as a
   "high-budget instrument" cap. Verification against the ICA official Cash
   Rebate page (ica-ip.pt/en/1-4-5/cash-rebate/) confirms the per-project
   maximum is €1,500,000. The €6M figure cannot be sourced.
   The rate_tier_json entry for a "high-budget €6M instrument" is also removed;
   the correct second programme is the "Cash Refund" (separate, non-combinable),
   which provides 30% on the first €2M of qualifying spend (max ≈ €600K rebate).
   Source: ICA official Cash Rebate page (accessed 2026-03-24).

2. Italy — 2025 reform makes rate variable (15-40%), no longer fixed at 40%
   Under the Inter-ministerial Decree of 10 July 2024 (effective 2025), the
   Italian Tax Credit for Foreign Productions is no longer a flat 40%.  The
   rate now varies between 15% and 40% depending on cultural impact score,
   project quality criteria, and European production involvement, as determined
   by ministerial decree.  The 40% ceiling is still achievable for top-scoring
   productions but is not guaranteed.
   rate_gross remains at 40.0 (headline/maximum) but a mandatory warning is
   added and eligibility_notes updated.
   Source: Clovers Law (2025-04-28); Celluloid Junkie (2025-07-16); MiC decree
   of 10 July 2024.
"""
from __future__ import annotations

import json
import sqlalchemy as sa
from alembic import op

revision = "q8r9s0t1u2v3"
down_revision = "p7q8r9s0t1u2"
branch_labels = None
depends_on = None


# ── Portugal constants ─────────────────────────────────────────────────────────

_PT_OLD_CAP_AMOUNT = 6_000_000.0
_PT_NEW_CAP_AMOUNT = 1_500_000.0

_PT_OLD_CAP_TEXT = "€6M per feature / €3M per episode (high-budget instrument)"
_PT_NEW_CAP_TEXT = "€1.5M per project (ICA Cash Rebate official maximum)"

_PT_OLD_TIER_JSON = json.dumps([
    {"label": "FATC standard rate (cultural test score dependent)", "rate_gross": 25},
    {"label": "FATC enhanced rate (high cultural test score)", "rate_gross": 30},
    {"label": "High-budget 30% instrument (€2.5M+ budget, separate fund)", "rate_gross": 30},
])

_PT_NEW_TIER_JSON = json.dumps([
    {"label": "Standard Cash Rebate (cultural test score 50-74)", "rate_gross": 25},
    {"label": "Enhanced Cash Rebate (cultural test score 75+)", "rate_gross": 30},
    {
        "label": (
            "Cash Refund — SEPARATE non-combinable programme: 30% on first €2M qualifying "
            "spend (min €2.5M production budget; max rebate ≈ €600K)"
        ),
        "rate_gross": 30,
    },
])

_PT_OLD_WARNINGS = json.dumps([
    "Annual FATC fund is SMALL — €14M total per year, split into two phases (~€7M each)",
    "Fund is frequently oversubscribed — apply as early as possible in the annual cycle",
    "High-budget 30% instrument: separate €2.5M+ budget threshold; €6M cap/feature, €3M/episode",
    "Rate slides 25-30% based on cultural test score — 25% is the minimum",
    "Fiction/animation: €500K min qualifying spend; Documentaries: €250K",
])

_PT_NEW_WARNINGS = json.dumps([
    "Annual fund is SMALL — €14M total per year, split into two phases (~€7M each)",
    "Fund frequently oversubscribed — apply as early as possible in the annual cycle",
    (
        "PER-PROJECT CAP: €1.5M maximum rebate per project (ICA official). "
        "Cash Refund is a separate non-combinable programme offering 30% on "
        "first €2M qualifying spend (min €2.5M budget) — apply separately via ICA."
    ),
    "Rate slides 25-30% based on cultural test score — 25% is the minimum",
    "Fiction/animation: €500K min qualifying spend; Documentaries: €250K",
])

_PT_NEW_ELIG_NOTES = (
    "Cash Rebate (ICA): 25-30% sliding scale on qualifying Portuguese expenditures, "
    "based on cultural test score. Annual fund €14M split into two phases. "
    "Per-project maximum: €1.5M. "
    "Minimum qualifying spend: €500K (fiction/animation), €250K (documentary/post). "
    "Fund is competitive and frequently oversubscribed — apply at phase opening. "
    "SEPARATE programme — Cash Refund: 30% on first €2M qualifying spend, "
    "minimum €2.5M budget. Cannot be combined with Cash Rebate for same production."
)


# ── Italy constants ────────────────────────────────────────────────────────────

_IT_OLD_WARNINGS = json.dumps([
    "€20M per-project cap",
    "Transferable tax credit — can be sold to Italian entities at ~90-92 cents on the euro",
    "Italian executive producer requirement — must engage local partner",
    "Cultural test via MiC can take 4-8 weeks",
])

_IT_NEW_WARNINGS = json.dumps([
    (
        "2025 REFORM — RATE NOW VARIABLE: Under the MiC Inter-ministerial Decree of "
        "10 July 2024 (effective 2025), the Italian Tax Credit is no longer a fixed 40%. "
        "The rate now varies 15-40% based on cultural impact score, project quality, "
        "and European production involvement. The 40% rate applies only to top-scoring "
        "productions. Verify applicable rate with MiC or Italian production partner "
        "before budgeting."
    ),
    "€20M per-year-per-company cap (post-2025 reform)",
    "Transferable tax credit — can be sold to Italian entities at ~90-92 cents on the euro",
    "Italian executive producer and local executive producer required — must engage partner",
    "Cultural test via MiC can take 4-8 weeks; reform introduced stricter requirements",
])

_IT_NEW_ELIG_NOTES = (
    "Italian Tax Credit for Foreign Productions: 40% maximum on qualifying Italian "
    "expenditure (variable 15-40% under 2025 reform — not guaranteed). "
    "Requires Italian production service company partner, cultural test approval via MiC. "
    "Italian spend must not exceed 60% of total budget. ATL costs eligible up to 30% of "
    "production costs. Cap: €20M per year per company."
)

_IT_NEW_RATE = "Up to 40% of qualifying Italian expenditure (variable 15-40% under 2025 MiC reform)"


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Portugal: correct cap €6M → €1.5M, update tier JSON and warnings ──
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount       = :cap_amount,
            cap              = :cap_text,
            rate_tier_json   = :tier_json,
            warnings_json    = :warnings,
            eligibility_notes = :elig_notes,
            last_verified_at = '2026-03-24'
        WHERE territory = 'Portugal'
    """), {
        "cap_amount": _PT_NEW_CAP_AMOUNT,
        "cap_text": _PT_NEW_CAP_TEXT,
        "tier_json": _PT_NEW_TIER_JSON,
        "warnings": _PT_NEW_WARNINGS,
        "elig_notes": _PT_NEW_ELIG_NOTES,
    })

    # ── 2. Italy: add 2025 reform warning, update rate description ────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate             = :rate,
            warnings_json    = :warnings,
            eligibility_notes = :elig_notes,
            last_verified_at = '2026-03-24'
        WHERE territory = 'Italy'
          AND program = 'Italian Tax Credit for Foreign Productions'
    """), {
        "rate": _IT_NEW_RATE,
        "warnings": _IT_NEW_WARNINGS,
        "elig_notes": _IT_NEW_ELIG_NOTES,
    })


def downgrade() -> None:
    conn = op.get_bind()

    # Restore Portugal €6M cap
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount       = :cap_amount,
            cap              = :cap_text,
            rate_tier_json   = :tier_json,
            warnings_json    = :warnings
        WHERE territory = 'Portugal'
    """), {
        "cap_amount": _PT_OLD_CAP_AMOUNT,
        "cap_text": _PT_OLD_CAP_TEXT,
        "tier_json": _PT_OLD_TIER_JSON,
        "warnings": _PT_OLD_WARNINGS,
    })

    # Restore Italy old warnings (remove reform notice)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate          = '40% of qualifying Italian expenditure',
            warnings_json = :warnings
        WHERE territory = 'Italy'
          AND program = 'Italian Tax Credit for Foreign Productions'
    """), {
        "warnings": _IT_OLD_WARNINGS,
    })
