"""fix_bc_pstc_labour_basis

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-08-19

BC PSTC was typed as a local-spend credit when it is a labour credit.

ROOT CAUSE
----------
The v4 source row carries:

    rateType  labour_credit
    qsType    local_spend
    qsCapPct  "100% of BC labour expenditure"
    qsBasis   "36% of accredited BC labour expenditure (raised from 28%, for
               productions starting principal photography after 31 Dec 2024)"

Every field except ``qsType`` says labour. ReportValidator._qualifying_spend_for
treats ``local_spend`` with a 100% cap as "the whole budget qualifies", so the
36% rate was applied to the budget less the standard 15% ATL estimate:

    GBP 20M budget -> qualifying spend GBP 17.0M -> credit GBP 6.12M

The real base is accredited BC labour expenditure only. At a typical 30% labour
share the base is nearer GBP 6M and the credit nearer GBP 2.16M — the reported
figure was roughly 2.8x too high, in a number that goes into investor documents.

FIX
---
``qualifying_spend_type = 'labour'``.

The labour branch requires a SOURCED ``qualifying_spend_labour_pct`` and returns
no figure without one, so BC will present without a computed rebate until a share
is sourced and recorded. That is deliberate and matches the rule already applied
to every other labour-only credit in this table: a programme is shown without a
working rather than with a confident wrong number. Inventing a labour share here
would reintroduce the fabricated-ratio problem the engine was built to refuse.

TO RESTORE A BC FIGURE
----------------------
Record a sourced share on the row, e.g.

    UPDATE incentive_programs
    SET qualifying_spend_labour_pct = <sourced value>
    WHERE territory = 'British Columbia' AND program ILIKE 'BC Production Services%'

with the source noted in ``internal_audit_notes``. Creative BC / the BC film
incentive guidance is the authority for an accredited-labour share.

Source for the rate itself (36%, post-2024 productions):
https://www2.gov.bc.ca/gov/content/taxes/income-taxes/corporate/credits/film-television
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v2w3x4y5z6a7"
down_revision = "u1v2w3x4y5z6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    result = conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_type = 'labour',
            last_verified_at      = '2026-08-19'
        WHERE territory = 'British Columbia'
          AND program ILIKE 'BC Production Services%'
          AND (status = 'active' OR status IS NULL)
    """))
    print(
        f"[v2w3x4y5z6a7] BC PSTC retyped as a labour credit on "
        f"{result.rowcount} row(s); it will present without a computed rebate "
        f"until qualifying_spend_labour_pct is sourced"
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_type = 'local_spend'
        WHERE territory = 'British Columbia'
          AND program ILIKE 'BC Production Services%'
    """))
