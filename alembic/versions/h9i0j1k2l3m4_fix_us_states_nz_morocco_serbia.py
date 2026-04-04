"""fix_us_states_nz_morocco_serbia

Revision ID: h9i0j1k2l3m4
Revises: g8h9i0j1k2l3
Create Date: 2026-03-21 16:00:00.000000

Fixes confirmed rate errors and programme updates identified through systematic
web research against official government and film commission sources (2026-03-21).

CORRECTIONS
-----------
1. Morocco CCM — rate 20% → 30%
   Rate was raised from 20% to 30% in March 2022 and remains current for
   2025-2026. The DB had the pre-2022 rate. Source: ccm.ma

2. California — rate 22% → 35% (Program 4.0, effective July 1, 2025)
   AB 132 created Program 4.0: 35% base refundable credit (up from 20-25%
   in Program 3.0). First refundable credit since 2009. Credit applies to
   first $120M qualified expenditures ($42M max credit value).
   Separate $20M cap for independent films.
   Source: film.ca.gov/tax-credit/the-basics-4-0/

3. New York State — rate 25% → 30% (restored base rate)
   Base rate restored to 30%; +10% upstate uplift makes max 40%.
   Annual programme cap increased to $800M.
   Source: esd.ny.gov

4. Illinois — rate 30% → 35% (IL-resident labour/vendor, from July 2025)
   30% → 35% for Illinois resident payroll and qualified vendor spend.
   Non-resident labour stays 30% (up to 13 non-resident employees).
   Source: dceo.illinois.gov

5. New Mexico — correct rate structure, increase cap to 40%
   Base is 25%; maximum 40% with stacked uplifts:
     +5% TV series/pilot; +5% qualified soundstage; +10% rural filming.
   Annual programme cap $140M (FY2026). Non-resident performing artists
   capped at $5M per production. Source: nmfilm.com

6. New Zealand NZSPG — min spend NZD $15M → $4M; add PDV uplift tier
   Min spend for live-action dropped from NZD $15M to $4M (Jan 1, 2026).
   New 5% PDV uplift from Jan 1, 2026 makes PDV-only total up to 25%.
   Source: nzfilm.co.nz

7. Serbia — add 30% large-production tier (>€5M local spend)
   Standard rate is 25% (€300K+ qualifying spend).
   30% tier for productions with qualifying Serbian expenditure >€5M.
   Source: filminserbia.com (March 2025 decree)
"""
from alembic import op
import sqlalchemy as sa

revision = "h9i0j1k2l3m4"
down_revision = "g8h9i0j1k2l3"
branch_labels = None
depends_on = None


# ─── Previous values (used in downgrade) ─────────────────────────────────────

