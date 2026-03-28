"""fix_five_incentive_errors

Revision ID: e6f7g8h9i0j1
Revises: d5e6f7g8h9i0
Create Date: 2026-03-21 12:00:00.000000

Fixes five systematic tax incentive errors confirmed against official government
sources.  All errors produced wrong output on every report for these territories.

SCHEMA ADDITIONS
----------------
rebate_cap_amount (FLOAT, nullable)
  Maximum grant issued per project — a hard ceiling on the computed rebate.
  Distinct from cap_amount, which is a BUDGET threshold that triggers programme
  switching.  Example: South Africa R25M per-project cap.

rebate_cap_currency (TEXT, nullable)
  Currency of rebate_cap_amount (e.g. "ZAR").

ISSUE 1 — Australia Location Offset: rate 16.5% → 30%
------------------------------------------------------
The Location Offset was raised from 16.5% to 30% effective 1 July 2023 for
productions commencing principal photography on or after that date.
Source: Screen Australia (screenaustralia.gov.au)

Also updates rate_type from 'tax_offset' → 'cash_rebate' for consistency.
The ATL deduction is NOT applied (cash_rebate is not in _TAX_CREDIT_RATE_TYPES),
preserving the correct existing behaviour.

Also fixes the duplicate scraper row ("Location Offset & PDV Offset (International)")
which still had rate_gross = 16.5 from z2c3d4e5f6g7.

qualifying_spend_min remains at 20,000,000 AUD (A$20M confirmed by Screen
Australia official website for features — previous d5e6f7g8h9i0 value is correct).

ISSUE 2 — BC Production Services Tax Credit (PSTC): missing programme
----------------------------------------------------------------------
Only BC FIBC (40% of qualified BC labour, Canadian-controlled corps only) existed
in the DB.  The BC Production Services Tax Credit (PSTC) — 36% of qualified BC
labour, directly accessible to foreign-owned corporations — was missing entirely.
This caused reports to tell foreign producers the 36% BC rate was unavailable.
Source: Province of British Columbia / Creative BC (creativebc.com)
Effective: January 2025 (BC Budget 2025)

ISSUE 3 — Georgia EIIA wage cap: W-2 vs loan-out distinction
------------------------------------------------------------
The existing $500K per-person cap warning implied the cap applied to all
individuals.  In fact the cap ONLY applies to W-2 employees.  Loan-outs,
personal service companies, and 1099 arrangements have NO per-person cap,
provided Georgia income tax withholding is remitted.  Most ATL talent on
high-budget features is paid via loan-out structures.
Source: Georgia Department of Economic Development / Georgia Film Office

ISSUE 4 — South Africa Foreign Film incentive: R25M rebate cap + DTIC delays
----------------------------------------------------------------------------
The R25M per-project maximum grant was not enforced in calculations — reports
modelled 25% × full budget (e.g. ~£5M on £20M) when actual maximum is R25M
(≈£1.05M).  rebate_cap_amount and rebate_cap_currency are now set so the
validator enforces the ceiling in _compute_corrected_rebate().

Also updates payment_reliability to 0.25 and strengthens warnings to reflect
significant DTIC payment processing delays reported by industry as of 2026.
Source: DTIC South Africa (thedtic.gov.za)
"""
import json as _json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "e6f7g8h9i0j1"
down_revision = "d5e6f7g8h9i0"
branch_labels = None
depends_on = None


# ─── Issue 1 — Australia Location Offset ─────────────────────────────────────

_AU_TERRITORY = "Australia"
_AU_PROGRAM = "Location Offset (Foreign Productions)"
_AU_PROGRAM_DUPE = "Location Offset & PDV Offset (International)"

_AU_NEW_RATE = "30% of qualifying Australian production expenditure (QAPE)"
_AU_NEW_RATE_GROSS = 30.0
_AU_NEW_RATE_NET = 30.0
_AU_NEW_RATE_TYPE = "cash_rebate"

