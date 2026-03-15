"""fix_incentive_data_accuracy

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-03-14 19:00:00.000000

Corrects incentive data accuracy issues identified during report QA:
- Malta: payment timeline 3-6 months (was 2-4), cultural test clarification
- South Africa: add reliability warnings, payment delay risk emphasis
- Hungary: emphasise service company requirement in warnings
- IFTC: clarify taper zone logic in warnings
"""
from alembic import op
import sqlalchemy as sa

revision = "t4u5v6w7x8y9"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None

# Each entry: (territory, program) → fields to UPDATE
_FIXES = [
    # ── Malta: correct payment timeline and cultural test language ────────
    {
        "territory": "Malta",
        "program": "Malta Film Tax Incentive (MFTI)",
        "updates": {
            "payment_timeline_days_min": 90,
            "payment_timeline_days_max": 180,
            "payment_timeline_notes": (
                "3-6 months after Malta Film Commission audit. "
                "Relatively fast by European standards but timelines vary by project complexity."
            ),
            "eligibility_rules_json": (
                '[{"rule":"Application to Malta Film Commission before production","required":true},'
                '{"rule":"Qualifying expenditure must be incurred in Malta","required":true},'
                '{"rule":"Cultural test OR EU co-production treaty qualification required","required":true},'
                '{"rule":"International ATL paid outside Malta does not qualify unless talent is physically based and paid via Maltese entity","required":true}]'
            ),
            "warnings_json": (
                '["€12.5M ATL (above-the-line) expenditure cap — international ATL paid outside Malta does not qualify unless talent is based and paid via Maltese entity",'
                '"Programme expires 29 October 2028 — check renewal status for later productions",'
                '"Limited local crew pool — key HODs may need to be brought in",'
                '"ATL on a typical production is 20-35% of total budget — non-qualifying ATL must be deducted from rebate calculation"]'
            ),
        },
    },
    # ── South Africa: add reliability warnings and payment risk ──────────
    {
        "territory": "South Africa",
        "program": "Foreign Film & TV Production Incentive",
        "updates": {
            "warnings_json": (
                '["Payment timeline 9-15 months — do NOT treat as investor-bankable. Budget cash flow independently",'
                '"DTIC approval backlog can extend payment beyond 15 months",'
                '"ZAR exchange rate volatility risk — consider hedging",'
                '"Programme reliability is lower than UK/Malta/Hungary — include contingency in financial plan",'
                '"Minimum qualifying SA spend of ZAR 12M ($2.5M USD approx.) — verify allocated spend meets threshold"]'
            ),
            "eligibility_rules_json": (
                '[{"rule":"Minimum qualifying SA spend of ZAR 12M (approx. $2.5M USD)","required":true},'
                '{"rule":"Must use South African production services company","required":true},'
                '{"rule":"Application to DTIC before principal photography","required":true},'
                '{"rule":"QSAPE qualifying spend must be incurred in South Africa","required":true}]'
            ),
        },
    },
    # ── Hungary: emphasise service company requirement ────────────────────
    {
        "territory": "Hungary",
        "program": "Hungarian Film Incentive",
        "updates": {
            "warnings_json": (
                '["Hungarian production service company is mandatory — producer cannot claim incentive directly",'
                '"HUF 3M per-person cap on individual above-the-line fees — reduces qualifying spend for high-fee talent",'
                '"NFI annual budget cap ~HUF 70B — queue risk in busy years",'
                '"Payment may be delayed if NFI audit backlog"]'
            ),
            "eligibility_rules_json": (
                '[{"rule":"Must apply to NFI before principal photography","required":true},'
                '{"rule":"Cultural test or bilateral co-production treaty","required":true},'
                '{"rule":"Hungarian production service company required — producer cannot claim directly","required":true},'
                '{"rule":"HUF 3M per-person cap on individual above-the-line fees","required":true}]'
            ),
        },
    },
    # ── UK IFTC: clarify taper zone in warnings ─────────────────────────
    {
        "territory": "United Kingdom",
        "program": "Independent Film Tax Credit (IFTC)",
        "updates": {
            "warnings_json": (
                '["Budget cap £23.5M — films above this threshold must use AVEC instead",'
                '"IFTC enhanced rate (53%/39.75% net) applies only to first £15M core expenditure — budgets with qualifying spend above £15M enter the AVEC taper zone",'
                '"Theatrical release required — direct-to-streaming titles do not qualify",'
                '"80% qualifying spend rule applies — only 80% of total budget can be treated as qualifying expenditure",'
                '"Always show NET rate (39.75%) to investors — gross rate (53%) overstates actual cash return"]'
            ),
        },
    },
    # ── UK AVEC: add qualifying spend reminder ───────────────────────────
    {
        "territory": "United Kingdom",
        "program": "Audio Visual Expenditure Credit (AVEC)",
        "updates": {
            "warnings_json": (
                '["80% qualifying spend rule applies — only 80% of total budget can be treated as qualifying expenditure",'
                '"Net rate after corporation tax is 25.5% — always present this figure to investors rather than the 34% gross rate",'
                '"BFI certification typically takes 12-16 weeks — apply early"]'
            ),
        },
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    for fix in _FIXES:
        territory = fix["territory"]
        program = fix["program"]
        updates = fix["updates"]

        set_clauses = ", ".join(f"{field} = :{field}" for field in updates)
        params = dict(updates)
        params["territory"] = territory
        params["program"] = program

        conn.execute(
            sa.text(
                f"UPDATE incentive_programs SET {set_clauses}, "  # noqa: S608
                f"updated_at = NOW() "
                f"WHERE territory = :territory AND program = :program"
            ),
            params,
        )


def downgrade() -> None:
    # No-op: previous migration data will be re-applied if needed
    pass
