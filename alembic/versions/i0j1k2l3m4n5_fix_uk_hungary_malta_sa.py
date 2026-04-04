"""fix_uk_hungary_malta_sa

Revision ID: i0j1k2l3m4n5
Revises: h9i0j1k2l3m4
Create Date: 2026-03-21 18:00:00.000000

Fixes confirmed data errors from official source verification (2026-03-21).

CORRECTIONS
-----------
1. UK AVEC — remove incorrect 25% BTL rate tier
   AVEC is a FLAT 34% rate on all qualifying UK expenditure. There is NO
   ATL/BTL split under AVEC. The 25%/34% split existed under the old Film
   Tax Relief (FTR) which AVEC replaced. Our DB incorrectly carried this
   legacy distinction. The VFX rate is a SEPARATE credit (39%), not a BTL
   sub-rate of AVEC. rate_tier_json corrected; rate_net updated to 34.0.
   Sources: GOV.UK, BFI

2. UK IFTC — clarify rate description above £15M
   The "53%/34% above" phrasing implies a blended rate continues above £15M.
   It does not. Correct behaviour: 53% on first £15M qualifying spend;
   productions up to £23.5M TOTAL still use IFTC (53% on first £15M only);
   above £23.5M total budget, IFTC is ineligible entirely — production
   defaults to standard AVEC at 34%. rate_tier_json corrected.
   Sources: GOV.UK IFTC policy note, BFI

3. Hungary — correct "VFX uplift" description
   The 37.5% is NOT a VFX-specific uplift. It is a non-Hungarian spend
   allowance: up to 25% of the rebate basis can comprise non-Hungarian
   production costs. This yields an effective 37.5% on the total budget
   when the 25% non-Hungarian allowance is fully used. Productions doing
   VFX inside Hungary receive the full 30% on that Hungarian VFX spend
   without this provision. Updating rate string and eligibility_notes.
   Sources: NFI Hungary

4. Malta — correct ATL cap (€12.5M → €5M max) and rate tiers
   The DB had cap_amount=€12.5M which cannot be verified from any official
   source. Official June 2024 Screen Malta guidelines state: ATL cap is
   the HIGHER of €1M OR 30% of Malta eligible spend, up to a MAXIMUM of
   €5M. Also: 40% rate is conditional (requires Malta Studios water tanks
   or maximum local resource use); the BASE rate is 30%; standard rate
   with Maltese cultural content is 35%; 40% requires specific production
   facility use. Rate tiers and cap corrected.
   Sources: Screen Malta June 2024 Guidelines, Cineuropa

5. South Africa — flag programme as operationally suspended
   The DTIC Foreign Film incentive has been effectively frozen since late
   2023. No new Letters of Approval (LOAs) have been issued since March
   2024. Outstanding backlog: R600M–R1B in unpaid claims. Employment in
   the sector collapsed 80%+ since 2021. Do NOT recommend this programme
   without disclosing the crisis. Payment timeline "9-15 months" is now
   severely understated — claims from 2023 remain unpaid as of March 2026.
   Sources: DTIC, Variety (Jan 2025), Daily Maverick (Feb 2026)
"""
from alembic import op
import sqlalchemy as sa

