"""seed_regional_incentives_and_nationality_data

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-03-13 10:15:00.000000

1. Inserts regional incentive programmes (Creative Scotland, Wales Screen,
   NI Screen, Georgia, New Mexico, BC FIBC, etc.)
2. Updates existing national incentives with stacking_group and stackable_with.
3. Seeds nationality_requirements, co_production_eligible,
   co_production_treaties, and spv_eligible for all existing programmes.
"""
from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "o9p0q1r2s3t4"
down_revision = "n8o9p0q1r2s3"
branch_labels = None
depends_on = None

_NOW = datetime(2026, 3, 13, tzinfo=timezone.utc).isoformat()

# ── Regional incentive programmes to INSERT ──────────────────────────────────

_REGIONAL_INCENTIVES = [
    {
        "territory": "Scotland",
        "program": "Creative Scotland Production Growth Fund",
        "rate": "Up to £500K per project (grant)",
        "rate_gross": 0.0,
        "rate_net": 0.0,
        "rate_type": "grant",
        "cap": "£500,000 per project",
        "cap_amount": 500000.0,
        "cap_currency": "GBP",
        "qualifying_spend_min": None,
        "qualifying_spend_currency": "GBP",
        "payment_timeline_days_min": 30,
        "payment_timeline_days_max": 90,
        "payment_timeline_notes": "Grant disbursed in tranches; first tranche on commencement, remainder on delivery milestones.",
        "eligibility_rules_json": '[{"rule":"Must shoot a significant portion of principal photography in Scotland","required":true},{"rule":"Must demonstrate Scottish crew employment benefit","required":true},{"rule":"Application before start of principal photography","required":true}]',
        "currency": "GBP",
        "warnings_json": '["Discretionary fund — not guaranteed. Competitive application process.","Fund budget refreshes annually — apply early in cycle."]',
        "source_name": "Creative Scotland",
        "source_url": "https://www.creativescotland.com/funding/funding-programmes/screen",
        "status": "active",
        "scope": "regional",
        "parent_territory": "United Kingdom",
        "stacking_group": "uk_screen",
        "stackable_with": '["Audio Visual Expenditure Credit (AVEC)", "Independent Film Tax Credit (IFTC)"]',
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": None,
        "spv_eligible": True,
    },
    {
        "territory": "Wales",
        "program": "Ffilm Cymru Wales Production Fund",
        "rate": "Up to £500K per project (grant/equity)",
        "rate_gross": 0.0,
        "rate_net": 0.0,
        "rate_type": "grant",
        "cap": "£500,000 per project",
        "cap_amount": 500000.0,
        "cap_currency": "GBP",
        "qualifying_spend_min": None,
        "qualifying_spend_currency": "GBP",
        "payment_timeline_days_min": 30,
        "payment_timeline_days_max": 90,
        "payment_timeline_notes": "Grant/equity disbursed in tranches tied to production milestones.",
        "eligibility_rules_json": '[{"rule":"Must demonstrate significant economic benefit to Wales","required":true},{"rule":"Significant Welsh crew and facilities usage","required":true}]',
        "currency": "GBP",
        "warnings_json": '["Discretionary fund — competitive application.","Often requires Welsh language or cultural connection."]',
        "source_name": "Ffilm Cymru Wales",
        "source_url": "https://ffilmcymruwales.com/funding",
        "status": "active",
        "scope": "regional",
        "parent_territory": "United Kingdom",
        "stacking_group": "uk_screen",
        "stackable_with": '["Audio Visual Expenditure Credit (AVEC)", "Independent Film Tax Credit (IFTC)"]',
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": None,
        "spv_eligible": True,
    },
    {
        "territory": "Northern Ireland",
        "program": "Northern Ireland Screen Fund",
        "rate": "Up to £800K per project (grant/equity)",
        "rate_gross": 0.0,
        "rate_net": 0.0,
        "rate_type": "grant",
        "cap": "£800,000 per project",
        "cap_amount": 800000.0,
        "cap_currency": "GBP",
        "qualifying_spend_min": None,
        "qualifying_spend_currency": "GBP",
        "payment_timeline_days_min": 30,
        "payment_timeline_days_max": 120,
        "payment_timeline_notes": "Disbursed against agreed milestones. NI Screen has historically fast turnaround.",
        "eligibility_rules_json": '[{"rule":"Must shoot in Northern Ireland","required":true},{"rule":"Demonstrate NI crew and facilities benefit","required":true}]',
        "currency": "GBP",
        "warnings_json": '["Discretionary fund — competitive.","Strong track record (Game of Thrones legacy) means high demand."]',
        "source_name": "Northern Ireland Screen",
        "source_url": "https://www.northernirelandscreen.co.uk/funding/",
        "status": "active",
        "scope": "regional",
        "parent_territory": "United Kingdom",
        "stacking_group": "uk_screen",
        "stackable_with": '["Audio Visual Expenditure Credit (AVEC)", "Independent Film Tax Credit (IFTC)"]',
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": None,
        "spv_eligible": True,
    },
    {
        "territory": "Georgia (USA)",
        "program": "Georgia Entertainment Industry Investment Act",
        "rate": "20% base + 10% uplift with GA logo = 30%",
        "rate_gross": 30.0,
        "rate_net": 30.0,
        "rate_type": "transferable_tax_credit",
        "rate_tier_json": '[{"label":"Base credit","rate_gross":20},{"label":"With Georgia promotional logo","rate_gross":10}]',
        "cap": "No cap on qualifying spend (min $500K)",
        "cap_amount": None,
        "cap_currency": "USD",
        "qualifying_spend_min": 500000.0,
        "qualifying_spend_currency": "USD",
        "payment_timeline_days_min": 60,
        "payment_timeline_days_max": 120,
        "payment_timeline_notes": "Credits issued 2-4 months after DOR audit. Transferable at ~88-90 cents on the dollar.",
        "eligibility_rules_json": '[{"rule":"Minimum $500K Georgia spend","required":true},{"rule":"Apply to Georgia Film Office before production","required":true},{"rule":"Include Georgia promotional logo for extra 10%","required":false}]',
        "currency": "USD",
        "warnings_json": '["Transferable credit — sold at ~88-90% of face value","Political risk: periodic legislative challenges to programme"]',
        "source_name": "Georgia Film Office",
        "source_url": "https://www.georgia.org/industries/film-entertainment",
        "status": "active",
        "scope": "regional",
        "parent_territory": "USA",
        "stacking_group": None,
        "stackable_with": None,
        "nationality_requirements": None,
        "co_production_eligible": False,
        "co_production_treaties": None,
        "spv_eligible": True,
    },
    {
        "territory": "New Mexico",
        "program": "New Mexico Film Tax Credit",
        "rate": "25-35% depending on qualification",
        "rate_gross": 35.0,
        "rate_net": 35.0,
        "rate_type": "refundable_tax_credit",
        "rate_tier_json": '[{"label":"Base credit","rate_gross":25},{"label":"Television pilot/series uplift","rate_gross":5},{"label":"Qualifying facility uplift","rate_gross":5}]',
        "cap": "No per-project cap",
        "cap_amount": None,
        "cap_currency": "USD",
        "qualifying_spend_min": None,
        "qualifying_spend_currency": "USD",
        "payment_timeline_days_min": 90,
        "payment_timeline_days_max": 180,
        "payment_timeline_notes": "3-6 months post-audit. Refundable — paid directly, not transferable.",
        "eligibility_rules_json": '[{"rule":"Must apply to NM Film Office before production","required":true},{"rule":"Local hire and vendor requirements","required":true}]',
        "currency": "USD",
        "warnings_json": '["Annual programme budget cap — apply early","Local crew pool smaller than Georgia/California"]',
        "source_name": "New Mexico Film Office",
        "source_url": "https://nmfilm.com/film-incentive/",
        "status": "active",
        "scope": "regional",
        "parent_territory": "USA",
        "stacking_group": None,
        "stackable_with": None,
        "nationality_requirements": None,
        "co_production_eligible": False,
        "co_production_treaties": None,
        "spv_eligible": True,
    },
    {
        "territory": "British Columbia",
        "program": "BC Film Incentive BC Tax Credit (FIBC)",
        "rate": "35% of qualified BC labour",
        "rate_gross": 35.0,
        "rate_net": 35.0,
        "rate_type": "tax_credit",
        "cap": "No cap",
        "cap_amount": None,
        "cap_currency": "CAD",
        "qualifying_spend_min": None,
        "qualifying_spend_currency": "CAD",
        "payment_timeline_days_min": 90,
        "payment_timeline_days_max": 270,
        "payment_timeline_notes": "3-9 months after CRA assessment. Federal PSTC stacks on top.",
        "eligibility_rules_json": '[{"rule":"Canadian-controlled corporation","required":true},{"rule":"Qualifying BC labour expenditure","required":true}]',
        "currency": "CAD",
        "warnings_json": '["Only available to Canadian-controlled corporations","Assessment timeline can extend to 9 months"]',
        "source_name": "Creative BC",
        "source_url": "https://www.creativebc.com/sector-development/motion-picture-tax-credits",
        "status": "active",
        "scope": "regional",
        "parent_territory": "Canada",
        "stacking_group": "canada_screen",
        "stackable_with": '["Canada Federal PSTC"]',
        "nationality_requirements": '["CA"]',
        "co_production_eligible": True,
        "co_production_treaties": '["GB","FR","AU","DE","IT","IE","NZ","ZA"]',
        "spv_eligible": False,
    },
    {
        "territory": "Ireland",
        "program": "Section 481 Tax Credit",
        "rate": "32% of eligible expenditure",
        "rate_gross": 32.0,
        "rate_net": 32.0,
        "rate_type": "tax_credit",
        "cap": "€70M eligible expenditure cap per project",
        "cap_amount": 70000000.0,
        "cap_currency": "EUR",
        "qualifying_spend_min": 250000.0,
        "qualifying_spend_currency": "EUR",
        "payment_timeline_days_min": 60,
        "payment_timeline_days_max": 180,
        "payment_timeline_notes": "2-6 months from Revenue Commissioners. Cultural test certification via Screen Ireland required first.",
        "eligibility_rules_json": '[{"rule":"Must pass cultural test via Screen Ireland","required":true},{"rule":"Minimum €250K eligible expenditure","required":true},{"rule":"Irish-registered qualifying company required","required":true}]',
        "currency": "EUR",
        "warnings_json": '["Requires Irish qualifying company (SPV acceptable)","Cultural test can take 4-8 weeks"]',
        "source_name": "Screen Ireland / Revenue Commissioners",
        "source_url": "https://www.screenireland.ie/filming/section-481",
        "status": "active",
        "scope": "national",
        "parent_territory": None,
        "stacking_group": "ireland_screen",
        "stackable_with": None,
        "nationality_requirements": '["IE"]',
        "co_production_eligible": True,
        "co_production_treaties": '["GB","FR","DE","CA","AU","BE","LU","NL","IT","DK","NO","SE"]',
        "spv_eligible": True,
    },
]

