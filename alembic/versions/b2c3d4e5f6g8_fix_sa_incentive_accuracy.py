"""fix_sa_incentive_accuracy

Revision ID: b2c3d4e5f6g8
Revises: a1b2c3d4e5f7
Create Date: 2026-03-18 12:00:00.000000

Corrects the South Africa Foreign Film & TV Production Incentive row to
reflect official DTIC programme requirements:

1. qualifying_spend_min: ZAR 12M → ZAR 15M (official current minimum)
2. eligibility_rules_json: add the 50% principal photography requirement
3. warnings_json: add DTIC annual budget constraint warning
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6g8"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None

_TERRITORY = "South Africa"
_PROGRAM = "Foreign Film & TV Production Incentive"

# Updated eligibility rules (ZAR 15M min + 50% photography rule)
_NEW_ELIGIBILITY_RULES = (
    '['
    '{"rule":"Minimum qualifying SA spend of ZAR 15M","required":true},'
    '{"rule":"Minimum 50% of principal photography days must be in South Africa","required":true},'
    '{"rule":"Must use South African production services company","required":true},'
    '{"rule":"Application to DTIC before principal photography","required":true}'
    ']'
)

# Updated warnings (add DTIC annual budget / R25M practical cap note)
_NEW_WARNINGS = (
    '['
    '"Payment timeline 9-15 months — budget cash flow accordingly",'
    '"DTIC approval backlog can extend beyond 15 months",'
    '"ZAR exchange rate volatility risk",'
    '"DTIC annual programme budget (~R25M practical per-project limit) — rebate is subject to available annual funding; verify availability with DTIC before treating as bankable",'
    '"Minimum 50% of principal photography days in South Africa required — a short secondary unit does not qualify; full programme access requires substantial SA shoot"'
    ']'
)

# Previous values for downgrade
_OLD_ELIGIBILITY_RULES = (
    '['
    '{"rule":"Minimum qualifying SA spend of ZAR 12M","required":true},'
    '{"rule":"Must use South African production services company","required":true},'
    '{"rule":"Application to DTIC before principal photography","required":true}'
    ']'
)

_OLD_WARNINGS = (
    '['
    '"Payment timeline 9-15 months — budget cash flow accordingly",'
    '"DTIC approval backlog can extend beyond 15 months",'
    '"ZAR exchange rate volatility risk"'
    ']'
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_min = :qs_min, "
            "    eligibility_rules_json = :rules, "
            "    warnings_json = :warnings, "
            "    updated_at = NOW() "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "qs_min": 15_000_000.0,
            "rules": _NEW_ELIGIBILITY_RULES,
            "warnings": _NEW_WARNINGS,
            "territory": _TERRITORY,
            "program": _PROGRAM,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_min = :qs_min, "
            "    eligibility_rules_json = :rules, "
            "    warnings_json = :warnings, "
            "    updated_at = NOW() "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "qs_min": 12_000_000.0,
            "rules": _OLD_ELIGIBILITY_RULES,
            "warnings": _OLD_WARNINGS,
            "territory": _TERRITORY,
            "program": _PROGRAM,
        },
    )