revision = "i0j1k2l3m4n5"
down_revision = "h9i0j1k2l3m4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. UK AVEC: remove incorrect 25% BTL rate — AVEC is flat 34% ─────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 34.0, "
            "    rate_net = 34.0, "         # AVEC net = gross (not 25.5)
            "    rate_tier_json = :tier_json, "
            "    eligibility_notes = :elig_notes, "
            "    warnings_json = :warnings, "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'United Kingdom' "
            "  AND program = 'Audio-Visual Expenditure Credit (AVEC)'"
        ),
        {
            "rate": "34% flat rate on all qualifying UK expenditure",
            "tier_json": (
                '[{"label":"All qualifying UK expenditure (single flat rate)","rate_gross":34,"rate_net":34}]'
            ),
            "elig_notes": (
                "AVEC is a single flat 34% rate applied to ALL qualifying UK expenditure — "
                "there is NO separate ATL/BTL split (that existed under the old Film Tax Relief "
                "which AVEC replaced from 1 April 2025). "
                "VFX expenditure: separate 39% VFX Expenditure Credit available for qualifying "
                "UK VFX costs from 1 January 2025 spend. "
                "HETV strand (TV series): minimum £1M qualifying UK spend per broadcast hour. "
                "AVEC and IFTC are mutually exclusive — choose one per project. "
                "Minimum 10% core expenditure must be incurred in the UK."
            ),
            "warnings": (
                '["AVEC is a FLAT 34% — applies equally to all qualifying UK expenditure (no ATL/BTL split)",'
                '"The old 25%/34% ATL/BTL split was Film Tax Relief (FTR), which AVEC replaced April 2025",'
                '"Separate 39% VFX Expenditure Credit available for qualifying UK VFX costs — see VFX row",'
                '"Mutually exclusive with IFTC",'
                '"HETV strand requires minimum £1M qualifying UK spend per broadcast hour",'
                '"BFI cultural test certification required before HMRC claim (allow 12-16 weeks)"]'
            ),
        },
    )

    # ── 2. UK IFTC: correct rate description above £15M ───────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_tier_json = :tier_json, "
            "    eligibility_notes = :elig_notes, "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'United Kingdom' "
            "  AND program = 'UK Independent Film Tax Credit (IFTC)'"
        ),
        {
            "rate": "53% on first £15M qualifying spend (total budget cap £23.5M)",
            "tier_json": (
                '[{"label":"Qualifying UK expenditure up to £15M (53% rate applies)","rate_gross":53,"rate_net":39.75},'
                '{"label":"Projects with £15M–£23.5M total budget: IFTC still applies but only on first £15M","rate_gross":53,"rate_net":39.75},'
                '{"label":"Projects above £23.5M total budget: IFTC ineligible — use standard AVEC (34%) instead","rate_gross":34,"rate_net":34}]'
            ),
            "elig_notes": (
                "53% rate on the first £15M of qualifying UK expenditure. "
                "Productions with total budget up to £23.5M can use IFTC (53% on first £15M only). "
                "Productions above £23.5M total budget are ineligible for IFTC — must use standard AVEC at 34%. "
                "Maximum credit value: £6.36M (£15M × 80% qualifying % × 53%). "
                "Theatrical release required — no direct-to-streaming. "
                "At least one of: UK writer, UK director, or certified UK co-production status. "
                "Minimum 10% core expenditure in UK. Mutually exclusive with AVEC and VFX credit. "
                "Principal photography must start on or after 1 April 2024; claims from 1 April 2025."
            ),
        },
    )

    # ── 3. UK VFX Expenditure Credit: clarify it's separate from AVEC ─────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET eligibility_notes = :elig_notes, "
            "    warnings_json = :warnings, "
            "    updated_at = NOW() "
            "WHERE territory = 'United Kingdom' "
            "  AND program = 'VFX Expenditure Credit (Uplift)'"
        ),
        {
            "elig_notes": (
                "39% credit on qualifying UK VFX expenditure (from 1 January 2025 spend, "
                "claimable from 1 April 2025). "
                "This is a SEPARATE credit from AVEC — it can be COMBINED with AVEC on the same "
                "production for VFX portions of the budget. "
                "CANNOT be combined with IFTC — mutually exclusive. "
                "Must pass BFI cultural test. VFX work must be physically performed in the UK."
            ),
            "warnings": (
                '["CAN be combined with standard AVEC (34%) on the same production for qualifying VFX costs",'
                '"CANNOT be combined with IFTC — mutually exclusive with IFTC",'
                '"39% applies only to qualifying UK VFX costs — overseas VFX does not qualify",'
                '"Effective for expenditure from 1 January 2025; claimable from 1 April 2025"]'
            ),
        },
    )

    # ── 4. Hungary: correct "VFX uplift" to "non-Hungarian spend allowance" ───
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    eligibility_notes = :elig_notes, "
            "    warnings_json = :warnings, "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'Hungary'"
        ),
        {
            "rate": "30% on qualifying Hungarian spend (37.5% effective when non-Hungarian spend allowance applied)",
            "elig_notes": (
                "30% on qualifying Hungarian production expenditure. "
                "Non-Hungarian spend allowance: up to 25% of the rebate basis can comprise "
                "non-Hungarian production costs — this yields an effective rate of 37.5% on total "
                "production budget when the full allowance is used. This is NOT a VFX-specific uplift: "
                "it applies to any eligible non-Hungarian costs (including foreign VFX, foreign ATL, etc.). "
                "Productions using Hungarian VFX facilities receive 30% on that spend within the standard base. "
                "HUF 3M per-person cap on ATL individual fees (100% eligible up to cap; 50% above). "
                "No nationality restriction; foreign company must contract through Hungarian registered entity. "
                "Annual collection cap HUF 70B; scheme extended to 2030 (EU approved 2024)."
            ),
            "warnings": (
                '["37.5% EFFECTIVE RATE: The 37.5% figure comes from the non-Hungarian spend allowance ",'
                '"(up to 25% of rebate basis can be non-Hungarian costs) — NOT a VFX-specific uplift",'
                '"Productions doing VFX IN Hungary get 30% on that VFX spend in the standard base",'
                '"Productions with significant FOREIGN VFX or ATL costs benefit most from the 37.5% effective rate",'
                '"HUF 3M per-person cap on above-the-line individual fees",'
                '"NFI annual budget cap ~HUF 70B — queue risk in busy years",'
                '"6-month commencement deadline from NFI support decision (December 2025 decree)"]'
            ),
        },
    )

    # ── 5. Malta: correct ATL cap (€12.5M → €5M max) and rate tiers ──────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = 30.0, "    # 30% is the base rate; 35% with cultural content; 40% with water tanks
            "    rate_tier_json = :tier_json, "
            "    cap_amount = 5000000.0, "   # ATL cap max €5M (corrected from €12.5M)
            "    cap_currency = 'EUR', "
            "    cap = :cap, "
            "    eligibility_notes = :elig_notes, "
            "    warnings_json = :warnings, "
            "    source_url = 'https://screenmalta.com/financial-incentives/', "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'Malta'"
        ),
        {
            "rate": "30% base / 35% with Maltese cultural content / 40% with Malta Studios water tanks",
            "tier_json": (
                '[{"label":"Base rate — all qualifying Malta expenditure","rate_gross":30},'
                '{"label":"With Maltese cultural content or Malta portrayed as Malta","rate_gross":35},'
                '{"label":"With Malta Studios water tanks used or maximum local resources","rate_gross":40}]'
            ),
            "cap": "ATL cap: higher of €1M or 30% of Malta eligible spend, up to €5M maximum",
            "elig_notes": (
                "30% base rate on qualifying Maltese expenditure. "
                "35% when Malta is portrayed as Malta or qualifying Maltese cultural content. "
                "40% when Malta Studios water tanks are used or maximum local resources are employed. "
                "ATL cap (June 2024 update): higher of €1M OR 30% of Malta eligible spend, up to €5M maximum. "
                "BTL costs are uncapped and open to ALL nationalities (EU/EEA/UK restriction removed June 2024). "
                "Min €100K Malta spend (€200K overall budget); €50K Malta spend (€100K budget) for low-budget/high-risk. "
                "10% of rebate available as early payment during production if accounts submitted. "
                "Programme expires 29 October 2028."
            ),
            "warnings": (
                '["40% rate is CONDITIONAL — requires Malta Studios water tanks or maximum local resources use",'
                '"Standard rate is 30% base; 35% with qualifying Maltese cultural content",'
                '"ATL cap: €1M OR 30% of Malta eligible spend (whichever is higher), up to €5M maximum",'
                '"BTL costs are UNCAPPED and now open to ALL nationalities (June 2024 update removed EU/EEA/UK restriction)",'
                '"Programme expires 29 October 2028 — verify renewal for productions filming after that date",'
                '"10% early payment option available during production"]'
            ),
        },
    )

    # ── 6. South Africa: flag programme as operationally suspended ────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET payment_reliability = 0.05, "  # Near-zero: LOA frozen, massive backlog
            "    payment_timeline_notes = :timeline, "
            "    warnings_json = :warnings, "
            "    eligibility_notes = :elig_notes, "
            "    last_verified_at = '2026-03-21', "
            "    updated_at = NOW() "
            "WHERE territory = 'South Africa' "
            "  AND program = 'South Africa Film & TV Production Incentive'"
        ),
        {
            "timeline": (
                "PROGRAMME EFFECTIVELY FROZEN as of March 2026. "
                "No new Letters of Approval (LOAs) issued since March 2024. "
                "Existing claims from 2023 remain unpaid. "
                "Do not model as bankable cash flow."
            ),
            "warnings": (
                '["CRITICAL — PROGRAMME OPERATIONALLY SUSPENDED: No new Letters of Approval (LOAs) '
                'have been issued since March 2024. DTIC adjudication panel has not met since then.",'
                '"BACKLOG: R600M–R1 billion in outstanding unpaid claims as of early 2026. Government '
                'committed R473M to begin clearing backlog (Feb 2026) but disbursement mechanism unclear.",'
                '"INDUSTRY COLLAPSE: Full-time equivalent roles fell from 4,943 in 2021 to 884 in 2024. '
                'Production investment collapsed from R6B+ in 2021 to ~R962M in 2024.",'
                '"R25M PER-PROJECT CAP: Maximum grant R25M regardless of budget size (~£1.05M at current rates).",'
                '"DO NOT INCLUDE in investor cash-flow projections or financing plans until the LOA freeze '
                'is formally lifted and new approvals resume.",'
                '"PROGRAMME DESIGN (when operational): 25% of qualifying SA spend; +5% post-production bonus; '
                'min ZAR 15M spend; ≥50% of principal photography and ≥21 days in South Africa required."]'
            ),
            "elig_notes": (
                "WARNING: Programme operationally suspended as of March 2026. No new LOAs issued since "
                "March 2024. Massive backlog of unpaid claims (R600M–R1B). "
                "Do not recommend to clients without explicit disclosure of the programme freeze. "
                "When operational: 25% cash rebate on qualifying SA spend (R25M per-project cap). "
                "+5% post-production bonus if post-production also done in SA. "
                "Min ZAR 15M qualifying spend. ≥50% of principal photography in SA. ≥21 calendar days in SA. "
                "Must use SA-registered production services company (SPCV)."
            ),
        },
    )


