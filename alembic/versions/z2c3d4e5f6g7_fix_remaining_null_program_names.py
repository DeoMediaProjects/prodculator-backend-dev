"""Fix remaining NULL program names in scraper-created incentive rows.

Revision ID: z2c3d4e5f6g7
Revises: z1b2c3d4e5f6
Create Date: 2026-03-15
"""
from alembic import op
import sqlalchemy as sa

revision = "z2c3d4e5f6g7"
down_revision = "z1b2c3d4e5f6"
branch_labels = None
depends_on = None

# Map: (territory, rate_pattern) → (program_name, rate_gross, rate_type, scope, source_name)
_FIXES = [
    {
        "territory": "Canada",
        "rate_like": "%25%",
        "program": "Canadian Film or Video Production Tax Credit (CPTC)",
        "rate_gross": 25.0,
        "rate_type": "tax_credit",
        "scope": "national",
        "source_name": "Canada Revenue Agency / CAVCO",
    },
    {
        "territory": "Australia",
        "rate_like": "%30%",
        "program": "Producer Offset",
        "rate_gross": 30.0,
        "rate_type": "tax_credit",
        "scope": "national",
        "source_name": "Screen Australia",
    },
    {
        "territory": "Australia",
        "rate_like": "%varies%",
        "program": "Location Offset & PDV Offset (International)",
        "rate_gross": 16.5,
        "rate_type": "tax_credit",
        "scope": "national",
        "source_name": "Screen Australia",
    },
    {
        "territory": "United States",
        "rate_like": "%20-30%",
        "program": "Georgia Entertainment Industry Investment Act",
        "rate_gross": 30.0,
        "rate_type": "tax_credit",
        "scope": "state",
        "source_name": "Georgia Department of Economic Development",
    },
    {
        "territory": "United States",
        "rate_like": "%30%",
        "program": "New York State Film Tax Credit Program",
        "rate_gross": 30.0,
        "rate_type": "tax_credit",
        "scope": "state",
        "source_name": "Empire State Development",
    },
    {
        "territory": "Italy",
        "rate_like": "%varies%",
        "program": "Italy MiC Film Tax Credit",
        "rate_gross": 40.0,
        "rate_type": "tax_credit",
        "scope": "national",
        "source_name": "MiC Direzione Generale Cinema",
    },
    {
        "territory": "Czech Republic",
        "rate_like": "%25-35%",
        "program": "Czech Film Incentive Programme",
        "rate_gross": 30.0,
        "rate_type": "cash_rebate",
        "scope": "national",
        "source_name": "Czech Film Commission / Czech Film Fund",
    },
    {
        "territory": "France",
        "rate_like": "%30%40%",
        "program": "TRIP (Tax Rebate for International Production)",
        "rate_gross": 30.0,
        "rate_type": "cash_rebate",
        "scope": "national",
        "source_name": "CNC",
    },
    {
        "territory": "Germany",
        "rate_like": "%30%",
        "program": "German Federal Film Fund (DFFF)",
        "rate_gross": 30.0,
        "rate_type": "cash_rebate",
        "scope": "national",
        "source_name": "FFA (German Federal Film Board)",
    },
    {
        "territory": "South Africa",
        "rate_like": "%Cash Rebate%",
        "program": "South Africa Film & TV Production Incentive",
        "rate_gross": 25.0,
        "rate_type": "cash_rebate",
        "scope": "national",
        "source_name": "DTIC South Africa",
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    for fix in _FIXES:
        result = conn.execute(sa.text("""
            UPDATE incentive_programs
            SET program = :program,
                rate_gross = :rate_gross,
                rate_type = :rate_type,
                scope = :scope,
                source_name = :source_name,
                last_verified_at = '2026-03-01'
            WHERE territory = :territory
              AND rate LIKE :rate_like
              AND program IS NULL
        """), fix)
        if result.rowcount:
            print(f"  Fixed {fix['territory']}: {fix['program']}")
        else:
            print(f"  No match for {fix['territory']} rate LIKE {fix['rate_like']}")

    # Check for any remaining NULLs
    remaining = conn.execute(sa.text(
        "SELECT territory, rate FROM incentive_programs WHERE program IS NULL"
    )).fetchall()
    if remaining:
        for r in remaining:
            print(f"  STILL NULL: {r[0]} | rate={r[1]}")
    else:
        print("  All incentive programs now have names ✓")


def downgrade() -> None:
    conn = op.get_bind()
    for fix in _FIXES:
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET program = NULL, rate_gross = NULL, rate_type = NULL, scope = NULL, source_name = NULL
            WHERE territory = :territory AND program = :program
        """), fix)