# ── Nationality data updates for EXISTING incentive programmes ───────────────

_NATIONALITY_UPDATES = [
    {
        "territory": "United Kingdom",
        "program": "Audio Visual Expenditure Credit (AVEC)",
        "nationality_requirements": '["GB"]',
        "co_production_eligible": True,
        "co_production_treaties": '["IE","FR","DE","AU","CA","ZA","NZ","IT","IL","JM","IN","MO","PL","CN","BR"]',
        "spv_eligible": True,
        "stacking_group": "uk_screen",
    },
    {
        "territory": "United Kingdom",
        "program": "Independent Film Tax Credit (IFTC)",
        "nationality_requirements": '["GB"]',
        "co_production_eligible": True,
        "co_production_treaties": '["IE","FR","DE","AU","CA","ZA","NZ","IT","IL","JM","IN","MO","PL","CN","BR"]',
        "spv_eligible": True,
        "stacking_group": "uk_screen",
    },
    {
        "territory": "United Kingdom",
        "program": "VFX Expenditure Credit (Uplift)",
        "nationality_requirements": '["GB"]',
        "co_production_eligible": True,
        "co_production_treaties": '["IE","FR","DE","AU","CA","ZA","NZ","IT","IL","JM","IN","MO","PL","CN","BR"]',
        "spv_eligible": True,
        "stacking_group": "uk_screen",
    },
    {
        "territory": "South Africa",
        "program": "Foreign Film & TV Production Incentive",
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": '["GB","FR","DE","CA","AU","IT","IE","NL"]',
        "spv_eligible": False,
    },
    {
        "territory": "Hungary",
        "program": "Hungarian Film Incentive",
        "nationality_requirements": None,
        "co_production_eligible": True,
        "co_production_treaties": '["GB","FR","DE","CA","AU","IT","ES","IL"]',
        "spv_eligible": True,
    },
    {
        "territory": "Malta",
        "program": "Malta Film Tax Incentive (MFTI)",
        "nationality_requirements": '["MT","EU"]',
        "co_production_eligible": True,
        "co_production_treaties": '["GB","FR","DE","CA","AU","IT"]',
        "spv_eligible": True,
    },
    {
        "territory": "Nigeria",
        "program": "No National Cash Rebate",
        "nationality_requirements": None,
        "co_production_eligible": False,
        "co_production_treaties": None,
        "spv_eligible": False,
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    # 1. Insert regional incentives
    for seed in _REGIONAL_INCENTIVES:
        territory = seed["territory"]
        program = seed["program"]

        # Check if already exists
        result = conn.execute(
            sa.text(
                "SELECT id FROM incentive_programs "
                "WHERE territory = :territory AND program = :program "
                "LIMIT 1"
            ),
            {"territory": territory, "program": program},
        )
        existing = result.fetchone()

        if existing:
            # Update with new fields
            set_parts = []
            params = {"id": existing[0]}
            for key, val in seed.items():
                if key in ("territory", "program"):
                    continue
                set_parts.append(f"{key} = :{key}")
                params[key] = val
            params["last_verified_at"] = _NOW
            set_parts.append("last_verified_at = :last_verified_at")
            conn.execute(
                sa.text(
                    f"UPDATE incentive_programs SET {', '.join(set_parts)} WHERE id = :id"
                ),
                params,
            )
        else:
            seed["id"] = str(uuid4())
            seed["created_at"] = _NOW
            seed["last_verified_at"] = _NOW
            seed["last_updated"] = _NOW
            cols = ", ".join(seed.keys())
            placeholders = ", ".join(f":{k}" for k in seed.keys())
            conn.execute(
                sa.text(
                    f"INSERT INTO incentive_programs ({cols}) VALUES ({placeholders})"
                ),
                seed,
            )

    # 2. Update existing national incentives with nationality / stacking data
    for upd in _NATIONALITY_UPDATES:
        territory = upd.pop("territory")
        program = upd.pop("program")

        set_parts = []
        params = {"territory": territory, "program": program}
        for key, val in upd.items():
            set_parts.append(f"{key} = :{key}")
            params[key] = val

        if set_parts:
            conn.execute(
                sa.text(
                    f"UPDATE incentive_programs SET {', '.join(set_parts)} "
                    "WHERE territory = :territory AND program = :program"
                ),
                params,
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    # Remove inserted regional incentives
    for seed in _REGIONAL_INCENTIVES:
        conn.execute(
            sa.text(
                "DELETE FROM incentive_programs "
                "WHERE territory = :territory AND program = :program"
            ),
            {"territory": seed["territory"], "program": seed["program"]},
        )

    # Clear nationality fields on existing national rows
    conn.execute(
        sa.text(
            "UPDATE incentive_programs SET "
            "nationality_requirements = NULL, "
            "co_production_eligible = NULL, "
            "co_production_treaties = NULL, "
            "spv_eligible = NULL, "
            "stacking_group = NULL "
            "WHERE scope = 'national' OR scope IS NULL"
        )
    )
