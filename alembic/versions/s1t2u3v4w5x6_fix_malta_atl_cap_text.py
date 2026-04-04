"""fix_malta_atl_cap_text

Revision ID: s1t2u3v4w5x6
Revises: r0s1t2u3v4w5
Create Date: 2026-03-29

ROOT CAUSE
----------
Migration m4n5o6p7q8r9 (fix_thirteen_confirmed_errors) rewrote Malta's
eligibility_notes to lead with the 40% headline rate, but introduced an
error in the ATL cap description:

    "ATL cap: €1M OR 30% of Malta expenditure (whichever is lower)"

The correct rule, per Screen Malta's June 2024 updated guidelines, is:

    eligible above-the-line expenditures will be capped at either €1 million
    or 30% of total Maltese eligible spend, whichever is HIGHER

Source: Screen Malta / Cineuropa (June 2024 update)

The warnings_json was set correctly by i0j1k2l3m4n5 ("whichever is higher")
and was not overwritten by m4n5o6p7q8r9. So warnings_json is correct;
only eligibility_notes contained the wrong word.

IMPACT
------
For a €17M Malta spend: 30% = €5.1M > €1M, so the higher cap applies.
"Whichever is lower" would wrongly cap ATL at €1M, under-stating how much
ATL spend qualifies. This misleads producers modelling high-fee talent.

Last Verified: 2026-03-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "s1t2u3v4w5x6"
down_revision = "r0s1t2u3v4w5"
branch_labels = None
depends_on = None

_CORRECT = (
    "Cash rebate up to 40% of qualifying Maltese expenditure. Tiered structure: "
    "30% base rate on all qualifying Malta expenditure; "
    "35% when Malta is portrayed as Malta or qualifying Maltese cultural content is incorporated; "
    "40% when Malta Studios water tanks are used or maximum local resources are mobilised. "
    "Minimum spend: €250K qualifying Maltese expenditure. "
    "No per-project rebate cap. Scheme runs through October 2028. "
    "ATL cap: €1M OR 30% of Malta expenditure (whichever is higher), up to €5M maximum. "
    "Foreign productions access via Malta Film Commission / Screen Malta."
)

_WRONG = (
    "Cash rebate up to 40% of qualifying Maltese expenditure. Tiered structure: "
    "30% base rate on all qualifying Malta expenditure; "
    "35% when Malta is portrayed as Malta or qualifying Maltese cultural content is incorporated; "
    "40% when Malta Studios water tanks are used or maximum local resources are mobilised. "
    "Minimum spend: €250K qualifying Maltese expenditure. "
    "No per-project rebate cap. Scheme runs through October 2028. "
    "ATL cap: €1M OR 30% of Malta expenditure (whichever is lower) on above-the-line costs. "
    "Foreign productions access via Malta Film Commission / Screen Malta."
)


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = :notes,
            last_verified_at  = '2026-03-29'
        WHERE territory = 'Malta'
          AND program   = 'Malta Film Commission Cash Rebate'
          AND status    = 'active'
    """), {"notes": _CORRECT})

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Malta' "
            "AND program = 'Malta Film Commission Cash Rebate' "
            "AND eligibility_notes LIKE '%whichever is higher%'"
        ),
        expected_min=1,
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = :notes
        WHERE territory = 'Malta'
          AND program   = 'Malta Film Commission Cash Rebate'
          AND status    = 'active'
    """), {"notes": _WRONG})
