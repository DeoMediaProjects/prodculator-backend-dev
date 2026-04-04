"""fix_thirteen_confirmed_errors

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-03-24

Fixes 13 confirmed errors identified by comprehensive fact-check of all 42 active
incentive rows against official government sources (March 2026).

Errors fixed:

 1. Australia — Producer Offset
    rate_gross 30% → 40% (theatrical feature films). 30% is correct only for
    TV and non-theatrical formats. 40% is the primary rate for this row, which
    represents the theatrical feature film programme.
    Source: Screen Australia / ATO film industry incentives 2025.

 2. Quebec — QPSTC
    rate_gross/rate_net 20% → 25%. Rate increased via 2023 Québec Budget.
    rate_tier_json labour rate also updated: 20% → 25%.
    Source: SODEC / Revenu Québec 2024.

 3. California — Film & TV Tax Credit Program 4.0
    rate_tier_json was showing stale Program 3.0 rates (20%/25%).
    Updated to reflect Program 4.0 structure (effective 1 July 2025): 35% base,
    uplifts available. cap_amount and cap_currency were already correct (USD).
    Source: California Film Commission, AB 132 (2025).

 4. Louisiana — Motion Picture Investor Tax Credit
    cap_amount $20M per project → NULL. The per-project cap was eliminated
    effective 1 July 2025. Only an annual state issuance cap of $125M remains.
    warnings_json updated to remove the stale "$20M credit cap" reference.
    Source: Louisiana Entertainment, Act 323 (2025).

 5. New Mexico — Film Tax Credit
    rate_gross/rate_net 35% → 25% (correct base rate). The 35% was the
    MAXIMUM achievable with all uplifts stacked; the base rate is 25%.
    rate_tier_json and eligibility_notes already correctly described the 25%
    base — only the headline rate_gross/rate_net fields were wrong.
    Source: New Mexico Film Office / NM Tax and Revenue Dept.

 6. France — Crédit d'Impôt Cinéma (CIC)
    cap_amount €25M → €30M per film.
    warnings_json "€25M per-project rebate cap" corrected to €30M.
    Source: CNC official guidelines 2024/2025.

 7. Germany — GMPF
    rate_gross/rate_net 25% → 30% (post-January 2025 DFFF/GMPF reform).
    cap_amount €25M → €20M per project (post-2025 reform).
    warnings_json "combined benefit can reach 45%" updated to 60% (30+30).
    Source: German Federal Film Fund reform announcement, Jan 2025 / FFA.

 8. Czech Republic — Czech Film Fund Incentive Programme
    cap_amount CZK 150M → CZK 450M (tripled in January 2025 reform under
    new Czech Audiovisual Fund Act). rate_tier_json base rate also updated
    from 20% to 25% (increased in same 2025 reform).
    warnings_json "CZK 150M annual cap" corrected.
    Source: Czech Audiovisual Fund / Prague Reporter Feb 2025.

 9. Spain — General Tax Incentive
    cap_amount €10M → €20M per feature film. The €10M applies per TV episode;
    the per-feature-film cap is €20M. eligibility_notes corrected to reflect
    this distinction explicitly.
    Source: Art. 36.2 LIS / Spanish culture ministry 2025.

10. Iceland — Film Production Reimbursement
    Add warning about legislative expiry: the enabling Act No. 43/1999 expired
    at end of 2025. As of early 2026 the programme's continuation depends on
    new legislation being enacted. rate_tier_json (25%/35%) was already correct.
    Source: Icelandic Film Centre / official gov.is records.

11. New Zealand — NZSPR (formerly NZSPG)
    rate_type 'cash_grant' → 'cash_rebate' (it is a rebate, not a grant).
    Programme was also renamed from NZSPG to NZSPR — name updated.
    Source: New Zealand Screen Production Rebate (toitainui.govt.nz / NZ Film).

12. Malta — Film Commission Cash Rebate
    rate_gross 30% → 40%. Industry convention and Screen Malta's own
    headline rate is 40% (the maximum achievable rate). The tiered structure
    (30%/35%/40%) was already correctly stored in rate_tier_json and
    eligibility_notes. The headline rate_gross is updated to match the 40%
    figure that Malta markets and that qualifying productions can achieve.
    Source: Screen Malta financial incentives guidelines 2024.

13. Wales — Ffilm Cymru Wales Production Fund
    cap_amount £500K → £600K. Updated per Ffilm Cymru Wales & Creative Wales
    Independent Feature Film Production Fund guidelines (March 2025).
    eligibility_notes updated to reflect new cap.
    Source: Ffilm Cymru Wales funding guidelines 2025.
"""
from __future__ import annotations

