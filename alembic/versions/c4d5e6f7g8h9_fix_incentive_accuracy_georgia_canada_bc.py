"""fix_incentive_accuracy_georgia_canada_bc

Revision ID: c4d5e6f7g8h9
Revises: b2c3d4e5f6g8
Create Date: 2026-03-19 10:00:00.000000

Corrects three data accuracy errors confirmed against official government sources:

1. Georgia Entertainment Industry Investment Act — adds the $500K per-person
   W-2 salary cap (georgia.org Film Office). Individual W-2 wages above $500K
   do not count as qualifying spend; this materially reduces the credit on
   high-budget productions and was absent from all report output.

2. Canada BC Film Incentive (FIBC) — rate corrected from 35% to 36%.
   Creative BC increased the basic FIBC rate from 35% to 36% effective
   January 2025 (creativebc.com).

3. Canada CPTC — marked as requiring a Canadian-controlled corporation.
   Canada.ca CAVCO explicitly restricts CPTC to Canadian-owned corporations;
   a US or foreign producer must use PSTC (16% of qualifying Canadian labour)
   instead. The prior DB data had NULL nationality_requirements, causing
   _best_incentive() to select CPTC (25%) over PSTC (16%) for all Canada
   reports — an inaccessible programme yielding an overstated rebate.

4. Canada Federal PSTC — warnings strengthened to make clear that the 16%
   rate applies to qualifying Canadian labour expenditure only (not total
   production spend), since PSTC is now the modelled Canada incentive.
"""
from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7g8h9"
down_revision = "b2c3d4e5f6g8"
branch_labels = None
depends_on = None

# ── 1. Georgia ────────────────────────────────────────────────────────────────

_GA_TERRITORY = "Georgia (USA)"
_GA_PROGRAM = "Georgia Entertainment Industry Investment Act"

_GA_NEW_CAP_PER_PERSON = 500_000.0
_GA_NEW_CAP_PER_PERSON_CURRENCY = "USD"

_GA_NEW_WARNINGS = (
    "["
    '"Transferable credit \u2014 sold at ~88-90% of face value",'
    '"Political risk: periodic legislative challenges to programme",'
    '"$500K W-2 salary cap per individual: wages above $500,000 per person do not count'
    " as qualifying spend for the credit \u2014 on a $50M production with high-fee cast"
    " and department heads above this threshold, this materially reduces the actual credit"
    " received. Verify full payroll breakdown with your Georgia production accountant and"
    ' the Georgia Film Office before including this rebate in investor documents."'
    "]"
)

_GA_NEW_ELIGIBILITY_RULES = (
    "["
    '{"rule":"Minimum $500K Georgia spend","required":true},'
    '{"rule":"Apply to Georgia Film Office before principal photography","required":true},'
    '{"rule":"Include Georgia promotional logo in deliverables for the additional 10% uplift","required":false},'
    '{"rule":"Individual W-2 wages capped at $500,000 per person for credit calculation'
    " \u2014 amounts above this threshold are excluded from qualifying spend\","
    '"required":true}'
    "]"
)

# Previous values for downgrade
_GA_OLD_WARNINGS = (
    '["Transferable credit \u2014 sold at ~88-90% of face value",'
    '"Political risk: periodic legislative challenges to programme"]'
)
_GA_OLD_ELIGIBILITY_RULES = (
    "["
    '{"rule":"Minimum $500K Georgia spend","required":true},'
    '{"rule":"Apply to Georgia Film Office before production","required":true},'
    '{"rule":"Include Georgia promotional logo for extra 10%","required":false}'
    "]"
)

# ── 2. BC FIBC ────────────────────────────────────────────────────────────────

_BC_TERRITORY = "British Columbia"
_BC_PROGRAM = "BC Film Incentive BC Tax Credit (FIBC)"

_BC_NEW_RATE = "36% of qualified BC labour"
_BC_NEW_RATE_GROSS = 36.0
_BC_NEW_RATE_NET = 36.0

_BC_OLD_RATE = "35% of qualified BC labour"
_BC_OLD_RATE_GROSS = 35.0
_BC_OLD_RATE_NET = 35.0

# ── 3. CPTC ───────────────────────────────────────────────────────────────────

_CPTC_TERRITORY = "Canada"
_CPTC_PROGRAM = "Canadian Film or Video Production Tax Credit (CPTC)"

_CPTC_NEW_NATIONALITY_REQUIREMENTS = '["CA"]'
_CPTC_NEW_SPV_ELIGIBLE = False

_CPTC_NEW_ELIGIBILITY_RULES = (
    "["
    '{"rule":"Canadian-owned (Canadian-controlled) production company required'
    " \u2014 this programme is NOT accessible to US or other non-Canadian producers"
    ' directly","required":true},'
    '{"rule":"CAVCO certification required","required":true},'
    '{"rule":"Canadian theatrical distribution commitment required","required":true}'
    "]"
)

_CPTC_NEW_WARNINGS = (
    "["
    '"CPTC is restricted to Canadian-controlled corporations'
    " \u2014 a US or foreign producer cannot access this programme"
    ' without a Canadian-owned production company",'
    '"US and other foreign producers must use the Canada Federal PSTC route'
    " (16% of qualifying Canadian labour expenditure)"
    " \u2014 this is a significantly lower effective rate than the CPTC headline rate\","
    '"CRA processing can take 4-12 months'
    " \u2014 do not treat as investor-bankable cash flow\""
    "]"
)

