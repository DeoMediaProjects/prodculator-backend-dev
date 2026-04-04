"""fix_kofic_threshold_and_louisiana_cap_text

Revision ID: u3v4w5x6y7z8
Revises: t2u3v4w5x6y7
Create Date: 2026-03-29

ROOT CAUSE 1 — KOFIC full-rate qualifying spend threshold: KRW 800M → KRW 1B
-----------------------------------------------------------------------------
The seed (r9s0t1u2v3w4) set the full 25% tier threshold at KRW 800M
qualifying spend. Per KOFIC's official programme documentation (Korean Film
Biz Zone), the threshold for the full 25% rate is KRW 1 billion (₩1,000,000,000),
not KRW 800M. The KRW 800M figure is outdated.

Impact: At a modest ZAR 20M budget (~USD $1.1M), the production sits near this
threshold. Reporting the lower figure (KRW 800M) understates the qualifying
challenge — a production that believes it meets at ₩800M may not actually
qualify for the 25% rate.

Affected fields:
  - rate_tier_json tiers[0] label (mentions "KRW 800M")
  - warnings_json entry "25% rate requires ≥10 shoot days AND ≥KRW 800M qualifying spend"
  - rate field (human-readable description)
  - eligibility_rules_json (no entry for the spend threshold — add one)

Source: KOFIC / Korean Film Biz Zone — koreafilm.or.kr/biz

ROOT CAUSE 2 — Louisiana cap TEXT field: "$20M per-project" never updated
--------------------------------------------------------------------------
Migration m4n5o6p7q8r9 correctly set cap_amount = NULL (per-project cap
eliminated effective 1 July 2025, Act 323 / Act 44) and rewrote warnings_json.
But the cap TEXT field was never updated — same priority-chain pattern as
Spain (r0s1t2u3v4w5), Czech (t2u3v4w5x6y7).

Builder priority: rebate_cap_amount → cap TEXT → cap_amount. The stale
"Per-project cap $20M credit (feature film)" text overrides the NULL
cap_amount, so the report's Tax Incentive Analysis table still shows
"Per-project cap $20M credit (feature film)" — directly contradicting the
narrative section which correctly states the cap was removed.

Additionally: the review flagged that the Louisiana programme's eligibility
status for new 2027 applications is genuinely uncertain due to conflicting
legislative signals (HB 543 proposed terminating new applications July 1 2025;
LED's official page still describes the credit as active; Act 44 (June 2025)
made structural changes rather than terminating). The warnings_json must flag
this legislative uncertainty explicitly so the AI surfaces it in reports.

Source: Louisiana Legislature (HB 543); Louisiana Department of Revenue;
Louisiana Entertainment (LED official programme page); Act 44 (June 2025).

PORTUGAL NOTE — cap €1.5M vs €4M discrepancy
---------------------------------------------
The review flagged a possible discrepancy between the current DB value of
€1.5M (sourced from ICA official page by migration q8r9s0t1u2v3) and a
third-party source (Lisbonfilmworks) citing €4M. No change is made to the
cap_amount or cap TEXT here — the ICA official page is the authoritative
source. A note is added to warnings_json to flag that third-party sources
cite €4M and producers should verify directly with ICA before use in
investor documents.

Last Verified: 2026-03-29
"""
from __future__ import annotations

import json as _json

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "u3v4w5x6y7z8"
down_revision = "t2u3v4w5x6y7"
branch_labels = None
depends_on = None

# ── 1. KOFIC — update KRW 800M → KRW 1B threshold ───────────────────────────

_KOFIC_NEW_RATE = (
    "25% on qualifying Korean spend (≥10 shoot days + KRW 1B); "
    "20% for shorter shoots (≥3 days + KRW 50M–1B)"
)
_KOFIC_OLD_RATE = (
    "25% on qualifying Korean spend (≥10 shoot days + KRW 800M); "
    "20% for shorter shoots"
)

_KOFIC_NEW_TIER_JSON = _json.dumps([
    {
        "label": (
            "Full tier: ≥10 shooting days in Korea + KRW 1B+ qualifying spend "
            "(≈USD 900K) — 25% rate"
        ),
        "rate_gross": 25,
        "tier_type": "informational",
    },
    {
        "label": (
            "Reduced tier: ≥3 shooting days + KRW 50M–1B qualifying spend "
            "(≈USD 45K–900K) — 20% rate"
        ),
        "rate_gross": 20,
        "tier_type": "informational",
    },
])