import json
import sqlalchemy as sa
from alembic import op

revision = "m4n5o6p7q8r9"
down_revision = "l3m4n5o6p7q8"
branch_labels = None
depends_on = None

# ── 3. California — updated rate_tier_json reflecting Program 4.0 ─────────
CA_TIER_JSON = json.dumps([
    {
        "label": "Program 4.0 base credit (from 1 July 2025) — all qualifying CA expenditure",
        "rate_gross": 35,
    },
    {
        "label": "Independent film uplift (+5% qualified uplift — additional 5% = 40% total)",
        "rate_gross": 5,
    },
])

# ── 8. Czech Republic — updated tier JSON (base rate 20% → 25%) ───────────
CZ_TIER_JSON = json.dumps([
    {
        "label": "Base rebate on qualifying Czech spend (increased from 20% in Jan 2025 reform)",
        "rate_gross": 25,
    },
    {
        "label": "Czech crew bonus (Czech/EEA cast & crew spend)",
        "rate_gross": 10,
    },
])

CZ_WARNINGS = json.dumps([
    "CZK 450M per-project cap (tripled from CZK 150M in January 2025 reform under new Czech Audiovisual Fund Act)",
    "10% crew bonus only applies to Czech/EEA citizens",
    "Barrandov Studios regularly at capacity — book early",
    "Now administered by the Czech Audiovisual Fund (CAF) under the new Audiovisual Act from January 2025",
])

# ── 9. Spain — updated eligibility_notes ─────────────────────────────────
SPAIN_ELIGIBILITY_NOTES = (
    "Art. 36.2 LIS: 30% on first €1M of qualifying Spanish expenditure, 25% above. "
    "Open to all foreign productions (non-EU included) via Spanish co-producer or SPV. "
    "CAP: Maximum tax deduction €20M per feature film project. "
    "For TV series: maximum €10M per episode. "
    "Cultural test required (at least 1 of 3 Spanish creative criteria). "
    "Must be a Spanish co-producing entity."
)

# ── 10. Iceland — updated warnings JSON (add expiry warning) ─────────────
ICELAND_WARNINGS = json.dumps([
    "PROGRAMME EXPIRY / RENEWAL UNCERTAINTY: The enabling legislation (Act No. 43/1999 as amended) "
    "expired at end of 2025. As of early 2026, continuation of the programme depends on new "
    "legislation being enacted by the Althing (Icelandic parliament). Verify current status with "
    "the Icelandic Film Centre (kvikmyndamiðstöð.is) before committing to an Icelandic shoot.",
    "35% ENHANCED TIER: ISK 350M+ qualifying Icelandic spend AND 30+ working days AND 50+ "
    "full-time Icelandic staff — verify with Ragna Filmkvóti / Icelandic Film Centre",
    "Standard rate is 25% for productions not meeting enhanced tier criteria",
    "No per-project cap — but Ragna Fund (the reimbursement vehicle) subject to annual budget",
    "Minimum qualifying Icelandic spend: ISK 2M",
])

# ── 11. New Zealand — updated programme name ─────────────────────────────
NZ_NEW_PROGRAM_NAME = "NZSPR (NZ Screen Production Rebate — International)"

# ── 12. Malta — updated eligibility_notes to lead with 40% headline ──────
MALTA_ELIGIBILITY_NOTES = (
    "Cash rebate up to 40% of qualifying Maltese expenditure. Tiered structure: "
    "30% base rate on all qualifying Malta expenditure; "
    "35% when Malta is portrayed as Malta or qualifying Maltese cultural content is incorporated; "
    "40% when Malta Studios water tanks are used or maximum local resources are mobilised. "
    "Minimum spend: €250K qualifying Maltese expenditure. "
    "No per-project rebate cap. Scheme runs through October 2028. "
    "ATL cap: €1M OR 30% of Malta expenditure (whichever is lower) on above-the-line costs. "
    "Foreign productions access via Malta Film Commission / Screen Malta."
)


_AU_NOTES = (
    "Producer Offset for Australian producers: 40% for theatrically-released feature films; "
    "30% for TV drama/documentary (qualifying spend threshold applies), animated feature films, "
    "and non-theatrical releases. NOT accessible to foreign producers who are not Australian "
    "residents. Requires significant Australian content (points test) and Australian resident "
    "producer. Min 70% of total film budget must be qualifying Australian production expenditure "
    "(QAPE)."
)

_QC_TIER_JSON = json.dumps([
    {"label": "Quebec labour expenditure (increased from 20% in 2023 Budget)", "rate_gross": 25},
    {"label": "Non-labour Quebec expenditure (service productions)", "rate_gross": 20},
])

