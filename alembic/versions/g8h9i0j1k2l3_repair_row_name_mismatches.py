"""repair_row_name_mismatches

Revision ID: g8h9i0j1k2l3
Revises: f7g8h9i0j1k2
Create Date: 2026-03-21 14:00:00.000000

Comprehensive repair of the incentive_programs table.

ROOT CAUSE
----------
Multiple prior migrations (i3j4k5l6m7n8, q1r2s3t4u5v6, t4u5v6w7x8y9,
b2c3d4e5f6g8, e6f7g8h9i0j1) targeted program names that DO NOT EXIST in the
database. The active rows use different program name variants (from the earlier
z1b2c3d4e5f6 migration), so every enrichment and fix applied in those
migrations had zero effect on the rows actually used by the report engine.

CONFIRMED ZERO-EFFECT MIGRATIONS (all fixes applied here instead)
------------------------------------------------------------------
- SA 'Foreign Film & TV Production Incentive' (all migrations since i3j4k5l6m7n8)
  → Active row is 'South Africa Film & TV Production Incentive'
- UK 'Audio Visual Expenditure Credit (AVEC)' (no hyphen, from i3j4k5l6m7n8)
  → Active row is 'Audio-Visual Expenditure Credit (AVEC)' (with hyphen)
- UK 'Independent Film Tax Credit (IFTC)' (from i3j4k5l6m7n8)
  → Active row is 'UK Independent Film Tax Credit (IFTC)'
- Hungary 'Hungarian Film Incentive' (from i3j4k5l6m7n8 / q1r2s3t4u5v6)
  → Active row is 'Hungarian Film Incentive (NFI)'
- Malta 'Malta Film Tax Incentive (MFTI)' → 'Malta Audio-Visual GIP Rebate'
  → Active row is 'Malta Film Commission Cash Rebate'

DB HYGIENE ISSUES FIXED
------------------------
1. Czech Republic: stale 'Czech Film Incentive Programme' (30%) shadows the
   corrected 'Czech Film Fund Incentive Programme' (25%) — _best_incentive()
   picks the 30% row, causing systematic rate overstatement. Deprecated.

2. Germany: stale 'German Federal Film Fund (DFFF)' (no cap) duplicates the
   correct 'DFFF (German Federal Film Fund)' (€5M cap). Deprecated.

3. Italy: stale 'Italy MiC Film Tax Credit' (no cap) duplicates the correct
   'Italian Tax Credit for Foreign Productions' (€20M cap). Deprecated.

4. Australia: 'Location Offset & PDV Offset (International)' is a superseded
   combined row; 'Location Offset (Foreign Productions)' is the correct row.
   Deprecated. 'Producer Offset' is domestic-only; nationality_requirements set.

5. United States: generic 'Georgia...' and 'New York...' rows duplicate the
   territory-specific Georgia (USA) and New York entries. Deprecated.

DATA APPLIED TO CORRECT ROWS
------------------------------
- South Africa: min spend ZAR 15M, R25M rebate cap, DTIC warnings,
  payment_reliability 0.25, eligibility rules (50% photography)
- UK AVEC: rate_tier_json (34% ATL / 25% BTL), eligibility_notes,
  qualifying_spend_cap_pct 80%
- UK IFTC: cap_amount £23.5M, eligibility_notes
- Hungary: vfx_uplift_pct 7.5, rate string update, eligibility_notes
- Malta: cap_amount €12.5M ATL, eligibility_notes, payment timeline
- Ireland: nationality_requirements → NULL (foreign producers accessible
  via Irish co-producer/SPV — previously locked to IE-only)
- Australia PDV Offset: rate_type tax_offset → cash_rebate
- UK VFX Expenditure Credit: INSERT missing row (39% VFX uplift)
"""
from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision = "g8h9i0j1k2l3"
down_revision = "f7g8h9i0j1k2"
branch_labels = None
depends_on = None


# ─── Stale rows to deprecate ────────────────────────────────────────────────