def downgrade() -> None:
    conn = op.get_bind()

    # UK AVEC: restore 34% ATL / 25% BTL tier (previous incorrect version)
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '34% ATL + 25% BTL on qualifying UK expenditure', "
            "    rate_net = 25.5, "
            "    rate_tier_json = '[{\"label\":\"Above-the-Line qualifying spend\",\"rate_gross\":34,\"rate_net\":25.5},{\"label\":\"Below-the-Line qualifying spend\",\"rate_gross\":25,\"rate_net\":18.75}]' "
            "WHERE territory = 'United Kingdom' "
            "  AND program = 'Audio-Visual Expenditure Credit (AVEC)'"
        )
    )

    # UK IFTC: restore previous tier_json
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '53% on first £15M, 34% above (gross)', "
            "    rate_tier_json = '[{\"label\":\"First £15M qualifying UK spend\",\"rate_gross\":53},{\"label\":\"Above £15M qualifying spend\",\"rate_gross\":34}]' "
            "WHERE territory = 'United Kingdom' "
            "  AND program = 'UK Independent Film Tax Credit (IFTC)'"
        )
    )

    # Hungary: restore vfx_uplift description
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '30% base (37.5% with qualifying VFX content)' "
            "WHERE territory = 'Hungary'"
        )
    )

    # Malta: restore €12.5M cap and old rate
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = '40% cash rebate on qualifying Malta expenditure (ATL + BTL)', "
            "    rate_gross = 40.0, "
            "    cap_amount = 12500000.0, "
            "    cap = '€12.5M ATL expenditure cap', "
            "    rate_tier_json = NULL "
            "WHERE territory = 'Malta'"
        )
    )

    # SA: restore payment_reliability 0.25
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET payment_reliability = 0.25 "
            "WHERE territory = 'South Africa' "
            "  AND program = 'South Africa Film & TV Production Incentive'"
        )
    )
