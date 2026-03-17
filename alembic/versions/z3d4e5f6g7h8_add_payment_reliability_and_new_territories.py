"""Add payment_reliability to incentive_programs and seed new territories.

v3 spec requires payment_reliability (0.0-1.0) for bankability labels
and incentive reliability scoring. Also seeds Portugal, Romania, Serbia,
Morocco incentive data per spec Section 10.

Revision ID: z3d4e5f6g7h8
Revises: z2c3d4e5f6g7
Create Date: 2026-03-16
"""
from alembic import op
import sqlalchemy as sa
from uuid import uuid4
from datetime import datetime, timezone

revision = "z3d4e5f6g7h8"
down_revision = "z2c3d4e5f6g7"
branch_labels = None
depends_on = None

# Spec-driven payment_reliability values per territory
_RELIABILITY_OVERRIDES = {
    "United Kingdom": 0.92,
    "Ireland": 0.90,
    "Malta": 0.88,
    "France": 0.85,
    "Australia": 0.88,
    "New Zealand": 0.87,
    "Canada": 0.90,
    "British Columbia": 0.90,
    "Georgia": 0.92,
    "Czech Republic": 0.82,
    "Hungary": 0.65,
    "Italy": 0.60,
    "Spain": 0.55,
    "South Africa": 0.55,
    "Nigeria": 0.0,
    "Germany": 0.80,
    "Iceland": 0.75,
    "Portugal": 0.60,
    "Morocco": 0.55,
    "Serbia": 0.50,
    "Romania": 0.55,
}