# Previous values for downgrade (NULL / scraper defaults)
_CPTC_OLD_NATIONALITY_REQUIREMENTS = None
_CPTC_OLD_SPV_ELIGIBLE = None
_CPTC_OLD_ELIGIBILITY_RULES = (
    "["
    '{"rule":"Canadian-controlled production company required for CPTC'
    " \u2014 US producers should verify eligibility or consider PSTC route\","
    '"required":true},'
    '{"rule":"CAVCO certification required","required":true},'
    '{"rule":"Canadian theatrical distribution commitment","required":true}'
    "]"
)
_CPTC_OLD_WARNINGS = (
    "["
    '"CPTC is designed for Canadian-controlled productions.'
    " US producers should verify eligibility and consider the PSTC route\","
    '"CRA processing 4-12 months"'
    "]"
)

# ── 4. PSTC ───────────────────────────────────────────────────────────────────

_PSTC_TERRITORY = "Canada"
_PSTC_PROGRAM = "Canada Federal PSTC (Production Services Tax Credit)"

_PSTC_NEW_WARNINGS = (
    "["
    '"APPLIES TO QUALIFYING CANADIAN LABOUR EXPENDITURE ONLY'
    " \u2014 not total production spend. The modelled rebate assumes 100% of qualifying"
    " spend is Canadian labour, which overstates the actual credit. Typical Canadian labour"
    " is 30\u201345% of a production budget \u2014 verify actual labour split with a"
    ' Canadian production accountant before including in investor documents.",'
    '"CRA assessment can take 4-12 months in backlog periods'
    " \u2014 do not treat as investor-bankable cash flow\","
    '"Stackable with provincial credits: BC FIBC (36%), Ontario OPSTC (21.5%),'
    " Quebec QPSTC (20%) \u2014 all applied to qualifying provincial labour expenditure\""
    "]"
)

_PSTC_OLD_WARNINGS = (
    '["Applies to labour expenditure only \u2014 not total production spend",'
    '"CRA assessment can take up to 12 months in backlog periods",'
    '"Stackable with provincial credits (BC FIBC, Ontario OPSTC, Quebec QPSTC)"]'
)


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Georgia — add W-2 salary cap
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_per_person = :cap_per_person, "
            "    cap_per_person_currency = :cap_per_person_currency, "
            "    warnings_json = :warnings, "
            "    eligibility_rules_json = :rules, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "cap_per_person": _GA_NEW_CAP_PER_PERSON,
            "cap_per_person_currency": _GA_NEW_CAP_PER_PERSON_CURRENCY,
            "warnings": _GA_NEW_WARNINGS,
            "rules": _GA_NEW_ELIGIBILITY_RULES,
            "territory": _GA_TERRITORY,
            "program": _GA_PROGRAM,
        },
    )

    # 2. BC FIBC — update rate 35% → 36%
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = :rate_gross, "
            "    rate_net = :rate_net, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _BC_NEW_RATE,
            "rate_gross": _BC_NEW_RATE_GROSS,
            "rate_net": _BC_NEW_RATE_NET,
            "territory": _BC_TERRITORY,
            "program": _BC_PROGRAM,
        },
    )

    # 3. CPTC — mark as domestic-corp-only
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET nationality_requirements = :nationality_requirements, "
            "    spv_eligible = :spv_eligible, "
            "    eligibility_rules_json = :rules, "
            "    warnings_json = :warnings, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "nationality_requirements": _CPTC_NEW_NATIONALITY_REQUIREMENTS,
            "spv_eligible": _CPTC_NEW_SPV_ELIGIBLE,
            "rules": _CPTC_NEW_ELIGIBILITY_RULES,
            "warnings": _CPTC_NEW_WARNINGS,
            "territory": _CPTC_TERRITORY,
            "program": _CPTC_PROGRAM,
        },
    )

    # 4. PSTC — strengthen labour-only warning
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET warnings_json = :warnings, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "warnings": _PSTC_NEW_WARNINGS,
            "territory": _PSTC_TERRITORY,
            "program": _PSTC_PROGRAM,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()

    # 1. Georgia — remove W-2 salary cap
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET cap_per_person = NULL, "
            "    cap_per_person_currency = NULL, "
            "    warnings_json = :warnings, "
            "    eligibility_rules_json = :rules "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "warnings": _GA_OLD_WARNINGS,
            "rules": _GA_OLD_ELIGIBILITY_RULES,
            "territory": _GA_TERRITORY,
            "program": _GA_PROGRAM,
        },
    )

    # 2. BC FIBC — revert rate 36% → 35%
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = :rate_gross, "
            "    rate_net = :rate_net "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _BC_OLD_RATE,
            "rate_gross": _BC_OLD_RATE_GROSS,
            "rate_net": _BC_OLD_RATE_NET,
            "territory": _BC_TERRITORY,
            "program": _BC_PROGRAM,
        },
    )

    # 3. CPTC — remove domestic-corp restriction
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET nationality_requirements = :nationality_requirements, "
            "    spv_eligible = :spv_eligible, "
            "    eligibility_rules_json = :rules, "
            "    warnings_json = :warnings "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "nationality_requirements": _CPTC_OLD_NATIONALITY_REQUIREMENTS,
            "spv_eligible": _CPTC_OLD_SPV_ELIGIBLE,
            "rules": _CPTC_OLD_ELIGIBILITY_RULES,
            "warnings": _CPTC_OLD_WARNINGS,
            "territory": _CPTC_TERRITORY,
            "program": _CPTC_PROGRAM,
        },
    )

    # 4. PSTC — restore original warnings
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET warnings_json = :warnings "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "warnings": _PSTC_OLD_WARNINGS,
            "territory": _PSTC_TERRITORY,
            "program": _PSTC_PROGRAM,
        },
    )