_DEPRECATE = [
    # Czech — stale 30% row shadows corrected 25% row
    ("Czech Republic", "Czech Film Incentive Programme"),
    # Germany — stale DFFF duplicate with no cap
    ("Germany", "German Federal Film Fund (DFFF)"),
    # Italy — stale MiC row with no cap
    ("Italy", "Italy MiC Film Tax Credit"),
    # Australia — superseded combined row; 'Location Offset (Foreign Productions)' is authoritative
    ("Australia", "Location Offset & PDV Offset (International)"),
    # Australia — domestic-only Producer Offset (set to AU-only below instead of deprecate)
    # United States — generic duplicates of territory-specific entries
    ("United States", "Georgia Entertainment Industry Investment Act"),
    ("United States", "New York State Film Tax Credit Program"),
]


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Deprecate stale duplicate rows ────────────────────────────────────
    for territory, program in _DEPRECATE:
        conn.execute(
            sa.text(
                "UPDATE incentive_programs SET status = 'deprecated', "
                "updated_at = NOW() "
                "WHERE territory = :t AND program = :p"
            ),
            {"t": territory, "p": program},
        )

    # ── 2. Australia Producer Offset: domestic-only, not for foreign producers ─
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET nationality_requirements = :nat, "
            "    eligibility_notes = :notes, "
            "    updated_at = NOW() "
            "WHERE territory = 'Australia' AND program = 'Producer Offset'"
        ),
        {
            "nat": '["AU"]',
            "notes": (
                "The Producer Offset (up to 40% for qualifying Australian films, 20% for TV) "
                "is exclusively available to Australian resident producers with significant "
                "Australian creative elements. Not accessible to foreign service productions. "
                "Foreign producers use the Location Offset (30%, AUD 20M min) instead."
            ),
        },
    )

    # ── 3. Australia PDV Offset: fix rate_type tax_offset → cash_rebate ──────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate_type = 'cash_rebate', "
            "    updated_at = NOW() "
            "WHERE territory = 'Australia' AND program = 'PDV Offset (Post, Digital & VFX)'"
        )
    )

    # ── 4. South Africa: apply ALL correct data to active row ─────────────────
    # All prior SA fixes (b2c3d4e5f6g8, e6f7g8h9i0j1, q1r2s3t4u5v6,
    # t4u5v6w7x8y9) targeted 'Foreign Film & TV Production Incentive' which
    # does not exist — apply consolidated correct data here.
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_min = :qs_min, "
            "    qualifying_spend_currency = 'ZAR', "
            "    rebate_cap_amount = :rebate_cap, "
            "    rebate_cap_currency = 'ZAR', "
            "    payment_reliability = 0.25, "
            "    payment_timeline_days_min = 270, "
            "    payment_timeline_days_max = 450, "
            "    payment_timeline_notes = :timeline_notes, "
            "    eligibility_rules_json = :rules, "
            "    warnings_json = :warnings, "
            "    source_name = 'DTIC South Africa', "
            "    source_url = 'https://www.dtic.gov.za/incentives/film-and-television', "
            "    eligibility_notes = :elig_notes, "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'South Africa' "
            "  AND program = 'South Africa Film & TV Production Incentive'"
        ),
        {
            "qs_min": 15_000_000.0,
            "rebate_cap": 25_000_000.0,
            "timeline_notes": (
                "9-15 months post-production. DTIC approval backlog frequently extends "
                "beyond 15 months — do not treat as investor-bankable cash flow."
            ),
            "rules": (
                '[{"rule":"Minimum qualifying South African spend of ZAR 15M","required":true},'
                '{"rule":"Minimum 50% of principal photography days must be in South Africa","required":true},'
                '{"rule":"Must use South African production services company (SPCV)","required":true},'
                '{"rule":"Application to DTIC before principal photography","required":true}]'
            ),
            "warnings": (
                '["R25M PER-PROJECT CAP: Maximum grant is R25 million per project regardless of '
                'budget size. A 25% rate applied to a £20M budget implies ~£5M rebate, but actual '
                'maximum is R25M (≈£1.05M at current rates). The cap is enforced in the financial '
                'model.",'
                '"DTIC PAYMENT DELAYS: Industry reports significant backlogs (12-24+ months) in DTIC '
                'grant processing as of early 2026. R600M–R1B in outstanding rebates reported. Do not '
                'include in investor cash-flow projections without DTIC pre-approval confirmation.",'
                '"Payment timeline 9-15 months — budget cash flow independently",'
                '"ZAR exchange rate volatility risk",'
                '"Minimum 50% of principal photography days must be in South Africa",'
                '"SPCV (Special Purpose Corporate Vehicle) must be registered in South Africa"]'
            ),
            "elig_notes": (
                "25% cash rebate on qualifying South African spend. Maximum R25M per project "
                "(enforced cap). Minimum qualifying spend ZAR 15M. Must use South African "
                "production services company. At least 50% of principal photography days in SA. "
                "Apply to DTIC before principal photography. "
                "DTIC payment backlog: do not model this rebate as investor-bankable cash flow."
            ),
        },
    )

    # ── 5. Ireland Section 481: clear nationality_requirements ───────────────
    # Foreign producers (non-IE) CAN access Section 481 via an Irish-registered
    # production company or co-producer. Locking to '["IE"]' causes the report
    # engine to filter this programme out for non-Irish producers.
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET nationality_requirements = NULL, "
            "    updated_at = NOW() "
            "WHERE territory = 'Ireland' AND program = 'Section 481 Tax Credit'"
        )
    )

    # ── 6. UK AVEC: apply enriched data (rate_tier_json, eligibility_notes) ───
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 34.0, "
            "    rate_net = 25.5, "
            "    rate_type = 'tax_credit', "
            "    rate_tier_json = :tier_json, "
            "    qualifying_spend_cap_pct = 80.0, "
            "    qualifying_spend_currency = 'GBP', "
            "    payment_timeline_days_min = 42, "
            "    payment_timeline_days_max = 56, "
            "    payment_timeline_notes = :timeline, "
            "    eligibility_rules_json = :rules, "
            "    eligibility_notes = :elig_notes, "
            "    warnings_json = :warnings, "
            "    source_name = 'HMRC / BFI', "
            "    source_url = 'https://www.gov.uk/guidance/corporation-tax-the-film-tax-relief', "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'United Kingdom' "
            "  AND program = 'Audio-Visual Expenditure Credit (AVEC)'"
        ),
        {
            "rate": "34% ATL + 25% BTL on qualifying UK expenditure",
            "tier_json": (
                '[{"label":"Above-the-Line qualifying spend (directors, writers, lead actors)",'
                '"rate_gross":34,"rate_net":25.5},'
                '{"label":"Below-the-Line qualifying spend (all other UK crew and facilities)",'
                '"rate_gross":25,"rate_net":18.75}]'
            ),
            "timeline": (
                "6-8 weeks from HMRC claim submission. "
                "BFI cultural test certification (12-16 weeks) required first."
            ),
            "rules": (
                '[{"rule":"Must pass BFI cultural test or qualify as official co-production","required":true},'
                '{"rule":"Minimum 10% core expenditure in UK","required":true},'
                '{"rule":"Company must be liable to UK corporation tax","required":true}]'
            ),
            "elig_notes": (
                "AVEC has TWO separate rates: 34% for Above-the-Line (ATL) qualifying "
                "expenditure (directors, writers, lead actors) and 25% for Below-the-Line "
                "(BTL) qualifying expenditure. The blended effective rate depends on the "
                "ATL/BTL budget split. AVEC and IFTC are mutually exclusive — choose one "
                "per project. Minimum 10% of core expenditure must be incurred in the UK. "
                "HETV strand (TV series): minimum £1M per broadcast hour qualifying UK spend."
            ),
            "warnings": (
                '["AVEC has two tiers: 34% ATL (directors/writers/lead actors) and 25% BTL — '
                'blended effective rate depends on your ATL/BTL budget split",'
                '"Mutually exclusive with IFTC — cannot claim both on same production",'
                '"BFI cultural test certification required before HMRC claim (allow 12-16 weeks)",'
                '"HETV strand requires minimum £1M qualifying UK spend per broadcast hour"]'
            ),
        },
    )

    # ── 7. UK IFTC: add cap_amount and eligibility_notes ─────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 53.0, "
            "    rate_net = 39.75, "
            "    rate_type = 'enhanced_tax_credit', "
            "    rate_tier_json = :tier_json, "
            "    cap_amount = 23500000.0, "
            "    cap_currency = 'GBP', "
            "    cap = 'Budget cap £23.5M', "
            "    qualifying_spend_cap_pct = 80.0, "
            "    qualifying_spend_currency = 'GBP', "
            "    payment_timeline_days_min = 42, "
            "    payment_timeline_days_max = 56, "
            "    eligibility_rules_json = :rules, "
            "    eligibility_notes = :elig_notes, "
            "    warnings_json = :warnings, "
            "    source_name = 'HMRC / BFI', "
            "    source_url = 'https://www.bfi.org.uk/apply-british-certification-tax-relief/independent-film-tax-credit', "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'United Kingdom' "
            "  AND program = 'UK Independent Film Tax Credit (IFTC)'"
        ),
        {
            "rate": "53% on first £15M, 34% above (gross)",
            "tier_json": (
                '[{"label":"First £15M qualifying UK spend","rate_gross":53,"rate_net":39.75},'
                '{"label":"Above £15M qualifying spend","rate_gross":34,"rate_net":25.5}]'
            ),
            "rules": (
                '[{"rule":"Budget cap of £23.5M","required":true},'
                '{"rule":"Theatrical release required (no direct-to-streaming)","required":true},'
                '{"rule":"Must pass BFI cultural test or qualify as official co-production","required":true},'
                '{"rule":"Minimum 10% core expenditure in UK","required":true},'
                '{"rule":"Company must be liable to UK corporation tax","required":true}]'
            ),
            "elig_notes": (
                "Separate programme from AVEC — for independent films with a total budget "
                "below £15M (budget cap £23.5M). Offers up to 53% on first £15M of "
                "qualifying UK spend, then 34% above. BFI Cultural Test required (min 18 "
                "of 35 points). Theatrical release required — no direct-to-streaming. "
                "IFTC and AVEC are mutually exclusive on the same production."
            ),
            "warnings": (
                '["Budget cap £23.5M — films above this threshold use AVEC instead",'
                '"Theatrical release required — direct-to-streaming titles do not qualify",'
                '"Mutually exclusive with AVEC",'
                '"BFI cultural test certification required (allow 12-16 weeks)"]'
            ),
        },
    )

    # ── 8. UK VFX Expenditure Credit: INSERT missing row ─────────────────────
    # Guarded: on a clean-replay database the enriched seed (i3j4k5l6m7n8) has
    # already inserted this row; only insert when genuinely missing (the state
    # this migration was written against).
    _vfx_exists = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM incentive_programs "
            "WHERE territory = 'United Kingdom' "
            "  AND program = 'VFX Expenditure Credit (Uplift)'"
        )
    ).scalar()
    if not _vfx_exists:
        vfx_id = str(uuid4())
        conn.execute(
            sa.text(
                """INSERT INTO incentive_programs (
                    id, territory, program, rate, rate_gross, rate_net, rate_type,
                    rate_tier_json, cap_amount, cap_currency, qualifying_spend_cap_pct,
                    qualifying_spend_currency, payment_timeline_days_min,
                    payment_timeline_days_max, payment_timeline_notes,
                    eligibility_rules_json, eligibility_notes, warnings_json,
                    source_name, source_url, currency, scope, status,
                    payment_reliability, last_verified_at, created_at, updated_at
                ) VALUES (
                    :id, 'United Kingdom', 'VFX Expenditure Credit (Uplift)',
                    '39% of qualifying UK VFX expenditure',
                    39.0, 29.25, 'tax_credit', NULL,
                    NULL, 'GBP', 80.0, 'GBP', 42, 56,
                    '6-8 weeks from HMRC claim. BFI certification required first.',
                    :rules, :elig_notes, :warnings,
                    'HMRC / BFI',
                    'https://www.gov.uk/guidance/corporation-tax-the-film-tax-relief',
                    'GBP', 'national', 'active', 0.92,
                    '2026-03-21', NOW(), NOW()
                )"""
            ),
            {
                "id": vfx_id,
                "rules": (
                    '[{"rule":"Applies only to qualifying UK core VFX expenditure","required":true},'
                    '{"rule":"Cannot combine with IFTC — mutually exclusive","required":true},'
                    '{"rule":"Must pass BFI cultural test","required":true}]'
                ),
                "elig_notes": (
                    "39% credit on qualifying UK VFX expenditure (effective net 29.25% after 25% "
                    "corporation tax). Mutually exclusive with IFTC — cannot claim both on the same "
                    "production. Can be combined with AVEC. Must pass BFI cultural test. "
                    "Applies to VFX work physically performed in the UK."
                ),
                "warnings": (
                    '["Mutually exclusive with IFTC — cannot claim both on same production",'
                    '"Can stack with AVEC on the same production",'
                    '"Applies to qualifying UK VFX expenditure only — overseas VFX does not qualify"]'
                ),
            },
        )

    # ── 9. Hungary: add vfx_uplift_pct, updated rate, eligibility_notes ───────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    vfx_uplift_pct = 7.5, "
            "    cap_per_person = 3000000.0, "
            "    cap_per_person_currency = 'HUF', "
            "    qualifying_spend_currency = 'HUF', "
            "    payment_timeline_days_min = 90, "
            "    payment_timeline_days_max = 180, "
            "    payment_timeline_notes = :timeline, "
            "    eligibility_rules_json = :rules, "
            "    eligibility_notes = :elig_notes, "
            "    warnings_json = :warnings, "
            "    source_name = 'NFI Hungary', "
            "    source_url = 'https://nfi.hu/en/filming-in-hungary', "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'Hungary' AND program = 'Hungarian Film Incentive (NFI)'"
        ),
        {
            "rate": "30% base (37.5% with qualifying VFX content)",
            "timeline": (
                "3-6 months after NFI audit completion. "
                "Queue risk when annual budget cap approached."
            ),
            "rules": (
                '[{"rule":"Must apply to NFI before principal photography","required":true},'
                '{"rule":"Cultural test or bilateral co-production treaty","required":true},'
                '{"rule":"Hungarian production service company required","required":true}]'
            ),
            "elig_notes": (
                "30% base rate on qualifying Hungarian expenditure. VFX uplift applies: "
                "37.5% total rate (30% + 7.5%) for productions where VFX content is "
                "moderate or high. No nationality restriction, no cultural test required "
                "for service productions — one of the most accessible incentives in Europe. "
                "HUF 3M per-person cap on above-the-line individual fees."
            ),
            "warnings": (
                '["HUF 3M per-person cap on individual above-the-line fees",'
                '"NFI annual budget cap ~HUF 70B — queue risk in busy years",'
                '"Payment may be delayed if NFI audit backlog",'
                '"VFX UPLIFT: 37.5% total rate (7.5% uplift) for qualifying VFX-heavy '
                'productions — verify VFX threshold with NFI before modelling"]'
            ),
        },
    )

    # ── 10. Malta: apply correct cap, eligibility_notes, payment timeline ─────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 40.0, "
            "    rate_net = 40.0, "
            "    rate_type = 'cash_rebate', "
            "    cap_amount = 12500000.0, "
            "    cap_currency = 'EUR', "
            "    cap = '€12.5M ATL expenditure cap', "
            "    qualifying_spend_currency = 'EUR', "
            "    qualifying_spend_cap_pct = 80.0, "
            "    payment_timeline_days_min = 90, "
            "    payment_timeline_days_max = 180, "
            "    payment_timeline_notes = :timeline, "
            "    eligibility_rules_json = :rules, "
            "    eligibility_notes = :elig_notes, "
            "    warnings_json = :warnings, "
            "    source_name = 'Malta Film Commission', "
            "    source_url = 'https://www.mfc.com.mt/filming-in-malta/incentives', "
            "    expiry_date = '2028-10-29', "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'Malta' AND program = 'Malta Film Commission Cash Rebate'"
        ),
        {
            "rate": "40% cash rebate on qualifying Malta expenditure (ATL + BTL)",
            "timeline": (
                "3-6 months after Malta Film Commission audit. "
                "Relatively fast by European standards."
            ),
            "rules": (
                '[{"rule":"Application to Malta Film Commission before production","required":true},'
                '{"rule":"Qualifying expenditure must be incurred in Malta","required":true},'
                '{"rule":"Cultural test or EU co-production qualification","required":true},'
                '{"rule":"International ATL paid outside Malta does not qualify unless talent is '
                'physically based and paid via Maltese entity","required":true}]'
            ),
            "elig_notes": (
                "40% rebate on ALL qualifying Malta spend — uniquely covers both ATL "
                "(non-resident lead actor fees, director) AND BTL crew costs at the same rate. "
                "Most other territories exclude or cap ATL. This makes Malta disproportionately "
                "valuable for productions with high lead actor costs. €12.5M ATL expenditure cap. "
                "Min €100K qualifying Malta spend. No nationality restriction. "
                "Programme expires 29 October 2028 — check renewal status for later productions."
            ),
            "warnings": (
                '["€12.5M ATL (above-the-line) expenditure cap — model carefully for productions '
                'with high lead actor/director fees",'
                '"Programme expires 29 October 2028 — verify renewal for productions after that date",'
                '"Limited local crew pool — key HODs may need to be imported",'
                '"ATL paid OUTSIDE Malta does not qualify unless talent is based and paid via '
                'Maltese entity — verify payroll structure with Malta Film Commission"]'
            ),
        },
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Remove UK VFX row
    conn.execute(
        sa.text(
            "DELETE FROM incentive_programs "
            "WHERE territory = 'United Kingdom' "
            "  AND program = 'VFX Expenditure Credit (Uplift)'"
        )
    )

    # Re-activate deprecated rows
    for territory, program in _DEPRECATE:
        conn.execute(
            sa.text(
                "UPDATE incentive_programs SET status = 'active' "
                "WHERE territory = :t AND program = :p"
            ),
            {"t": territory, "p": program},
        )

    # Restore Australia Producer Offset
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET nationality_requirements = NULL "
            "WHERE territory = 'Australia' AND program = 'Producer Offset'"
        )
    )

    # Restore Australia PDV Offset rate_type
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate_type = 'tax_offset' "
            "WHERE territory = 'Australia' AND program = 'PDV Offset (Post, Digital & VFX)'"
        )
    )

    # Restore Ireland nationality_requirements
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET nationality_requirements = '[\"IE\"]' "
            "WHERE territory = 'Ireland' AND program = 'Section 481 Tax Credit'"
        )
    )

    # Restore SA row (strip enriched data)
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_min = NULL, rebate_cap_amount = NULL, rebate_cap_currency = NULL, "
            "    payment_reliability = 0.55, eligibility_rules_json = NULL, warnings_json = NULL, "
            "    eligibility_notes = NULL "
            "WHERE territory = 'South Africa' "
            "  AND program = 'South Africa Film & TV Production Incentive'"
        )
    )
