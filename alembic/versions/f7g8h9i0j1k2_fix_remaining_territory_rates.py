"""fix_remaining_territory_rates

Revision ID: f7g8h9i0j1k2
Revises: e6f7g8h9i0j1
Create Date: 2026-03-21 12:00:00.000000

Fixes confirmed rate and cap errors in six territories, identified through
systematic verification against official government and film commission sources.

CORRECTIONS (all verified against official sources, 2026-03-21)
--------------------------------------------------------------
1. Germany DFFF — cap_amount €4M → €5M
   Source: ffa.de — "maximum €5M per project" effective Feb 2025.
   The previous migration (d5e6f7g8h9i0) updated the rate to 30% but left
   cap_amount at the old €4M figure from the seed.

2. Romania CNC — rate 35% → 30%, add €10M eligible-spend cap
   Source: CNC Romania (cnc.ro) — current programme rate is 30% (not 35%
   as seeded in z3d4e5f6g7h8). Maximum eligible expenditure per project €10M.

3. Spain Art. 36.2 — add €10M cap, clear nationality_requirements
   Source: Ministerio de Cultura — maximum deduction €10M per production
   (Art. 36.2 LIS). nationality_requirements cleared: foreign producers access
   via Spanish co-producer or SPV regardless of nationality, not restricted to
   ES/EU.

4. Canary Islands — rebate cap €18M → €36M, add 54% enhanced ZEC tier
   Source: Canary Islands Film Commission / ZEC legislation (2023 update) —
   per-project maximum raised from €18M to €36M; enhanced 54% tier available
   via ZEC for qualifying complex productions.

5. Ireland Section 481 — eligible expenditure cap €70M → €125M, add VFX tier
   Source: Revenue Commissioners Ireland / Screen Ireland (2024 update) —
   maximum eligible Irish expenditure raised from €70M to €125M; 40% enhanced
   rate for productions with ≥€1M qualifying VFX expenditure.

6. Iceland — document two-tier structure (25% standard, 35% enhanced)
   Source: Icelandic Film Centre (icelandicfilmcentre.is) — standard rate 25%;
   enhanced 35% applies to productions with ISK 350M+ qualifying spend, at
   least 30 working days in Iceland, and at least 50 full-time Icelandic staff.
"""
from alembic import op
import sqlalchemy as sa

revision = "f7g8h9i0j1k2"
down_revision = "e6f7g8h9i0j1"
branch_labels = None
depends_on = None


# ─── Previous values (used in downgrade) ─────────────────────────────────────

_DE_OLD_CAP_AMOUNT = 4_000_000.0
_DE_OLD_CAP = "€4M per project (€10M for productions spending €25M+ in Germany)"
_DE_OLD_WARNINGS = (
    '["€4M standard cap per project — enhanced €10M cap only for productions spending €25M+ in Germany",'
    '"Grant, not automatic rebate — competitive application process",'
    '"Must apply before principal photography",'
    '"Annual budget limited — apply early in fiscal year"]'
)

_RO_OLD_RATE_GROSS = 35.0
_RO_OLD_RATE_NET = 35.0

_ES_OLD_NATIONALITY = '["ES","EU"]'

_CI_OLD_CAP_AMOUNT = 18_000_000.0
_CI_OLD_WARNINGS = (
    '["€18M maximum rebate cap per project",'
    '"Must establish Spanish entity or permanent establishment",'
    '"Canary Islands crew pool is limited — key HODs imported from mainland Spain or UK",'
    '"Enhanced rate (50%/45%) is for international productions only — Spanish domestic productions get lower rate"]'
)