_MA_OLD_RATE_GROSS = 20.0
_CA_OLD_RATE_GROSS = 22.0
_CA_OLD_CAP = 17_000_000.0
_NY_OLD_RATE_GROSS = 25.0
_IL_OLD_RATE_GROSS = 30.0
_NM_OLD_RATE_GROSS = 35.0
_NZ_OLD_MIN = 15_000_000.0


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Morocco CCM: 20% → 30% ────────────────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 30.0, "
            "    rate_net = 30.0, "
            "    qualifying_spend_min = 570000.0, "  # MAD 10M ≈ £570K at current rate
            "    warnings_json = :warnings, "
            "    eligibility_notes = :elig_notes, "
            "    source_url = 'https://ccm.ma/foreign_production/pe/index.html', "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Morocco'"
        ),
        {
            "rate": "30% of qualifying local Moroccan expenditure",
            "warnings": (
                '["Rate raised from 20% to 30% in March 2022 — reports using the 20% figure are outdated",'
                '"Minimum budget MAD 10M (~$1M USD) and minimum 18 shooting days in Morocco required",'
                '"No per-project cap (MAD 18M cap was removed in March 2022)",'
                '"Annual fund competitive — verify availability with CCM before committing"]'
            ),
            "elig_notes": (
                "30% cash rebate on eligible local Moroccan expenditures (raised from 20% March 2022). "
                "Minimum budget of MAD 10M (~$1M USD). Minimum 18 shooting days in Morocco. "
                "No per-project cap. Eligible expenses: local crew/talent, hotels, transport, "
                "studios, equipment rental, art department. Apply through CCM before production."
            ),
        },
    )

    # ── 2. California: 22% → 35% (Program 4.0, effective July 1, 2025) ───────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 35.0, "
            "    rate_net = 35.0, "
            "    rate_type = 'refundable_tax_credit', "
            "    cap_amount = 42000000.0, "  # 35% × $120M max qualified expenditure
            "    cap_currency = 'USD', "
            "    cap = '$42M per production (35% of first $120M qualified expenditure)', "
            "    qualifying_spend_min = 1000000.0, "
            "    warnings_json = :warnings, "
            "    eligibility_notes = :elig_notes, "
            "    source_url = 'https://film.ca.gov/tax-credit/the-basics-4-0/', "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'California' "
            "  AND program = 'California Film & Television Tax Credit (Program 4.0)'"
        ),
        {
            "rate": "35% of qualified CA expenditure (up to $120M qualifying spend per project)",
            "warnings": (
                '["Program 4.0 effective July 1, 2025 — previous DB entry reflected old Program 3.0 (20-25%)",'
                '"35% base rate; uplifts available: +2-5% targeted hiring, +5% relocation, +5% filming outside 30-mile LA zone",'
                '"Credit refundable for first time since 2009 — 90% paid over 5 years if refundability elected",'
                '"Credit applies to first $120M qualified expenditure ($42M max credit value); independent films capped at $20M qualified",'
                '"Annual allocation $750M/year for 5 years — competitive, allocated by California Film Commission",'
                '"Must be allocated before production begins"]'
            ),
            "elig_notes": (
                "California Program 4.0 (AB 132): 35% base refundable tax credit on qualified CA "
                "expenditures, effective July 1, 2025. Credit applies to first $120M of qualified "
                "expenditures (max credit $42M per production). Independent films: first $20M. "
                "Additional uplifts: +2-5% for targeted hiring programs, +5% for relocating production "
                "back to CA, +5% for filming outside the 30-mile LA studio zone. Refundable for first "
                "time since 2009 (90% paid over 5 years if elected). Annual programme $750M/year. "
                "Must be allocated by California Film Commission before production begins."
            ),
        },
    )

    # ── 3. New York: 25% → 30% base; add upstate 40% tier ───────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 30.0, "
            "    rate_net = 30.0, "
            "    rate_tier_json = :tier_json, "
            "    warnings_json = :warnings, "
            "    eligibility_notes = :elig_notes, "
            "    source_url = 'https://esd.ny.gov/new-york-state-film-tax-credit-program-production', "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'New York'"
        ),
        {
            "rate": "30% base (40% for upstate NY filming)",
            "tier_json": (
                '[{"label":"Standard qualifying NY production expenditure","rate_gross":30},'
                '{"label":"Upstate NY uplift (>50% filming in designated upstate counties)","rate_gross":40}]'
            ),
            "warnings": (
                '["Base rate restored to 30% (was 25%) — update any reports using the old 25% figure",'
                '"Additional 10% for productions filming >50% in designated upstate counties (max 40% total)",'
                '"Independent film pool: separate $100M/year allocation at 30%",'
                '"Annual programme cap $800M — competitive but well-funded",'
                '"ATL costs qualify but cannot exceed 40% of total qualified in-state spend"]'
            ),
            "elig_notes": (
                "30% refundable/transferable credit on qualified NY production expenditures. "
                "Base rate restored to 30% (from 25%). Additional 10% uplift for productions "
                "filming >50% in designated upstate NY counties (max 40% total). "
                "Separate independent film pool ($100M/year) for productions ≤$10M or >$10M qualified costs. "
                "Annual programme cap $800M. ATL costs capped at 40% of qualified in-state spend."
            ),
        },
    )

    # ── 4. Illinois: 30% → 35% IL-resident, non-resident stays 30% ──────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 35.0, "
            "    rate_net = 35.0, "
            "    rate_tier_json = :tier_json, "
            "    warnings_json = :warnings, "
            "    eligibility_notes = :elig_notes, "
            "    source_url = 'https://dceo.illinois.gov/whyillinois/film/filmtaxcredit.html', "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Illinois'"
        ),
        {
            "rate": "35% IL resident labour/vendor; 30% non-resident (from July 2025)",
            "tier_json": (
                '[{"label":"IL resident payroll and qualified vendor spend (from Jul 2025)","rate_gross":35},'
                '{"label":"Non-resident labour (up to 13 non-resident employees, excl. actors)","rate_gross":30}]'
            ),
            "warnings": (
                '["Upgraded to 35% for IL resident labour/vendor from July 2025 (was 30%)",'
                '"30% rate applies to non-resident labour (up to 13 non-resident employees, actors excluded)",'
                '"Regional uplift: +5% on IL-resident labour for filming outside Cook, DuPage, Kane, Lake, McHenry, Will counties",'
                '"Sustainability bonus: +5% for certified green production",'
                '"Programme extended through December 31, 2038"]'
            ),
            "elig_notes": (
                "35% tax credit on IL resident payroll and qualified vendor spend (from July 2025; "
                "raised from 30%). 30% on non-resident labour (up to 13 non-resident employees, "
                "actors excluded). Regional uplift: +5% on IL-resident labour outside the 6 collar "
                "counties. Sustainability: +5% for certified green production. No per-production cap. "
                "Programme extended through December 31, 2038. Transferable credit."
            ),
        },
    )

    # ── 5. New Mexico: update structure (25% base, 40% max with uplifts) ─────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 35.0, "  # typical blended rate; 40% requires all uplifts
            "    rate_net = 35.0, "
            "    rate_tier_json = :tier_json, "
            "    warnings_json = :warnings, "
            "    eligibility_notes = :elig_notes, "
            "    source_url = 'https://nmfilm.com/whynewmexico/incentives-2', "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'New Mexico'"
        ),
        {
            "rate": "25% base + uplifts up to 40% of qualified NM expenditure",
            "tier_json": (
                '[{"label":"Base rate (all qualifying NM expenditure)","rate_gross":25},'
                '{"label":"With TV series/pilot uplift (+5%)","rate_gross":30},'
                '{"label":"With soundstage uplift (+5%)","rate_gross":30},'
                '{"label":"With rural filming uplift (60+ miles from Santa Fe/ABQ, +10%)","rate_gross":35},'
                '{"label":"Maximum with all applicable uplifts stacked","rate_gross":40}]'
            ),
            "warnings": (
                '["Base rate is 25% — the maximum 40% requires stacking multiple uplifts",'
                '"Available uplifts: +5% TV series/pilot; +5% qualified soundstage; +10% rural filming (60+ miles from Santa Fe or Albuquerque)",'
                '"Annual programme cap $140M (FY2026), rising to $160M in FY2028",'
                '"Non-resident principal performing artists capped at $5M per production",'
                '"Fully refundable — excess credit is paid as a cash refund",'
                '"Programme registration required before production"]'
            ),
            "elig_notes": (
                "Refundable tax credit: 25% base on qualified NM expenditure. Uplifts: +5% for TV "
                "series/pilot productions; +5% for use of qualified production facilities (soundstage); "
                "+10% for filming 60+ miles outside Santa Fe and Albuquerque city halls. Maximum 40% "
                "with all uplifts stacked. Annual cap $140M (FY2026). Non-resident performing artists "
                "capped at $5M per production. Fully refundable. Register before principal photography."
            ),
        },
    )

    # ── 6. New Zealand: update min spend, add PDV uplift tier ────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_tier_json = :tier_json, "
            "    qualifying_spend_min = 4000000.0, "  # NZD $4M from Jan 2026 (was $15M)
            "    warnings_json = :warnings, "
            "    eligibility_notes = :elig_notes, "
            "    source_url = 'https://www.nzfilm.co.nz/incentives-co-productions/nzspg-international', "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'New Zealand'"
        ),
        {
            "rate": "20% base rebate; 25% with standard uplift or PDV uplift",
            "tier_json": (
                '[{"label":"Base NZSPG rebate (all qualifying international productions)","rate_gross":20},'
                '{"label":"With standard uplift (enhanced NZ content/crew, NZD $20M+ spend)","rate_gross":25},'
                '{"label":"PDV-only productions with PDV uplift (from Jan 1, 2026)","rate_gross":25}]'
            ),
            "warnings": (
                '["Min spend for live-action reduced to NZD $4M (from $15M) — effective January 1, 2026",'
                '"New 5% PDV uplift from January 1, 2026 — PDV-only productions now eligible for 25% total",'
                '"Standard 5% uplift requires enhanced NZ content/crew criteria AND NZD $20M+ qualifying spend",'
                '"Rebate paid as cash after NZ Film Commission audit on completion",'
                '"NZD currency risk — rebate value in GBP/USD fluctuates with exchange rate"]'
            ),
            "elig_notes": (
                "NZSPG International: 20% base cash rebate on Qualifying NZ Production Expenditure (QNZPE). "
                "5% standard uplift: requires enhanced NZ content/crew criteria and NZD $20M+ spend. "
                "5% PDV uplift (new from Jan 1, 2026): separate uplift for PDV-only productions. "
                "Min spend: NZD $4M live-action features (reduced from $15M Jan 2026); NZD $250K PDV. "
                "Max total rebate: 25% (20% + either uplift). Paid as cash after NZ Film Commission audit."
            ),
        },
    )

    # ── 7. Serbia: add 30% large-production tier ─────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_tier_json = :tier_json, "
            "    qualifying_spend_min = 300000.0, "  # €300K feature min (was RSD-denominated)
            "    qualifying_spend_currency = 'EUR', "
            "    warnings_json = :warnings, "
            "    eligibility_notes = :elig_notes, "
            "    source_url = 'https://www.filminserbia.com/incentives/', "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Serbia'"
        ),
        {
            "rate": "25% standard; 30% for productions with >€5M qualifying Serbian spend",
            "tier_json": (
                '[{"label":"Standard rate (feature: €300K+ qualifying spend)","rate_gross":25},'
                '{"label":"Large production tier (€5M+ qualifying Serbian expenditure)","rate_gross":30}]'
            ),
            "warnings": (
                '["30% tier available for productions with qualifying Serbian expenditure exceeding €5M",'
                '"Standard 25% rate applies to features with €300K+ qualifying spend",'
                '"Annual programme budget ~€17M (2B Serbian dinars, 2025) — apply early",'
                '"Drama/animated series minimum €150K/episode; Documentary minimum €50K",'
                '"Cash rebate paid after audit — timeline variable"]'
            ),
            "elig_notes": (
                "25% cash rebate on qualifying Serbian expenditure (feature films: min €300K; "
                "drama/animated series: €150K/episode; documentary: €50K). "
                "30% enhanced rate for productions with qualifying Serbian expenditure exceeding €5M. "
                "Annual programme budget ~€17M. March 2025 Decree on Film Incentives governs eligibility. "
                "Cash rebate paid after production completion and audit."
            ),
        },
    )

    # ── 8. Portugal: clarify 25-30% sliding scale and annual fund constraint ──
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_tier_json = :tier_json, "
            "    cap_amount = 6000000.0, "  # €6M per feature (high-budget 30% instrument)
            "    cap_currency = 'EUR', "
            "    cap = '€6M per feature / €3M per episode (high-budget instrument)', "
            "    warnings_json = :warnings, "
            "    eligibility_notes = :elig_notes, "
            "    source_url = 'https://www.ica-ip.pt/en/1-4-5/cash-rebate/', "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = 'Portugal'"
        ),
        {
            "rate": "25-30% of qualifying Portuguese expenditure (sliding cultural test scale)",
            "tier_json": (
                '[{"label":"FATC standard rate (cultural test score dependent)","rate_gross":25},'
                '{"label":"FATC enhanced rate (high cultural test score)","rate_gross":30},'
                '{"label":"High-budget 30% instrument (€2.5M+ budget, separate fund)","rate_gross":30}]'
            ),
            "warnings": (
                '["Annual FATC fund is SMALL — €14M total per year, split into two phases (~€7M each)",'
                '"Fund is frequently oversubscribed — apply as early as possible in the annual cycle",'
                '"High-budget 30% instrument: separate €2.5M+ budget threshold; €6M cap/feature, €3M/episode",'
                '"Rate slides 25-30% based on cultural test score — 25% is the minimum",'
                '"Fiction/animation: €500K min qualifying spend; Documentaries: €250K"]'
            ),
            "elig_notes": (
                "FATC (Film and Audiovisual Tax Credit): 25-30% sliding scale on qualifying Portuguese "
                "expenditures, based on cultural test score. Annual fund €14M split into two phases. "
                "Separate high-budget instrument: 30% flat for productions with budget ≥€2.5M; "
                "cap of €6M per feature film or €3M per episode. "
                "Minimum qualifying spend: €500K (fiction/animation), €250K (documentary/post). "
                "Fund is competitive and frequently oversubscribed — apply early."
            ),
        },
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Morocco: restore 20%
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '20% of qualifying Moroccan production expenditure', "
            "    rate_gross = :rg, rate_net = :rg, qualifying_spend_min = 400000.0 "
            "WHERE territory = 'Morocco'"
        ),
        {"rg": _MA_OLD_RATE_GROSS},
    )

    # California: restore 22%
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '22% of qualified CA expenditure', "
            "    rate_gross = :rg, rate_net = :rg, "
            "    cap_amount = :cap, rate_type = 'tax_credit' "
            "WHERE territory = 'California'"
        ),
        {"rg": _CA_OLD_RATE_GROSS, "cap": _CA_OLD_CAP},
    )

    # New York: restore 25%
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '25% of qualified NY expenditure', "
            "    rate_gross = :rg, rate_net = :rg, rate_tier_json = NULL "
            "WHERE territory = 'New York'"
        ),
        {"rg": _NY_OLD_RATE_GROSS},
    )

    # Illinois: restore 30%
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '30% of qualified IL expenditure', "
            "    rate_gross = :rg, rate_net = :rg, rate_tier_json = NULL "
            "WHERE territory = 'Illinois'"
        ),
        {"rg": _IL_OLD_RATE_GROSS},
    )

    # New Mexico: restore 35% single rate
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '35% of qualified NM expenditure', "
            "    rate_gross = :rg, rate_net = :rg, rate_tier_json = NULL "
            "WHERE territory = 'New Mexico'"
        ),
        {"rg": _NM_OLD_RATE_GROSS},
    )

    # New Zealand: restore $15M min spend
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_min = :min, rate_tier_json = NULL "
            "WHERE territory = 'New Zealand'"
        ),
        {"min": _NZ_OLD_MIN},
    )

    # Serbia: restore plain 25%, remove tier
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '25% of qualifying Romanian production expenditure', "
            "    rate_tier_json = NULL, "
            "    qualifying_spend_min = 215000.0, qualifying_spend_currency = 'RSD' "
            "WHERE territory = 'Serbia'"
        )
    )

    # Portugal: restore simple 25%
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '25% of qualifying Portuguese expenditure', "
            "    rate_tier_json = NULL, cap_amount = NULL "
            "WHERE territory = 'Portugal'"
        )
    )