_LA_WARNINGS = json.dumps([
    "Transferable credit — sold at approximately 85-88 cents on the dollar",
    (
        "PER-PROJECT CAP REMOVED (effective 1 July 2025): No per-project credit cap. "
        "Only the $125M annual state issuance cap applies — apply early in the calendar year."
    ),
    (
        "Rate structure: 25% base + up to 15% Louisiana resident payroll bonus + "
        "10% screenplay bonus (Louisiana-written) — maximum combined credit can exceed "
        "40% for qualifying productions"
    ),
    "Programme has been modified several times — verify current rules with Louisiana Entertainment before finalising budget",
])

_FR_WARNINGS = json.dumps([
    "Only available to French-initiated productions — foreign productions use TRIP instead",
    "€990K per-person ATL cap reduces qualifying spend for high-fee talent",
    "€30M per-project rebate cap (increased from €25M)",
])

_DE_WARNINGS = json.dumps([
    "High minimum spend threshold (€8M) — only for large-scale productions",
    "Competitive grant — not guaranteed",
    "Stackable with DFFF — combined benefit can reach 60% on eligible German spend (DFFF 30% + GMPF 30%)",
    "Rate increased from 25% to 30% and cap reduced from €25M to €20M effective January 2025 reform",
])

_ES_WARNINGS = json.dumps([
    "€20M maximum tax deduction per feature film (€10M per TV series episode)",
    "Foreign producers must operate through a Spanish co-producer or registered SPV",
    "Tax credit — offset against Spanish corporate tax; timing depends on tax filing",
])