_IE_OLD_CAP_AMOUNT = 70_000_000.0
_IE_OLD_WARNINGS = '["Requires Irish qualifying company (SPV acceptable)","Cultural test can take 4-8 weeks"]'


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Germany DFFF: cap €4M → €5M ───────────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_amount = :cap_amount, "
            "    cap_currency = 'EUR', "
            "    cap = :cap, "
            "    warnings_json = :warnings_json, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Germany' "
            "  AND program = 'DFFF (German Federal Film Fund)'"
        ),
        {
            "cap_amount": 5_000_000.0,
            "cap": "€5M per project",
            "warnings_json": (
                '["€5M cap per project (effective Feb 2025 — previous €4M cap superseded)",'
                '"Grant, not automatic rebate — competitive application process",'
                '"Must apply before principal photography",'
                '"Annual budget limited — apply early in fiscal year",'
                '"Rate applies to qualifying German production costs only, capped at 80% of total budget"]'
            ),
        },
    )

    # ── 2. Romania CNC: rate 35% → 30%, add €10M cap ─────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 30.0, "
            "    rate_net = 30.0, "
            "    cap_amount = :cap_amount, "
            "    cap_currency = 'EUR', "
            "    cap = :cap, "
            "    warnings_json = :warnings_json, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Romania'"
        ),
        {
            "rate": "30% of qualifying Romanian production expenditure",
            "cap_amount": 10_000_000.0,
            "cap": "€10M maximum eligible expenditure per project",
            "warnings_json": (
                '["Rate is 30% — previous DB entry incorrectly showed 35%",'
                '"€10M maximum eligible expenditure per project",'
                '"Cultural points system required — achievable for international productions using Romanian crew",'
                '"Payment timelines variable — verify current status with CNC Romania",'
                '"RON exchange rate risk — rebate paid in RON"]'
            ),
        },
    )

    # ── 3. Spain Art. 36.2: add €10M cap, clear nationality restriction ───────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_amount = :cap_amount, "
            "    cap_currency = 'EUR', "
            "    cap = :cap, "
            "    nationality_requirements = NULL, "
            "    warnings_json = :warnings_json, "
            "    eligibility_notes = :eligibility_notes, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Spain' "
            "  AND program = 'Spain General Tax Incentive for Film Production'"
        ),
        {
            "cap_amount": 10_000_000.0,
            "cap": "€10M maximum deduction per project",
            "warnings_json": (
                '["€10M maximum tax deduction per project",'
                '"Foreign producers must operate through a Spanish co-producer or registered SPV",'
                '"Tax credit — offset against Spanish corporate tax; timing depends on tax filing cycle",'
                '"Rate is tiered: 30% on first €1M qualifying spend, 25% above",'
                '"Cultural qualification as \'international cinematographic production\' required via ICAA"]'
            ),
            "eligibility_notes": (
                "Art. 36.2 LIS: 30% on first €1M of qualifying Spanish expenditure, 25% above. "
                "Open to all foreign productions (non-EU included) via Spanish co-producer or SPV. "
                "Maximum deduction €10M per project. Cultural qualification via ICAA required. "
                "Stackable with Canary Islands incentive (separate territorial programme)."
            ),
        },
    )

    # ── 4. Canary Islands: cap €18M → €36M, add 54% enhanced ZEC tier ────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_amount = :cap_amount, "
            "    cap_currency = 'EUR', "
            "    cap = :cap, "
            "    rate = :rate, "
            "    rate_tier_json = :rate_tier_json, "
            "    warnings_json = :warnings_json, "
            "    eligibility_notes = :eligibility_notes, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Canary Islands'"
        ),
        {
            "cap_amount": 36_000_000.0,
            "cap": "€36M per project (doubled from €18M in 2023 ZEC update)",
            "rate": "50% on first €1.8M + 45% above (up to 54% via ZEC for qualifying productions)",
            "rate_tier_json": (
                '[{"label":"First €1.8M qualifying Canary Islands spend","rate_gross":50},'
                '{"label":"Above €1.8M qualifying spend","rate_gross":45},'
                '{"label":"Enhanced ZEC tier (qualifying complex productions)","rate_gross":54}]'
            ),
            "warnings_json": (
                '["€36M maximum rebate cap per project (updated 2023 — previously €18M)",'
                '"Enhanced 54% rate available for qualifying productions via ZEC regime — verify eligibility with Canary Islands Film Commission",'
                '"Must establish Spanish entity or Canary Islands permanent establishment",'
                '"Canary Islands crew pool is limited — key HODs imported from mainland Spain or UK",'
                '"Enhanced 50%/45% rate is for international productions only"]'
            ),
            "eligibility_notes": (
                "50% on first €1.8M qualifying Canary Islands spend, 45% above. "
                "Maximum rebate €36M per project (raised from €18M in 2023 ZEC legislation update). "
                "An additional enhanced tier up to 54% is available through the ZEC (Zona Especial Canaria) "
                "production services regime for qualifying complex productions — verify threshold with "
                "Canary Islands Film Commission. Foreign producers require a Spanish entity or Canary "
                "Islands permanent establishment. Stackable with mainland Spain Art. 36.2."
            ),
        },
    )

    # ── 5. Ireland Section 481: cap €70M → €125M, add 40% VFX tier ──────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_amount = :cap_amount, "
            "    cap_currency = 'EUR', "
            "    cap = :cap, "
            "    rate_tier_json = :rate_tier_json, "
            "    eligibility_notes = :eligibility_notes, "
            "    warnings_json = :warnings_json, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Ireland' "
            "  AND program = 'Section 481 Tax Credit'"
        ),
        {
            "cap_amount": 125_000_000.0,
            "cap": "€125M maximum eligible Irish expenditure per project",
            "rate_tier_json": (
                '[{"label":"Standard qualifying Irish expenditure","rate_gross":32},'
                '{"label":"Qualifying VFX expenditure (min €1M total VFX spend)","rate_gross":40}]'
            ),
            "eligibility_notes": (
                "32% tax credit on qualifying Irish expenditure (up to €125M per project). "
                "40% enhanced rate applies to qualifying VFX expenditure where total project VFX spend "
                "is ≥€1M — verify current VFX tier criteria with Revenue Commissioners. "
                "Open to Irish and foreign co-producers with Irish spend. Foreign producers must route "
                "through an Irish-registered production company or co-producer. Cultural test via "
                "Screen Ireland required. Min €1M project budget, min €250K qualifying Irish spend. "
                "Cap raised from €70M to €125M effective 2024."
            ),
            "warnings_json": (
                '["Requires Irish qualifying company (SPV acceptable)",'
                '"Cultural test can take 4-8 weeks",'
                '"Cap raised €70M → €125M effective 2024",'
                '"40% VFX tier available for productions with ≥€1M qualifying VFX spend — verify with Revenue Commissioners"]'
            ),
        },
    )

    # ── 6. Iceland: document 25% / 35% two-tier structure ────────────────────
    # The standard programme was seeded at 25% (u5v6w7x8y9z0). The enhanced
    # 35% tier (ISK 350M+ spend, 30+ days, 50+ staff) must be documented.
    # rate_gross stays 25.0 (standard/default rate); rate_tier_json holds both.
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_tier_json = :rate_tier_json, "
            "    warnings_json = :warnings_json, "
            "    eligibility_notes = :eligibility_notes, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Iceland'"
        ),
        {
            "rate": "25% standard (35% enhanced for qualifying large productions)",
            "rate_tier_json": (
                '[{"label":"Standard rate — all qualifying productions","rate_gross":25},'
                '{"label":"Enhanced rate — ISK 350M+ qualifying spend, 30+ working days, '
                '50+ full-time Icelandic staff","rate_gross":35}]'
            ),
            "warnings_json": (
                '["35% ENHANCED TIER: ISK 350M+ qualifying Icelandic spend AND 30+ working days '
                'AND 50+ full-time Icelandic staff — verify with Ragna Filmkvóti / Icelandic Film Centre",'
                '"Standard rate is 25% for productions that do not meet all enhanced tier criteria",'
                '"Annual programme budget limited (~ISK 1.5B) — apply early",'
                '"Iceland crew pool is very small — all key departments likely imported",'
                '"Extreme seasonal daylight variation: 21h summer, 4h winter",'
                '"Weather highly unpredictable — significant contingency required"]'
            ),
            "eligibility_notes": (
                "Standard rate: 25% reimbursement on qualifying Icelandic production expenditure. "
                "Enhanced rate: 35% for productions meeting ALL three criteria: "
                "(1) ISK 350M+ qualifying Icelandic spend, "
                "(2) at least 30 working days principal photography in Iceland, "
                "(3) at least 50 full-time Icelandic staff. "
                "Apply to Ragna Filmkvóti (Icelandic Film Centre) before production commences. "
                "Annual programme budget ~ISK 1.5B — early application advised."
            ),
        },
    )