_NOW = datetime.now(timezone.utc).isoformat()


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Add payment_reliability column ──────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE incentive_programs "
        "ADD COLUMN IF NOT EXISTS payment_reliability float DEFAULT NULL"
    ))

    # ── 2. Seed defaults from payment_timeline_days_max ────────────────
    # ≤90 days → 0.90, 91-180 → 0.70, 181-365 → 0.50, >365 or NULL → 0.30
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET payment_reliability = CASE
            WHEN payment_timeline_days_max IS NOT NULL AND payment_timeline_days_max <= 90 THEN 0.90
            WHEN payment_timeline_days_max IS NOT NULL AND payment_timeline_days_max <= 180 THEN 0.70
            WHEN payment_timeline_days_max IS NOT NULL AND payment_timeline_days_max <= 365 THEN 0.50
            ELSE 0.30
        END
        WHERE payment_reliability IS NULL
    """))

    # ── 3. Override with spec-specific values ──────────────────────────
    for territory, reliability in _RELIABILITY_OVERRIDES.items():
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET payment_reliability = :reliability
            WHERE territory = :territory
        """), {"reliability": reliability, "territory": territory})

    # ── 4. Populate missing qualifying_spend_cap_pct ───────────────────
    # European programmes that follow the ~80% qualifying-spend rule but
    # were seeded without the field.  US state programmes generally allow
    # 100% of eligible spend (no cap_pct), so NULL is correct for them.
    _QS_CAP_OVERRIDES = {
        ("France", "TRIP (Tax Rebate for International Production)"): 80.0,
        ("Ireland", "Section 481 Tax Credit"): 80.0,
        ("Italy", "Italian Tax Credit for Foreign Productions"): 80.0,
        ("Malta", "Malta Film Tax Incentive (MFTI)"): 80.0,
    }
    for (territory, program), cap_pct in _QS_CAP_OVERRIDES.items():
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET qualifying_spend_cap_pct = :cap_pct
            WHERE territory = :territory
              AND program LIKE :program_pattern
              AND qualifying_spend_cap_pct IS NULL
        """), {
            "cap_pct": cap_pct,
            "territory": territory,
            "program_pattern": f"%{program}%",
        })

    # ── 5. Seed new territories (if missing) ───────────────────────────
    _seed_if_missing(conn, {
        "territory": "Portugal",
        "program": "Portugal Film Commission Cash Rebate",
        "rate": 25.0,
        "rate_gross": 25.0,
        "rate_net": 25.0,
        "rate_type": "cash_rebate",
        "cap_amount": None,
        "qualifying_spend_min": 500000,
        "qualifying_spend_currency": "EUR",
        "currency": "EUR",
        "scope": "national",
        "eligibility_rules_json": '["Minimum €500,000 qualifying Portuguese spend"]',
        "payment_timeline_notes": "Payment timeline variable — verify with Portugal Film Commission",
        "payment_timeline_days_min": 180,
        "payment_timeline_days_max": 365,
        "payment_reliability": 0.60,
        "source_name": "Portugal Film Commission",
        "source_url": "https://www.filmportugal.pt/en/incentive",
    })

    _seed_if_missing(conn, {
        "territory": "Morocco",
        "program": "Moroccan Film Support Fund (CCM)",
        "rate": 20.0,
        "rate_gross": 20.0,
        "rate_net": 20.0,
        "rate_type": "cash_rebate",
        "cap_amount": None,
        "qualifying_spend_min": 400000,  # ~MAD 5M
        "qualifying_spend_currency": "MAD",
        "currency": "MAD",
        "scope": "national",
        "eligibility_rules_json": '["Co-production agreement or CCM approval required","Minimum MAD 5,000,000 qualifying Moroccan spend"]',
        "payment_timeline_notes": "Payment terms variable — verify with CCM",
        "payment_timeline_days_min": 180,
        "payment_timeline_days_max": 365,
        "payment_reliability": 0.55,
        "source_name": "Centre Cinématographique Marocain",
        "source_url": "https://www.ccm.ma",
    })

    _seed_if_missing(conn, {
        "territory": "Serbia",
        "program": "Serbia Film Commission Cash Rebate",
        "rate": 25.0,
        "rate_gross": 25.0,
        "rate_net": 25.0,
        "rate_type": "cash_rebate",
        "cap_amount": None,
        "qualifying_spend_min": 215000,  # ~RSD 30M
        "qualifying_spend_currency": "RSD",
        "currency": "RSD",
        "scope": "national",
        "eligibility_rules_json": '["Open programme for foreign productions","Minimum RSD 30,000,000 qualifying Serbian spend"]',
        "payment_timeline_notes": "Newer programme — verify payment terms with Film Commission Serbia",
        "payment_timeline_days_min": 180,
        "payment_timeline_days_max": 365,
        "payment_reliability": 0.50,
        "source_name": "Film Commission Serbia",
        "source_url": "https://www.filmserbia.com/incentives",
    })

    _seed_if_missing(conn, {
        "territory": "Romania",
        "program": "Romanian Film Centre (CNC) Cash Rebate",
        "rate": 35.0,
        "rate_gross": 35.0,
        "rate_net": 35.0,
        "rate_type": "cash_rebate",
        "cap_amount": None,
        "qualifying_spend_min": 170000,  # ~RON 1M
        "qualifying_spend_currency": "RON",
        "currency": "RON",
        "scope": "national",
        "eligibility_rules_json": '["Cultural points system — achievable for international productions using Romanian crew","Minimum RON 1,000,000 qualifying Romanian spend"]',
        "payment_timeline_notes": "Payment timelines variable — verify current status with CNC Romania",
        "payment_timeline_days_min": 180,
        "payment_timeline_days_max": 365,
        "payment_reliability": 0.55,
        "source_name": "CNC Romania",
        "source_url": "https://www.cnc.ro",
    })


def _seed_if_missing(conn, data: dict) -> None:
    """Insert a row into incentive_programs only if no row exists for the territory."""
    result = conn.execute(sa.text(
        "SELECT COUNT(*) FROM incentive_programs WHERE territory = :territory"
    ), {"territory": data["territory"]})
    count = result.scalar()
    if count and count > 0:
        return

    row_id = str(uuid4())
    conn.execute(sa.text("""
        INSERT INTO incentive_programs (
            id, territory, program, rate, rate_gross, rate_net, rate_type,
            cap_amount, qualifying_spend_min, qualifying_spend_currency,
            currency, scope, eligibility_rules_json,
            payment_timeline_notes, payment_timeline_days_min,
            payment_timeline_days_max, payment_reliability,
            source_name, source_url, status, last_verified_at
        ) VALUES (
            :id, :territory, :program, :rate, :rate_gross, :rate_net, :rate_type,
            :cap_amount, :qualifying_spend_min, :qualifying_spend_currency,
            :currency, :scope, :eligibility_rules_json,
            :payment_timeline_notes, :payment_timeline_days_min,
            :payment_timeline_days_max, :payment_reliability,
            :source_name, :source_url, 'active', :last_verified_at
        )
    """), {
        "id": row_id,
        **data,
        "last_verified_at": _NOW,
    })


def downgrade() -> None:
    conn = op.get_bind()

    # Remove seeded territories
    for territory in ("Portugal", "Morocco", "Serbia", "Romania"):
        conn.execute(sa.text(
            "DELETE FROM incentive_programs WHERE territory = :territory"
        ), {"territory": territory})

    # Drop the column
    conn.execute(sa.text(
        "ALTER TABLE incentive_programs DROP COLUMN IF EXISTS payment_reliability"
    ))
