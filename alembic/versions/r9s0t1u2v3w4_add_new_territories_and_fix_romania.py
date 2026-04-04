"""add_new_territories_and_fix_romania

Revision ID: r9s0t1u2v3w4
Revises: q8r9s0t1u2v3
Create Date: 2026-03-24

Adds 6 territories not previously in the DB and fixes one existing data error.

ADDITION: Netherlands, India, Singapore, Japan, South Korea, Belgium.
FIX: Romania qualifying_spend_min inconsistency (170000 RON → 100000 EUR).

----------------------------------------------------------------------
Territories added (all verified against official government/commission
sources, March 2026):

1. Netherlands — Film Production Incentive (FPI)
   35% on qualifying Dutch production costs. Max €3M per project.
   Min €1M total / €150K qualifying (feature); €250K / €100K (docs).
   Source: filmfonds.nl / filmcommission.nl

2. India — India Cine Hub Film Incentive
   40% on qualifying Indian production expenditure. +5% bonus each for
   ≥15% Indian labour or significant Indian content (SIC). Raised from
   30% to 40% in November 2023.
   Cap: INR 300M (INR 30 crore, ≈£2.9M). Min: INR 30M for features.
   Source: indiacinehub.gov.in; PIB press release 2023.

3. Singapore — IMDA Location Incentive
   40% cash rebate on qualifying local production expenses. Administered
   by the Infocomm Media Development Authority (IMDA).
   Source: IMDA (imda.gov.sg), mbrellafilms.com/incentives/singapore/

4. Japan — VIPO Japan Location Incentive
   50% on eligible Japan production costs. Cap ¥1B (≈£5.3M).
   Min qualifying Japan costs ¥200M (≈£1.06M).
   Competitive selection — limited annual slots. Extended to multi-year
   scheme from 2025.
   Source: vipo.or.jp; Deadline 2025-03.

5. South Korea — KOFIC Location Incentive
   25% for ≥10 shooting days + KRW 800M+ spend (≈£477K).
   20% for ≥3 shooting days + KRW 50M–800M spend (≈£30K–£477K).
   Cap: KRW 200M (≈£119K) — modest programme designed for location spend.
   Source: koreanfilm.or.kr

6. Belgium — Tax Shelter for Audiovisual Works
   ~42% of qualifying Belgian production expenditure delivered via the
   Federal Tax Shelter mechanism (investors shelter corporate tax through
   Belgian co-productions). NOT a direct government cash rebate — requires
   Belgian co-producer and signed framework agreement.
   Regional top-ups: Screen Flanders, Screen Brussels, Wallimage can add
   10-15 percentage points. Combined can exceed 50% of Belgian spend.
   Source: screenflanders.be; ep.com/production-incentives/europe/belgium/

----------------------------------------------------------------------
FIX: Romania qualifying_spend_min inconsistency
   Original seed (z3d4e5f6g7h8) stored 170000 with currency "RON".
   This represents RON 170,000 ≈ €34,000 — far below the official minimum.
   The comment in the seed ("~RON 1M") was incorrect; the intended value
   was likely €170,000. The OFIC official programme page states the minimum
   qualifying Romanian expenditure is €100,000.
   Fix: set qualifying_spend_min=100000, qualifying_spend_currency='EUR'.
   Source: ofic.ro/en/cash-rebate/
"""
from __future__ import annotations

import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "r9s0t1u2v3w4"
down_revision = "q8r9s0t1u2v3"
branch_labels = None
depends_on = None


# ── Seed data ─────────────────────────────────────────────────────────────────

