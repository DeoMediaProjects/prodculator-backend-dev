"""Fix missing Malta/Hungary incentives and bare UK program names.

Revision ID: z1b2c3d4e5f6
Revises: z0a1b2c3d4e5
Create Date: 2026-03-15
"""
from alembic import op
import sqlalchemy as sa
from uuid import uuid4
from datetime import datetime, timezone

revision = "z1b2c3d4e5f6"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Fix the two bare UK rows (program=NULL) ────────────────────
    # These are scraper-created rows with no program name or enriched fields.
    # Row 1: Tax Credit: 34% → AVEC (Audio-Visual Expenditure Credit)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET program = 'Audio-Visual Expenditure Credit (AVEC)',
            rate_gross = 34.0,
            rate_net = 25.5,
            rate_type = 'tax_credit',
            cap_amount = NULL,
            qualifying_spend_min = 0,
            qualifying_spend_currency = 'GBP',
            currency = 'GBP',
            scope = 'national',
            eligibility_rules_json = '["BFI cultural test certification required","Minimum 10% UK core expenditure","UK company must be responsible for production"]',
            payment_timeline_days_min = 90,
            payment_timeline_days_max = 180,
            payment_timeline_notes = 'Claimed via corporation tax return after accounting period ends',
            last_verified_at = '2026-03-01',
            source_name = 'HMRC / GOV.UK'
        WHERE territory = 'United Kingdom'
          AND rate LIKE '%34%'
          AND program IS NULL
    """))

    # Row 2: Tax Credit: 53% → UK Independent Film Tax Credit (IFTC)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET program = 'UK Independent Film Tax Credit (IFTC)',
            rate_gross = 53.0,
            rate_net = 39.75,
            rate_type = 'enhanced_tax_credit',
            cap_amount = NULL,
            qualifying_spend_min = 0,
            qualifying_spend_currency = 'GBP',
            currency = 'GBP',
            scope = 'national',
            eligibility_rules_json = '["Must pass BFI cultural test","Budget cap £20M","At least 10% UK core expenditure","Company must be UK-based"]',
            payment_timeline_days_min = 90,
            payment_timeline_days_max = 180,
            payment_timeline_notes = 'Claimed via corporation tax return; enhanced rate for qualifying indie films',
            last_verified_at = '2026-03-01',
            source_name = 'BFI / GOV.UK'
        WHERE territory = 'United Kingdom'
          AND rate LIKE '%53%'
          AND program IS NULL
    """))

    # ── 2. Insert Malta incentive (cash rebate) ───────────────────────
    now = datetime.now(timezone.utc).isoformat()
    malta_id = str(uuid4())
    conn.execute(sa.text("""
        INSERT INTO incentive_programs (
            id, territory, program, rate, status, rate_gross, rate_net, rate_type,
            cap_amount, qualifying_spend_min, qualifying_spend_currency, currency,
            scope, source_url, source_name, eligibility_rules_json,
            payment_timeline_days_min, payment_timeline_days_max, payment_timeline_notes,
            last_verified_at, created_at, updated_at
        ) VALUES (
            :id, 'Malta', 'Malta Film Commission Cash Rebate', '40% cash rebate (up to 45% with cultural merit bonus)',
            'active', 40.0, 40.0, 'cash_rebate', NULL, 100000, 'EUR', 'EUR',
            'national', 'https://www.maltafilmcommission.com/incentives/',
            'Malta Film Commission',
            '["Minimum qualifying expenditure €100K in Malta","Cultural test or creative merit assessment","Must use Malta-based crew/facilities","Application must be submitted before principal photography"]',
            60, 120,
            'Paid after completion and audit; interim payments possible for large productions',
            '2026-03-01', :now, :now
        )
    """), {"id": malta_id, "now": now})

    # ── 3. Insert Hungary incentive (cash rebate) ─────────────────────
    hungary_id = str(uuid4())
    conn.execute(sa.text("""
        INSERT INTO incentive_programs (
            id, territory, program, rate, status, rate_gross, rate_net, rate_type,
            cap_amount, qualifying_spend_min, qualifying_spend_currency, currency,
            scope, source_url, source_name, eligibility_rules_json,
            payment_timeline_days_min, payment_timeline_days_max, payment_timeline_notes,
            last_verified_at, created_at, updated_at
        ) VALUES (
            :id, 'Hungary', 'Hungarian Film Incentive (NFI)', '30% cash rebate on qualifying Hungarian spend',
            'active', 30.0, 30.0, 'cash_rebate', NULL, 0, 'HUF', 'EUR',
            'national', 'https://nfi.hu/en/filming-in-hungary/hungarian-film-incentive',
            'National Film Institute Hungary',
            '["Must register with NFI before production","Minimum 80% of post-production in Hungary (for post-only claims)","Cultural test not required for service productions","Indirect costs (up to 25% of direct costs) can qualify"]',
            90, 180,
            'Paid after final audit; can take 6-9 months post-completion. Interim payments possible.',
            '2026-03-01', :now, :now
        )
    """), {"id": hungary_id, "now": now})

    # ── 4. Insert Iceland incentive (we have the row but verify) ──────
    # Check if Iceland already has a proper incentive row
    result = conn.execute(sa.text(
        "SELECT COUNT(*) FROM incentive_programs WHERE territory = 'Iceland' AND program IS NOT NULL"
    ))
    if result.scalar() == 0:
        iceland_id = str(uuid4())
        conn.execute(sa.text("""
            INSERT INTO incentive_programs (
                id, territory, program, rate, status, rate_gross, rate_net, rate_type,
                cap_amount, qualifying_spend_min, qualifying_spend_currency, currency,
                scope, source_url, source_name, eligibility_rules_json,
                payment_timeline_days_min, payment_timeline_days_max, payment_timeline_notes,
                last_verified_at, created_at, updated_at
            ) VALUES (
                :id, 'Iceland', 'Iceland Film Reimbursement Scheme', '35% reimbursement on qualifying Icelandic production costs',
                'active', 35.0, 35.0, 'cash_rebate', NULL, 0, 'ISK', 'ISK',
                'national', 'https://www.icelandicfilmcentre.is/support/production-incentive/',
                'Icelandic Film Centre',
                '["Must apply before production begins","Production must benefit Icelandic film industry","Both local and foreign productions eligible","Music recordings also eligible"]',
                60, 120,
                'Reimbursement paid after production completion and audit',
                '2026-03-01', :now, :now
            )
        """), {"id": iceland_id, "now": now})

    # ── 5. Also fix any NULL-program incentive rows in other territories ──
    # Check for any other NULL program names
    result = conn.execute(sa.text(
        "SELECT id, territory, rate FROM incentive_programs WHERE program IS NULL"
    ))
    nulls = result.fetchall()
    for row in nulls:
        print(f"WARNING: incentive {row[0]} for {row[1]} still has program=NULL (rate={row[2]})")


def downgrade() -> None:
    conn = op.get_bind()

    # Revert UK program names
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET program = NULL, rate_gross = NULL, rate_net = NULL, rate_type = NULL,
            scope = NULL, eligibility_rules_json = NULL
        WHERE territory = 'United Kingdom'
          AND program IN ('Audio-Visual Expenditure Credit (AVEC)', 'UK Independent Film Tax Credit (IFTC)')
    """))

    # Remove Malta incentive
    conn.execute(sa.text(
        "DELETE FROM incentive_programs WHERE territory = 'Malta' AND program = 'Malta Film Commission Cash Rebate'"
    ))

    # Remove Hungary incentive
    conn.execute(sa.text(
        "DELETE FROM incentive_programs WHERE territory = 'Hungary' AND program = 'Hungarian Film Incentive (NFI)'"
    ))