_KOFIC_OLD_TIER_JSON = _json.dumps([
    {
        "label": (
            "Full tier: ≥10 shooting days in Korea + KRW 800M+ qualifying spend (≈£477K)"
        ),
        "rate_gross": 25,
        "tier_type": "informational",
    },
    {
        "label": (
            "Reduced tier: ≥3 shooting days + KRW 50M–800M qualifying spend (≈£30K–£477K)"
        ),
        "rate_gross": 20,
        "tier_type": "informational",
    },
])

_KOFIC_NEW_WARNINGS = _json.dumps([
    (
        "MODEST CAP: Maximum KRW 200M (≈USD 150K) — this is a location marketing incentive, "
        "not a full production rebate. Budget accordingly."
    ),
    "Subject to annual KOFIC budget availability — apply early each financial year",
    (
        "25% rate requires ≥10 shoot days AND ≥KRW 1B qualifying spend (≈USD 900K). "
        "Productions spending KRW 50M–1B qualify for the 20% reduced tier only."
    ),
    "KRW/USD exchange rate volatility — cap and threshold equivalents fluctuate",
])

_KOFIC_OLD_WARNINGS = _json.dumps([
    (
        "MODEST CAP: Maximum KRW 200M (≈£119K) — this is a location marketing incentive, "
        "not a full production rebate. Budget accordingly."
    ),
    "Subject to annual KOFIC budget availability — apply early each financial year",
    "25% rate requires ≥10 shoot days AND ≥KRW 800M qualifying spend",
    "KRW/GBP exchange rate volatility — cap equivalent fluctuates",
])

_KOFIC_NEW_RULES = _json.dumps([
    {
        "rule": "Minimum 3 shooting days in South Korea",
        "required": True,
    },
    {
        "rule": "Minimum KRW 50M qualifying Korean production expenditure (≈USD 45K) for 20% rate",
        "required": True,
    },
    {
        "rule": (
            "Minimum KRW 1B qualifying Korean production expenditure (≈USD 900K) "
            "required for full 25% rate"
        ),
        "required": False,
    },
    {
        "rule": "Apply to KOFIC before commencement of Korea shoot",
        "required": True,
    },
])

_KOFIC_OLD_RULES = _json.dumps([
    {
        "rule": "Minimum 3 shooting days in South Korea",
        "required": True,
    },
    {
        "rule": "Minimum KRW 50M qualifying Korean production expenditure (≈£30K)",
        "required": True,
    },
    {
        "rule": "Apply to KOFIC before commencement of Korea shoot",
        "required": True,
    },
])

# ── 2. Louisiana — correct cap TEXT and add legislative uncertainty warning ──

_LA_NEW_CAP = "No per-project cap (eliminated effective 1 July 2025); $125M annual state issuance cap"
_LA_OLD_CAP = "Per-project cap $20M credit (feature film)"

_LA_NEW_WARNINGS = _json.dumps([
    "Transferable credit — sold at approximately 85-88 cents on the dollar",
    (
        "PER-PROJECT CAP REMOVED (effective 1 July 2025): No per-project credit cap. "
        "Only the $125M annual state issuance cap applies — apply early in the calendar year."
    ),
    (
        "PROGRAMME ELIGIBILITY UNCERTAINTY: HB 543 proposed terminating new applications "
        "from July 1 2025. LED's official page describes the credit as active and Act 44 "
        "(June 2025) made structural changes rather than terminating it — but the "
        "conflicting legislative signals mean programme availability for 2027 productions "
        "must be verified directly with Louisiana Entertainment before budget finalisation."
    ),
    (
        "Rate structure: 25% base + up to 15% Louisiana resident payroll bonus + "
        "10% screenplay bonus (Louisiana-written) — maximum combined credit can exceed "
        "35% on qualifying Louisiana-resident labour spend."
    ),
    "Programme has been modified several times — verify current rules with Louisiana Entertainment before finalising budget",
])