_NEW_TERRITORIES = [
    # ══════════════════════════════════════════════════════════════════
    # NETHERLANDS
    # ══════════════════════════════════════════════════════════════════
    {
        "territory": "Netherlands",
        "program": "Netherlands Film Production Incentive (FPI)",
        "rate": "35% of qualifying Dutch production costs",
        "rate_gross": 35.0,
        "rate_net": 35.0,
        "rate_type": "cash_rebate",
        "rate_tier_json": None,
        "cap_amount": 3_000_000.0,
        "cap_currency": "EUR",
        "qualifying_spend_min": 150_000.0,
        "qualifying_spend_currency": "EUR",
        "payment_timeline_days_min": 90,
        "payment_timeline_days_max": 180,
        "payment_timeline_notes": (
            "3-6 months. Administered by Netherlands Film Fund (Filmfonds NL). "
            "Final claim after completion and audit."
        ),
        "eligibility_rules_json": json.dumps([
            {
                "rule": (
                    "Feature film: min €1M total production costs, €150K qualifying Dutch costs"
                ),
                "required": True,
            },
            {
                "rule": (
                    "Documentary: min €250K total production costs, €100K qualifying Dutch costs"
                ),
                "required": True,
            },
            {
                "rule": (
                    "At least 50% of production budget unconditionally committed in writing "
                    "by third parties at time of application"
                ),
                "required": True,
            },
        ]),
        "currency": "EUR",
        "warnings_json": json.dumps([
            "€3M cap per year per applicant production company",
            "Annual budget limited — apply early; competitive round",
            "Requires Dutch production company (foreign can engage Dutch service company)",
            "Qualifying costs are Dutch local spend only — not total budget",
        ]),
        "source_name": "Netherlands Film Fund (Filmfonds NL)",
        "source_url": "https://www.filmfonds.nl/en/funding/fund/netherlands-film-production-incentive",
        "status": "active",
        "scope": "national",
        "parent_territory": None,
        "stacking_group": None,
        "stackable_with": None,
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": json.dumps(
            ["GB", "BE", "DE", "FR", "LU", "AT", "IE", "AU", "CA", "US", "IL"]
        ),
        "spv_eligible": True,
        "payment_reliability": 0.75,
    },

    # ══════════════════════════════════════════════════════════════════
    # INDIA
    # ══════════════════════════════════════════════════════════════════
    {
        "territory": "India",
        "program": "India Cine Hub — Film Incentive Scheme",
        "rate": (
            "40% of qualifying Indian production expenditure "
            "(+5% bonus each for ≥15% Indian crew or Significant Indian Content)"
        ),
        "rate_gross": 40.0,
        "rate_net": 40.0,
        "rate_type": "cash_rebate",
        "rate_tier_json": json.dumps([
            {
                "label": "Base incentive on qualifying Indian production spend",
                "rate_gross": 40,
            },
            {
                "label": "Indian Labour Bonus (+5% for employing ≥15% Indian crew)",
                "rate_gross": 5,
            },
            {
                "label": (
                    "Significant Indian Content bonus (+5% for integrating approved SIC elements)"
                ),
                "rate_gross": 5,
            },
        ]),
        "cap_amount": 300_000_000.0,
        "cap_currency": "INR",
        "qualifying_spend_min": 30_000_000.0,
        "qualifying_spend_currency": "INR",
        "payment_timeline_days_min": 90,
        "payment_timeline_days_max": 270,
        "payment_timeline_notes": (
            "3-9 months. Administered by India Cine Hub (NFDC/Film Facilitation Office). "
            "Digital disbursement system launched 2025 — faster processing."
        ),
        "eligibility_rules_json": json.dumps([
            {
                "rule": (
                    "Minimum qualifying Indian production expenditure: "
                    "INR 30M (INR 3 crore) — waived for documentaries"
                ),
                "required": True,
            },
            {
                "rule": "Apply through India Cine Hub before commencement of Indian shoot",
                "required": True,
            },
            {
                "rule": (
                    "Production must be an international co-production or foreign-origin "
                    "content utilising Indian production services"
                ),
                "required": True,
            },
        ]),
        "currency": "INR",
        "warnings_json": json.dumps([
            (
                "CAP: INR 300M (INR 30 crore, ≈£2.9M / $3.6M) maximum per project — "
                "rate applies fully to India shoot costs; global productions with "
                "moderate India spend benefit most"
            ),
            "Raised from 30% to 40% in November 2023 — verify current guidelines with India Cine Hub",
            "INR/GBP exchange rate volatility — cap equivalent fluctuates significantly",
            "Strong infrastructure in Mumbai and Rajasthan; crew pool limited for specialist roles",
            "+5% Indian Labour bonus: must employ ≥15% of cast & crew as Indian nationals",
        ]),
        "source_name": "India Cine Hub (NFDC / Ministry of Information & Broadcasting)",
        "source_url": "https://indiacinehub.gov.in/40-incentive-scheme",
        "status": "active",
        "scope": "national",
        "parent_territory": None,
        "stacking_group": None,
        "stackable_with": None,
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": json.dumps(
            ["GB", "IT", "FR", "DE", "AU", "NZ", "CA", "BR", "IL", "RU", "JP", "KR"]
        ),
        "spv_eligible": True,
        "payment_reliability": 0.60,
    },

    # ══════════════════════════════════════════════════════════════════
    # SINGAPORE
    # ══════════════════════════════════════════════════════════════════
    {
        "territory": "Singapore",
        "program": "IMDA Location Incentive — Production Assistance",
        "rate": "40% cash rebate on qualifying local production expenses",
        "rate_gross": 40.0,
        "rate_net": 40.0,
        "rate_type": "cash_rebate",
        "rate_tier_json": None,
        "cap_amount": None,
        "cap_currency": "SGD",
        "qualifying_spend_min": None,
        "qualifying_spend_currency": "SGD",
        "payment_timeline_days_min": 60,
        "payment_timeline_days_max": 150,
        "payment_timeline_notes": (
            "2-5 months. Administered by IMDA (Infocomm Media Development Authority). "
            "Separate On-Screen Fund and Co-Production Fund also available (apply separately)."
        ),
        "eligibility_rules_json": json.dumps([
            {
                "rule": "Apply to IMDA before commencement of Singapore shoot",
                "required": True,
            },
            {
                "rule": "Production must spend on qualifying local Singapore crew, equipment, and facilities",
                "required": True,
            },
        ]),
        "currency": "SGD",
        "warnings_json": json.dumps([
            "40% applies to qualifying local (Singapore) production expenses only — not total budget",
            "IMDA also offers On-Screen Fund (30%, capped S$10M) and Co-Production Fund (50%, S$30M) — apply separately",
            "Crew pool is limited for specialist technical roles — most HODs may need to be imported",
            "IMDA applies production and content guidelines — verify project eligibility with IMDA before committing",
            "SGD/GBP rate volatility — verify cap equivalents at time of budgeting",
        ]),
        "source_name": "IMDA (Infocomm Media Development Authority, Singapore)",
        "source_url": "https://www.imda.gov.sg/how-we-can-help/media-talent-progression-programme",
        "status": "active",
        "scope": "national",
        "parent_territory": None,
        "stacking_group": None,
        "stackable_with": None,
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": json.dumps(
            ["GB", "AU", "FR", "DE", "IT", "IN", "CN", "KR", "JP", "NZ", "CA"]
        ),
        "spv_eligible": True,
        "payment_reliability": 0.75,
    },

    # ══════════════════════════════════════════════════════════════════
    # JAPAN
    # ══════════════════════════════════════════════════════════════════
    {
        "territory": "Japan",
        "program": "VIPO Japan Location Incentive",
        "rate": "50% of eligible Japan production costs",
        "rate_gross": 50.0,
        "rate_net": 50.0,
        "rate_type": "cash_rebate",
        "rate_tier_json": None,
        "cap_amount": 1_000_000_000.0,
        "cap_currency": "JPY",
        "qualifying_spend_min": 200_000_000.0,
        "qualifying_spend_currency": "JPY",
        "payment_timeline_days_min": 90,
        "payment_timeline_days_max": 270,
        "payment_timeline_notes": (
            "3-9 months. Administered by VIPO (Visual Industry Promotion Organisation) "
            "under METI. Competitive selection — approved projects receive dedicated support."
        ),
        "eligibility_rules_json": json.dumps([
            {
                "rule": (
                    "Minimum qualifying Japan production expenditure: ¥200M (≈£1.06M)"
                ),
                "required": True,
            },
            {
                "rule": "Must include Japan as a principal location (not purely post-production)",
                "required": True,
            },
            {
                "rule": "Apply to VIPO for selection before Japan shoot commences",
                "required": True,
            },
        ]),
        "currency": "JPY",
        "warnings_json": json.dumps([
            (
                "HIGHLY COMPETITIVE SELECTION: Only ~10 international productions approved per year. "
                "Acceptance is not guaranteed regardless of budget or eligibility. "
                "Apply early and engage a Japanese production service company."
            ),
            "Cap: ¥1B (≈£5.3M at 188 JPY/GBP) — generous ceiling but selection bottleneck is the main constraint",
            "Minimum qualifying Japan spend: ¥200M (≈£1.06M) — requires a significant Japan-shoot commitment",
            "Extended to multi-year scheme from 2025 with broader eligibility categories",
            "JPY/GBP volatility risk — budget with appropriate FX contingency",
            "Japanese crew rates are high by Asian standards; strong local infrastructure in Tokyo and Osaka",
        ]),
        "source_name": "VIPO (Visual Industry Promotion Organisation) / METI Japan",
        "source_url": "https://www.vipo.or.jp/en/location-project/",
        "status": "active",
        "scope": "national",
        "parent_territory": None,
        "stacking_group": None,
        "stackable_with": None,
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": json.dumps(
            ["GB", "FR", "DE", "IT", "KR", "AU", "IN", "CA", "US", "CN"]
        ),
        "spv_eligible": True,
        "payment_reliability": 0.70,
    },

    # ══════════════════════════════════════════════════════════════════
    # SOUTH KOREA
    # ══════════════════════════════════════════════════════════════════
    {
        "territory": "South Korea",
        "program": "KOFIC Location Incentive",
        "rate": "25% on qualifying Korean spend (≥10 shoot days + KRW 800M); 20% for shorter shoots",
        "rate_gross": 25.0,
        "rate_net": 25.0,
        "rate_type": "cash_rebate",
        "rate_tier_json": json.dumps([
            {
                "label": (
                    "Full tier: ≥10 shooting days in Korea + KRW 800M+ qualifying spend (≈£477K)"
                ),
                "rate_gross": 25,
            },
            {
                "label": (
                    "Reduced tier: ≥3 shooting days + KRW 50M–800M qualifying spend (≈£30K–£477K)"
                ),
                "rate_gross": 20,
            },
        ]),
        "cap_amount": 200_000_000.0,
        "cap_currency": "KRW",
        "qualifying_spend_min": 50_000_000.0,
        "qualifying_spend_currency": "KRW",
        "payment_timeline_days_min": 60,
        "payment_timeline_days_max": 180,
        "payment_timeline_notes": (
            "2-6 months. Administered by KOFIC (Korean Film Council). "
            "Applications processed after completion of Korea shoot."
        ),
        "eligibility_rules_json": json.dumps([
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
        ]),
        "currency": "KRW",
        "warnings_json": json.dumps([
            (
                "MODEST CAP: Maximum KRW 200M (≈£119K) — this is a location marketing incentive, "
                "not a major production rebate. Most value for productions with substantial Korea spend."
            ),
            "Subject to annual KOFIC budget availability — apply early each financial year",
            "25% rate requires ≥10 shoot days AND ≥KRW 800M qualifying spend",
            "KRW/GBP exchange rate volatility — cap equivalent fluctuates",
        ]),
        "source_name": "KOFIC (Korean Film Council)",
        "source_url": "http://www.koreanfilm.or.kr/eng/coProduction/locIncentive.jsp",
        "status": "active",
        "scope": "national",
        "parent_territory": None,
        "stacking_group": None,
        "stackable_with": None,
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": json.dumps(
            ["GB", "FR", "AU", "IN", "IT", "NZ", "ID", "VN", "TH"]
        ),
        "spv_eligible": True,
        "payment_reliability": 0.70,
    },

    # ══════════════════════════════════════════════════════════════════
    # BELGIUM — Tax Shelter
    # ══════════════════════════════════════════════════════════════════
    {
        "territory": "Belgium",
        "program": "Belgian Film Tax Shelter",
        "rate": (
            "~42% of qualifying Belgian production spend via federal Tax Shelter mechanism "
            "(regional top-ups: +10-15% via Screen Flanders, Screen Brussels, or Wallimage)"
        ),
        "rate_gross": 42.0,
        "rate_net": 42.0,
        "rate_type": "tax_shelter",
        "rate_tier_json": None,
        "cap_amount": None,
        "cap_currency": "EUR",
        "qualifying_spend_min": None,
        "qualifying_spend_currency": "EUR",
        "payment_timeline_days_min": 180,
        "payment_timeline_days_max": 365,
        "payment_timeline_notes": (
            "6-12 months. Framework agreement signed with Belgian co-producer; "
            "investors disburse tax shelter funds within 18 months (24 for animation). "
            "Timing depends on investor relationship."
        ),
        "eligibility_rules_json": json.dumps([
            {
                "rule": (
                    "Must be a European audiovisual work co-produced with a Belgian producer"
                ),
                "required": True,
            },
            {
                "rule": (
                    "The producing country of the foreign producer must have a co-production treaty "
                    "or bilateral agreement with Belgium (UK, France, Germany, Canada, etc.)"
                ),
                "required": True,
            },
            {
                "rule": (
                    "Eligible Belgian spend must be incurred in Belgium within 18 months "
                    "of signing the framework agreement"
                ),
                "required": True,
            },
        ]),
        "currency": "EUR",
        "warnings_json": json.dumps([
            (
                "NOT A DIRECT GOVERNMENT CASH REBATE: The Tax Shelter works via Belgian "
                "corporate investors sheltering their income tax through a co-production "
                "framework. The production receives funds from investors, not from the "
                "government directly. Requires Belgian co-production partner."
            ),
            (
                "COMBINED RATE CAN EXCEED 50%: Regional top-ups from Screen Flanders "
                "(Flanders), Screen Brussels (Brussels-Capital Region), or Wallimage "
                "(Wallonia) can add 10-15 percentage points on qualifying regional spend."
            ),
            "UK producers are eligible under the UK-Belgium bilateral arrangement",
            (
                "Qualifying Belgian spend includes shoot costs (crew, locations, equipment) "
                "AND post-production — both can attract Tax Shelter investment"
            ),
            "Framework agreement must be signed before eligible expenses are incurred",
        ]),
        "source_name": "Screen Flanders / Enterprise Belgium / EP Film Incentives",
        "source_url": "https://screenflanders.be/en/other-incentives/tax-shelter/",
        "status": "active",
        "scope": "national",
        "parent_territory": None,
        "stacking_group": None,
        "stackable_with": None,
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": json.dumps(
            ["GB", "FR", "DE", "LU", "NL", "AT", "IT", "ES", "IE", "CH",
             "CA", "AU", "IL", "TN", "CN"]
        ),
        "spv_eligible": True,
        "payment_reliability": 0.65,
    },
]