_AU_OLD_RATE = "16.5% of qualifying Australian production expenditure"
_AU_OLD_RATE_GROSS = 16.5
_AU_OLD_RATE_NET = 16.5
_AU_OLD_RATE_TYPE = "tax_offset"


# ─── Issue 2 — BC PSTC new row ───────────────────────────────────────────────

_BC_PSTC_ROW = {
    "territory": "British Columbia",
    "program": "BC Production Services Tax Credit (PSTC)",
    "rate": "36% of qualified BC labour expenditure",
    "rate_gross": 36.0,
    "rate_net": 36.0,
    "rate_type": "tax_credit",
    "qualifying_spend_type": "labour",
    "qualifying_spend_labour_pct": 35.0,
    "scope": "regional",
    "parent_territory": "Canada",
    "stacking_group": "canada_screen",
    "stackable_with": _json.dumps(["Canada Federal PSTC (Production Services Tax Credit)"]),
    # NULL nationality_requirements = no restriction; foreign producers are eligible directly
    "nationality_requirements": None,
    "co_production_eligible": True,
    "spv_eligible": False,
    "payment_reliability": 0.85,
    "payment_timeline_days_min": 90,
    "payment_timeline_days_max": 270,
    "payment_timeline_notes": (
        "3-9 months after CRA assessment. "
        "Stackable with Canada Federal PSTC (16% of qualifying Canadian labour)."
    ),
    "eligibility_rules_json": _json.dumps([
        {
            "rule": "Accredited production corporation registered with Creative BC required",
            "required": True,
        },
        {
            "rule": "Qualifying BC labour expenditure required",
            "required": True,
        },
        {
            "rule": (
                "36% basic rate applies to productions commencing principal photography "
                "after December 31, 2024 (increased from 28% per BC Budget 2025)"
            ),
            "required": True,
        },
    ]),
    "warnings_json": _json.dumps([
        (
            "APPLIES TO QUALIFIED BC LABOUR EXPENDITURE ONLY — not total production budget. "
            "Typical qualifying BC labour is 30–45% of a production budget."
        ),
        (
            "Foreign-owned corporations are directly eligible — unlike BC Film Incentive BC "
            "(FIBC) which requires a Canadian-controlled corporation."
        ),
        (
            "Additional credits may be available: 6% Regional, 6% Distant Location, "
            "2% Major Production (for accredited productions meeting thresholds)."
        ),
        "CRA assessment can take 4-12 months — do not treat as investor-bankable cash flow.",
    ]),
    "source_name": "Province of British Columbia / Creative BC",
    "source_url": (
        "https://www2.gov.bc.ca/gov/content/taxes/income-taxes/corporate/credits/"
        "production-services"
    ),
    "status": "active",
    "last_verified_at": "2026-03-21",
}


# ─── Issue 3 — Georgia EIIA W-2 vs loan-out distinction ──────────────────────

_GA_TERRITORY = "Georgia (USA)"
_GA_PROGRAM = "Georgia Entertainment Industry Investment Act"

_GA_NEW_WARNINGS = _json.dumps([
    "Transferable credit \u2014 sold at ~88-90\u00a2 on the dollar",
    "Political risk: periodic legislative challenges to programme",
    (
        "$500K cap applies to W-2 EMPLOYEES ONLY \u2014 wages above $500,000 per W-2 individual "
        "do not count as qualifying spend. "
        "IMPORTANT: Loan-outs, personal service companies, and 1099 arrangements are NOT "
        "subject to this per-person cap, provided Georgia income tax withholding is remitted "
        "by the production company. "
        "Most ATL talent (principal cast, directors) on high-budget features is structured "
        "via loan-out \u2014 verify your specific payroll with a Georgia CPA before reducing "
        "your modelled credit for ATL costs."
    ),
])