def downgrade() -> None:
    conn = op.get_bind()

    # ── 1. Germany DFFF: restore €4M cap ─────────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_amount = :cap_amount, "
            "    cap = :cap, "
            "    warnings_json = :warnings_json "
            "WHERE territory = 'Germany' "
            "  AND program = 'DFFF (German Federal Film Fund)'"
        ),
        {
            "cap_amount": _DE_OLD_CAP_AMOUNT,
            "cap": _DE_OLD_CAP,
            "warnings_json": _DE_OLD_WARNINGS,
        },
    )

    # ── 2. Romania: restore 35%, remove cap ──────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = :rate_gross, "
            "    rate_net = :rate_net, "
            "    cap_amount = NULL, "
            "    cap = NULL, "
            "    warnings_json = NULL "
            "WHERE territory = 'Romania'"
        ),
        {
            "rate": "35% of qualifying Romanian production expenditure",
            "rate_gross": _RO_OLD_RATE_GROSS,
            "rate_net": _RO_OLD_RATE_NET,
        },
    )

    # ── 3. Spain: restore nationality_requirements, remove cap ───────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_amount = NULL, "
            "    cap = 'No formal per-project cap (annual budget limited)', "
            "    nationality_requirements = :nationality "
            "WHERE territory = 'Spain' "
            "  AND program = 'Spain General Tax Incentive for Film Production'"
        ),
        {"nationality": _ES_OLD_NATIONALITY},
    )

    # ── 4. Canary Islands: restore €18M cap, remove enhanced tier ────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_amount = :cap_amount, "
            "    cap = '€18M max rebate per project', "
            "    rate = '50% on first €1M + 45% above €1M (international productions)', "
            "    rate_tier_json = :rate_tier_json, "
            "    warnings_json = :warnings_json "
            "WHERE territory = 'Canary Islands'"
        ),
        {
            "cap_amount": _CI_OLD_CAP_AMOUNT,
            "rate_tier_json": (
                '[{"label":"First €1M qualifying spend","rate_gross":50},'
                '{"label":"Above €1M qualifying spend","rate_gross":45}]'
            ),
            "warnings_json": _CI_OLD_WARNINGS,
        },
    )

    # ── 5. Ireland: restore €70M cap, remove VFX tier ────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_amount = :cap_amount, "
            "    cap = '€70M eligible expenditure cap per project', "
            "    rate_tier_json = NULL, "
            "    warnings_json = :warnings_json "
            "WHERE territory = 'Ireland' "
            "  AND program = 'Section 481 Tax Credit'"
        ),
        {
            "cap_amount": _IE_OLD_CAP_AMOUNT,
            "warnings_json": _IE_OLD_WARNINGS,
        },
    )

    # ── 6. Iceland: restore single-tier 25% ──────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '25% of qualifying Icelandic production costs', "
            "    rate_tier_json = NULL, "
            "    eligibility_notes = NULL "
            "WHERE territory = 'Iceland'"
        )
    )