# Fields to upsert
_ALL_FIELDS = [
    "territory", "program", "rate", "rate_gross", "rate_net", "rate_type",
    "rate_tier_json", "cap_amount", "cap_currency", "qualifying_spend_min",
    "qualifying_spend_currency", "payment_timeline_days_min",
    "payment_timeline_days_max", "payment_timeline_notes",
    "eligibility_rules_json", "currency", "warnings_json",
    "source_name", "source_url", "status", "scope", "parent_territory",
    "stacking_group", "stackable_with", "nationality_requirements",
    "co_production_eligible", "co_production_treaties", "spv_eligible",
    "payment_reliability",
]

# Romania fix constants
_RO_OLD_MIN = 170_000.0
_RO_OLD_CURRENCY = "RON"
_RO_NEW_MIN = 100_000.0
_RO_NEW_CURRENCY = "EUR"
_RO_OLD_ELIG = '[{"rule":"Cultural points system — achievable for international productions using Romanian crew","required":false},{"rule":"Minimum RON 1,000,000 qualifying Romanian spend","required":true}]'
_RO_NEW_ELIG = json.dumps([
    {
        "rule": (
            "Minimum €100,000 qualifying Romanian spend (OFIC official requirement)"
        ),
        "required": True,
    },
    {
        "rule": "Cultural test — achievable for international productions using Romanian crew and locations",
        "required": False,
    },
    {
        "rule": "Apply to OFIC (Oficiul Național al Cinematografiei) before starting production in Romania",
        "required": True,
    },
])


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    # ── 1. Fix Romania qualifying_spend_min ───────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_min      = :new_min,
            qualifying_spend_currency = :new_currency,
            eligibility_rules_json    = :elig,
            last_verified_at          = '2026-03-24'
        WHERE territory = 'Romania'
    """), {
        "new_min": _RO_NEW_MIN,
        "new_currency": _RO_NEW_CURRENCY,
        "elig": _RO_NEW_ELIG,
    })

    # ── 2. Seed new territories ───────────────────────────────────────────────
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc).isoformat()

    for seed in _NEW_TERRITORIES:
        territory = seed["territory"]
        program = seed["program"]

        existing = conn.execute(
            sa.text(
                "SELECT id FROM incentive_programs "
                "WHERE territory = :territory AND program = :program LIMIT 1"
            ),
            {"territory": territory, "program": program},
        ).fetchone()

        if existing:
            # Update existing row
            set_parts = []
            params: dict = {"row_id": existing[0]}
            for f in _ALL_FIELDS:
                if f in ("territory", "program"):
                    continue
                set_parts.append(f"{f} = :{f}")
                params[f] = seed.get(f)
            params["last_verified_at"] = "2026-03-24"
            params["updated_at"] = now
            set_parts += ["last_verified_at = :last_verified_at", "updated_at = :updated_at"]
            conn.execute(
                sa.text(
                    f"UPDATE incentive_programs SET {', '.join(set_parts)} WHERE id = :row_id"
                ),
                params,
            )
        else:
            # Insert new row
            fields = [f for f in _ALL_FIELDS]
            placeholders = [f":{f}" for f in fields]
            params = {f: seed.get(f) for f in fields}
            params["last_verified_at"] = "2026-03-24"
            params["created_at"] = now
            params["updated_at"] = now
            params["id"] = str(uuid4())
            conn.execute(
                sa.text(
                    f"INSERT INTO incentive_programs "
                    f"({', '.join(fields)}, last_verified_at, created_at, updated_at, id) "
                    f"VALUES ({', '.join(placeholders)}, :last_verified_at, :created_at, :updated_at, :id)"
                ),
                params,
            )


def downgrade() -> None:
    conn = op.get_bind()

    # Remove newly added territories
    for territory in ("Netherlands", "India", "Singapore", "Japan", "South Korea", "Belgium"):
        conn.execute(
            sa.text("DELETE FROM incentive_programs WHERE territory = :t"),
            {"t": territory},
        )

    # Restore Romania qualifying_spend_min
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_min      = :old_min,
            qualifying_spend_currency = :old_currency,
            eligibility_rules_json    = :old_elig
        WHERE territory = 'Romania'
    """), {
        "old_min": _RO_OLD_MIN,
        "old_currency": _RO_OLD_CURRENCY,
        "old_elig": _RO_OLD_ELIG,
    })