_GA_NEW_ELIGIBILITY_RULES = _json.dumps([
    {"rule": "Minimum $500,000 Georgia spend", "required": True},
    {"rule": "Apply to Georgia Film Office before principal photography", "required": True},
    {
        "rule": (
            "Include Georgia promotional logo in deliverables for the additional 10% uplift"
        ),
        "required": False,
    },
    {
        "rule": (
            "$500,000 per-person cap applies to W-2 wages only \u2014 loan-outs and personal "
            "service companies are exempt provided Georgia income tax withholding is remitted "
            "by the production company"
        ),
        "required": True,
    },
])

# Previous values for downgrade (set by c4d5e6f7g8h9)
_GA_OLD_WARNINGS = (
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
_GA_OLD_ELIGIBILITY_RULES = (
    "["
    '{"rule":"Minimum $500K Georgia spend","required":true},'
    '{"rule":"Apply to Georgia Film Office before principal photography","required":true},'
    '{"rule":"Include Georgia promotional logo in deliverables for the additional 10% uplift","required":false},'
    '{"rule":"Individual W-2 wages capped at $500,000 per person for credit calculation'
    " \u2014 amounts above this threshold are excluded from qualifying spend\","
    '"required":true}'
    "]"
)


# ─── Issue 4 — South Africa rebate cap + DTIC delay warning ──────────────────

_SA_TERRITORY = "South Africa"
_SA_PROGRAM = "Foreign Film & TV Production Incentive"

_SA_REBATE_CAP_AMOUNT = 25_000_000.0
_SA_REBATE_CAP_CURRENCY = "ZAR"
_SA_NEW_PAYMENT_RELIABILITY = 0.25

_SA_NEW_WARNINGS = _json.dumps([
    "Payment timeline 9-15 months \u2014 budget cash flow accordingly",
    (
        "DTIC PAYMENT DELAYS: Industry reports significant backlogs (12-24+ months) in DTIC "
        "grant processing as of early 2026, with an estimated R600M\u2013R1B in outstanding "
        "rebates. Do not include in investor cash-flow projections without DTIC pre-approval "
        "confirmation."
    ),
    (
        "R25M PER-PROJECT CAP: Maximum grant is R25 million per project regardless of "
        "budget size. This cap is enforced in the financial model \u2014 the headline rebate "
        "figure reflects the capped amount, not 25% of total budget."
    ),
    "ZAR exchange rate volatility risk",
    "Minimum 50% of principal photography days must be filmed in South Africa",
    (
        "Special Purpose Corporate Vehicle (SPCV) must be registered in South Africa \u2014 "
        "requires B-BBEE Level 4 compliance"
    ),
])

# Previous values for downgrade (set by b2c3d4e5f6g8)
_SA_OLD_WARNINGS = (
    '['
    '"Payment timeline 9-15 months \u2014 budget cash flow accordingly",'
    '"DTIC approval backlog can extend beyond 15 months",'
    '"ZAR exchange rate volatility risk",'
    '"DTIC annual programme budget (~R25M practical per-project limit) \u2014 rebate is subject'
    " to available annual funding; verify availability with DTIC before treating as bankable\","
    '"Minimum 50% of principal photography days in South Africa required \u2014 a short'
    " secondary unit does not qualify; full programme access requires substantial SA shoot\""
    ']'
)
_SA_OLD_PAYMENT_RELIABILITY = 0.4  # approximate prior value


def upgrade() -> None:
    conn = op.get_bind()

    # ── Schema: add rebate cap columns ────────────────────────────────────────
    op.add_column(
        "incentive_programs",
        sa.Column("rebate_cap_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "incentive_programs",
        sa.Column("rebate_cap_currency", sa.Text(), nullable=True),
    )

    # ── Issue 1: Australia Location Offset rate 16.5% → 30% ──────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = :rate_gross, "
            "    rate_net = :rate_net, "
            "    rate_type = :rate_type, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _AU_NEW_RATE,
            "rate_gross": _AU_NEW_RATE_GROSS,
            "rate_net": _AU_NEW_RATE_NET,
            "rate_type": _AU_NEW_RATE_TYPE,
            "territory": _AU_TERRITORY,
            "program": _AU_PROGRAM,
        },
    )

    # Also fix the duplicate scraper row that still had 16.5
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate_gross = :rate_gross, "
            "    rate_net = :rate_net, "
            "    rate_type = :rate_type, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate_gross": _AU_NEW_RATE_GROSS,
            "rate_net": _AU_NEW_RATE_NET,
            "rate_type": _AU_NEW_RATE_TYPE,
            "territory": _AU_TERRITORY,
            "program": _AU_PROGRAM_DUPE,
        },
    )

    # ── Issue 2: INSERT BC PSTC ───────────────────────────────────────────────
    bc_pstc_row = dict(_BC_PSTC_ROW)
    bc_pstc_row["id"] = str(uuid4())
    cols = ", ".join(bc_pstc_row.keys())
    placeholders = ", ".join(f":{k}" for k in bc_pstc_row.keys())
    conn.execute(
        sa.text(f"INSERT INTO incentive_programs ({cols}) VALUES ({placeholders})"),
        bc_pstc_row,
    )

    # ── Issue 3: Georgia EIIA — clarify W-2 vs loan-out in cap warning ────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET warnings_json = :warnings, "
            "    eligibility_rules_json = :rules, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "warnings": _GA_NEW_WARNINGS,
            "rules": _GA_NEW_ELIGIBILITY_RULES,
            "territory": _GA_TERRITORY,
            "program": _GA_PROGRAM,
        },
    )

    # ── Issue 4: South Africa — set rebate cap + update reliability/warnings ──
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rebate_cap_amount = :rebate_cap_amount, "
            "    rebate_cap_currency = :rebate_cap_currency, "
            "    payment_reliability = :payment_reliability, "
            "    warnings_json = :warnings, "
            "    last_verified_at = '2026-03-21' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rebate_cap_amount": _SA_REBATE_CAP_AMOUNT,
            "rebate_cap_currency": _SA_REBATE_CAP_CURRENCY,
            "payment_reliability": _SA_NEW_PAYMENT_RELIABILITY,
            "warnings": _SA_NEW_WARNINGS,
            "territory": _SA_TERRITORY,
            "program": _SA_PROGRAM,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Issue 4 — restore South Africa
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rebate_cap_amount = NULL, "
            "    rebate_cap_currency = NULL, "
            "    payment_reliability = :payment_reliability, "
            "    warnings_json = :warnings "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "payment_reliability": _SA_OLD_PAYMENT_RELIABILITY,
            "warnings": _SA_OLD_WARNINGS,
            "territory": _SA_TERRITORY,
            "program": _SA_PROGRAM,
        },
    )

    # Issue 3 — restore Georgia warnings
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET warnings_json = :warnings, "
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

    # Issue 2 — remove BC PSTC row
    conn.execute(
        sa.text(
            "DELETE FROM incentive_programs "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "territory": _BC_PSTC_ROW["territory"],
            "program": _BC_PSTC_ROW["program"],
        },
    )

    # Issue 1 — restore Australia Location Offset to 16.5%
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = :rate_gross, "
            "    rate_net = :rate_net, "
            "    rate_type = :rate_type "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _AU_OLD_RATE,
            "rate_gross": _AU_OLD_RATE_GROSS,
            "rate_net": _AU_OLD_RATE_NET,
            "rate_type": _AU_OLD_RATE_TYPE,
            "territory": _AU_TERRITORY,
            "program": _AU_PROGRAM,
        },
    )
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate_gross = :rate_gross, "
            "    rate_net = :rate_net, "
            "    rate_type = :rate_type "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate_gross": _AU_OLD_RATE_GROSS,
            "rate_net": _AU_OLD_RATE_NET,
            "rate_type": _AU_OLD_RATE_TYPE,
            "territory": _AU_TERRITORY,
            "program": _AU_PROGRAM_DUPE,
        },
    )

    # Drop new columns last
    op.drop_column("incentive_programs", "rebate_cap_currency")
    op.drop_column("incentive_programs", "rebate_cap_amount")