_WALES_NOTES = (
    "Up to £600K grant/equity per project (updated March 2025 — Ffilm Cymru Wales & "
    "Creative Wales Independent Feature Film Production Fund guidelines). Welsh shoot required. "
    "Welsh language or cultural element adds points to BFI Cultural Test. "
    "Stackable with AVEC or IFTC."
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Australia — Producer Offset rate_gross 30% → 40% ─────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross        = 40.0,
            eligibility_notes = :notes,
            last_verified_at  = '2026-03-24'
        WHERE territory = 'Australia'
          AND program   = 'Producer Offset'
          AND status    = 'active'
    """), {"notes": _AU_NOTES})

    # ── 2. Quebec — QPSTC rate 20% → 25% ────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross       = 25.0,
            rate_net         = 25.0,
            rate_tier_json   = :tier_json,
            last_verified_at = '2026-03-24'
        WHERE territory = 'Quebec'
          AND program   = 'Quebec QPSTC (Production Services Tax Credit)'
          AND status    = 'active'
    """), {"tier_json": _QC_TIER_JSON})

    # ── 3. California — fix stale Program 3.0 rate_tier_json ─────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_tier_json   = :tier_json,
            last_verified_at = '2026-03-24'
        WHERE territory = 'California'
          AND program ILIKE :prog_pat
          AND status   = 'active'
    """), {"tier_json": CA_TIER_JSON, "prog_pat": "%California Film%"})

    # ── 4. Louisiana — remove per-project cap (eliminated July 2025) ─────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount       = NULL,
            warnings_json    = :warnings,
            last_verified_at = '2026-03-24'
        WHERE territory = 'Louisiana'
          AND program ILIKE :prog_pat
          AND status   = 'active'
    """), {"warnings": _LA_WARNINGS, "prog_pat": "%Louisiana Motion Picture%"})

    # ── 5. New Mexico — fix headline rate_gross/rate_net 35% → 25% ──────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross       = 25.0,
            rate_net         = 25.0,
            last_verified_at = '2026-03-24'
        WHERE territory = 'New Mexico'
          AND program ILIKE :prog_pat
          AND status   = 'active'
    """), {"prog_pat": "%New Mexico Film Tax Credit%"})

    # ── 6. France CIC — cap €25M → €30M ──────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount       = 30000000.0,
            warnings_json    = :warnings,
            last_verified_at = '2026-03-24'
        WHERE territory = 'France'
          AND program   = :prog
          AND status    = 'active'
    """), {"warnings": _FR_WARNINGS, "prog": "Crédit d'Impôt Cinéma (CIC)"})

    # ── 7. Germany GMPF — rate 25% → 30%, cap €25M → €20M ───────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross       = 30.0,
            rate_net         = 30.0,
            cap_amount       = 20000000.0,
            warnings_json    = :warnings,
            last_verified_at = '2026-03-24'
        WHERE territory = 'Germany'
          AND program   = 'German Motion Picture Fund (GMPF)'
          AND status    = 'active'
    """), {"warnings": _DE_WARNINGS})

    # ── 8. Czech Republic — cap CZK 150M → 450M, base tier 20% → 25% ────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount       = 450000000.0,
            rate_tier_json   = :tier_json,
            warnings_json    = :warnings,
            last_verified_at = '2026-03-24'
        WHERE territory = 'Czech Republic'
          AND program   = 'Czech Film Fund Incentive Programme'
          AND status    = 'active'
    """), {"tier_json": CZ_TIER_JSON, "warnings": CZ_WARNINGS})

    # ── 9. Spain — cap €10M → €20M per feature ───────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount        = 20000000.0,
            eligibility_notes = :notes,
            warnings_json     = :warnings,
            last_verified_at  = '2026-03-24'
        WHERE territory = 'Spain'
          AND program ILIKE :prog_pat
          AND status   = 'active'
    """), {"notes": SPAIN_ELIGIBILITY_NOTES, "warnings": _ES_WARNINGS, "prog_pat": "%Spain General Tax Incentive%"})

    # ── 10. Iceland — add legislative expiry warning ──────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json    = :warnings,
            last_verified_at = '2026-03-24'
        WHERE territory = 'Iceland'
          AND program ILIKE :prog_pat
          AND status   = 'active'
    """), {"warnings": ICELAND_WARNINGS, "prog_pat": "%Iceland Film Production%"})

    # ── 11. New Zealand — fix rate_type + programme name ─────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_type        = 'cash_rebate',
            program          = :prog,
            last_verified_at = '2026-03-24'
        WHERE territory = 'New Zealand'
          AND program ILIKE :prog_pat
          AND status   = 'active'
    """), {"prog": NZ_NEW_PROGRAM_NAME, "prog_pat": "%NZSPG%"})

    # ── 12. Malta — rate_gross 30% → 40% (headline), update notes ────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross        = 40.0,
            rate_net          = 40.0,
            eligibility_notes = :notes,
            last_verified_at  = '2026-03-24'
        WHERE territory = 'Malta'
          AND program   = 'Malta Film Commission Cash Rebate'
          AND status    = 'active'
    """), {"notes": MALTA_ELIGIBILITY_NOTES})

    # ── 13. Wales — cap £500K → £600K ────────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount        = 600000.0,
            eligibility_notes = :notes,
            last_verified_at  = '2026-03-24'
        WHERE territory = 'Wales'
          AND program ILIKE :prog_pat
          AND status   = 'active'
    """), {"notes": _WALES_NOTES, "prog_pat": "%Ffilm Cymru%"})


def downgrade() -> None:
    conn = op.get_bind()

    _dg_au_notes = (
        "The Producer Offset (up to 40% for qualifying Australian theatrical films, "
        "30% for TV) is exclusively available to Australian resident producers with "
        "significant Australian creative elements. Not accessible to foreign producers."
    )
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross = 30.0, eligibility_notes = :notes
        WHERE territory = 'Australia' AND program = 'Producer Offset' AND status = 'active'
    """), {"notes": _dg_au_notes})

    _dg_qc_tier = json.dumps([
        {"label": "Quebec labour expenditure", "rate_gross": 20},
        {"label": "Non-labour Quebec expenditure (service productions)", "rate_gross": 16},
    ])
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross = 20.0, rate_net = 20.0, rate_tier_json = :tier
        WHERE territory = 'Quebec'
          AND program = 'Quebec QPSTC (Production Services Tax Credit)'
          AND status = 'active'
    """), {"tier": _dg_qc_tier})

    _dg_ca_tier = json.dumps([
        {"label": "Non-independent film", "rate_gross": 20},
        {"label": "Independent film / relocating TV", "rate_gross": 25},
    ])
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_tier_json = :tier
        WHERE territory = 'California' AND program ILIKE :pat AND status = 'active'
    """), {"tier": _dg_ca_tier, "pat": "%California Film%"})

    _dg_la_warnings = json.dumps([
        "Transferable credit — sold at approximately 85-88 cents on the dollar",
        "$20M credit cap per feature film project",
        "$150M annual programme cap — apply early",
        "Programme has been modified several times — verify current rules with Louisiana Entertainment before finalising budget",
    ])
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount = 20000000.0, warnings_json = :w
        WHERE territory = 'Louisiana' AND program ILIKE :pat AND status = 'active'
    """), {"w": _dg_la_warnings, "pat": "%Louisiana Motion Picture%"})

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross = 35.0, rate_net = 35.0
        WHERE territory = 'New Mexico' AND program ILIKE :pat AND status = 'active'
    """), {"pat": "%New Mexico Film Tax Credit%"})

    _dg_fr_warnings = json.dumps([
        "Only available to French-initiated productions — foreign productions use TRIP instead",
        "€990K per-person ATL cap reduces qualifying spend for high-fee talent",
        "€25M per-project rebate cap",
    ])
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount = 25000000.0, warnings_json = :w
        WHERE territory = 'France' AND program = :prog AND status = 'active'
    """), {"w": _dg_fr_warnings, "prog": "Crédit d'Impôt Cinéma (CIC)"})

    _dg_de_warnings = json.dumps([
        "High minimum spend threshold (€8M) — only for large-scale productions",
        "Competitive grant — not guaranteed",
        "Stackable with DFFF — combined benefit can reach 45%",
    ])
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross = 25.0, rate_net = 25.0, cap_amount = 25000000.0, warnings_json = :w
        WHERE territory = 'Germany' AND program = 'German Motion Picture Fund (GMPF)'
          AND status = 'active'
    """), {"w": _dg_de_warnings})

    _dg_cz_tier = json.dumps([
        {"label": "Base rebate on qualifying Czech spend", "rate_gross": 20},
        {"label": "Czech crew bonus (Czech/EEA cast & crew spend)", "rate_gross": 10},
    ])
    _dg_cz_warnings = json.dumps([
        "CZK 150M annual cap per applicant — large productions may hit limit",
        "10% crew bonus only applies to Czech/EEA citizens",
        "Barrandov Studios regularly at capacity — book early",
    ])
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount = 150000000.0, rate_tier_json = :tier, warnings_json = :w
        WHERE territory = 'Czech Republic'
          AND program = 'Czech Film Fund Incentive Programme' AND status = 'active'
    """), {"tier": _dg_cz_tier, "w": _dg_cz_warnings})

    _dg_es_notes = (
        "Art. 36.2 LIS: 30% on first €1M of qualifying Spanish expenditure, 25% above. "
        "Open to all foreign productions (non-EU included) via Spanish co-producer or SPV. "
        "Maximum deduction €10M per project. Cultural test required."
    )
    _dg_es_warnings = json.dumps([
        "€10M maximum tax deduction per project",
        "Foreign producers must operate through a Spanish co-producer or registered SPV",
        "Tax credit — offset against Spanish corporate tax; timing depends on tax filing",
    ])
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount = 10000000.0, eligibility_notes = :notes, warnings_json = :w
        WHERE territory = 'Spain' AND program ILIKE :pat AND status = 'active'
    """), {"notes": _dg_es_notes, "w": _dg_es_warnings, "pat": "%Spain General Tax Incentive%"})

    _dg_is_warnings = json.dumps([
        "35% ENHANCED TIER: ISK 350M+ qualifying Icelandic spend AND 30+ working days AND "
        "50+ full-time Icelandic staff — verify with Ragna Filmkvóti / Icelandic Film Centre",
        "Standard rate is 25% for productions not meeting enhanced tier criteria",
        "No per-project cap — but Ragna Fund (the reimbursement vehicle) subject to annual budget",
        "Minimum qualifying Icelandic spend: ISK 2M",
    ])
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = :w
        WHERE territory = 'Iceland' AND program ILIKE :pat AND status = 'active'
    """), {"w": _dg_is_warnings, "pat": "%Iceland Film Production%"})

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_type = 'cash_grant',
            program   = 'NZSPG (NZ Screen Production Grant — International)'
        WHERE territory = 'New Zealand' AND program ILIKE :pat AND status = 'active'
    """), {"pat": "%NZSPR%"})

    _dg_mt_notes = (
        "30% base rate on qualifying Maltese expenditure. "
        "35% when Malta is portrayed as Malta or qualifying Maltese cultural content. "
        "40% when Malta Studios water tanks are used or maximum local resources are mobilised."
    )
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_gross = 30.0, rate_net = 30.0, eligibility_notes = :notes
        WHERE territory = 'Malta' AND program = 'Malta Film Commission Cash Rebate'
          AND status = 'active'
    """), {"notes": _dg_mt_notes})

    _dg_wales_notes = (
        "Up to £500K grant/equity per project. Welsh shoot required. "
        "Welsh language or cultural element adds points to BFI Cultural Test. "
        "Stackable with AVEC or IFTC."
    )
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_amount = 500000.0, eligibility_notes = :notes
        WHERE territory = 'Wales' AND program ILIKE :pat AND status = 'active'
    """), {"notes": _dg_wales_notes, "pat": "%Ffilm Cymru%"})