_LA_OLD_WARNINGS = _json.dumps([
    "Transferable credit — sold at ~85-88 cents on the dollar",
    (
        "PER-PROJECT CAP REMOVED (effective 1 July 2025): No per-project credit cap. "
        "Only the $125M annual state issuance cap applies — apply early in the calendar year."
    ),
    (
        "Rate structure: 25% base + up to 15% Louisiana resident payroll bonus + "
        "10% screenplay bonus (Louisiana-written) — maximum combined credit can exceed "
        "35% on qualifying Louisiana-resident labour spend."
    ),
    "Programme has been modified several times — verify current rules with Louisiana Entertainment before finalising budget",
])

# ── 3. Portugal — add cap discrepancy note to warnings_json ─────────────────

_PT_NEW_WARNINGS = _json.dumps([
    "Annual fund is SMALL — €14M total per year, split into two phases (~€7M each)",
    "Fund frequently oversubscribed — apply as early as possible in the annual cycle",
    (
        "PER-PROJECT CAP: €1.5M maximum rebate per project (per ICA official Cash Rebate page). "
        "Note: some third-party sources cite €4M — verify the current cap directly with ICA "
        "before including in investor documents. "
        "Cash Refund is a separate non-combinable programme offering 30% on "
        "first €2M qualifying spend (min €2.5M budget) — apply separately via ICA."
    ),
    "Rate slides 25-30% based on cultural test score — 25% is the minimum",
    "Fiction/animation: €500K min qualifying spend; Documentaries: €250K",
])

_PT_OLD_WARNINGS = _json.dumps([
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


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. KOFIC — update rate, tiers, warnings, eligibility_rules_json ──────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate                   = :rate,
            rate_tier_json         = :tiers,
            warnings_json          = :warnings,
            eligibility_rules_json = :rules,
            last_verified_at       = '2026-03-29'
        WHERE territory = 'South Korea'
          AND program   = 'KOFIC Location Incentive'
          AND status    = 'active'
    """), {
        "rate": _KOFIC_NEW_RATE,
        "tiers": _KOFIC_NEW_TIER_JSON,
        "warnings": _KOFIC_NEW_WARNINGS,
        "rules": _KOFIC_NEW_RULES,
    })

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'South Korea' "
            "AND program = 'KOFIC Location Incentive' "
            "AND warnings_json LIKE '%1B%'"
        ),
        expected_min=1,
        migration_id=revision,
    )

    # ── 2. Louisiana — correct cap TEXT, update warnings ─────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap              = :cap,
            warnings_json    = :warnings,
            last_verified_at = '2026-03-29'
        WHERE territory = 'Louisiana'
          AND program ILIKE '%Louisiana Motion Picture%'
          AND status   = 'active'
    """), {"cap": _LA_NEW_CAP, "warnings": _LA_NEW_WARNINGS})

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Louisiana' "
            "AND cap LIKE '%annual state issuance cap%'"
        ),
        expected_min=1,
        migration_id=revision,
    )

    # ── 3. Portugal — add cap discrepancy note to warnings_json ──────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json    = :warnings,
            last_verified_at = '2026-03-29'
        WHERE territory = 'Portugal'
          AND status    = 'active'
    """), {"warnings": _PT_NEW_WARNINGS})

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Portugal' "
            "AND warnings_json LIKE '%third-party sources%'"
        ),
        expected_min=1,
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Restore KOFIC
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate                   = :rate,
            rate_tier_json         = :tiers,
            warnings_json          = :warnings,
            eligibility_rules_json = :rules
        WHERE territory = 'South Korea'
          AND program   = 'KOFIC Location Incentive'
          AND status    = 'active'
    """), {
        "rate": _KOFIC_OLD_RATE,
        "tiers": _KOFIC_OLD_TIER_JSON,
        "warnings": _KOFIC_OLD_WARNINGS,
        "rules": _KOFIC_OLD_RULES,
    })

    # Restore Louisiana cap TEXT and warnings
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap           = :cap,
            warnings_json = :warnings
        WHERE territory = 'Louisiana'
          AND program ILIKE '%Louisiana Motion Picture%'
          AND status   = 'active'
    """), {"cap": _LA_OLD_CAP, "warnings": _LA_OLD_WARNINGS})

    # Restore Portugal warnings
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = :warnings
        WHERE territory = 'Portugal'
          AND status    = 'active'
    """), {"warnings": _PT_OLD_WARNINGS})
