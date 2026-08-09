"""Programme-level format eligibility: status, conditions, source, verified date.

``applicable_formats`` already existed but carried no way to say whether the list
was authoritative. Read with the old rule (NULL means "all formats"), and NULL on
every one of the 49 rows, it meant every programme was modelled as accepting every
format — so a short film was quoted feature-scale rebates from programmes that may
exclude short films outright, with nothing in the output saying so.

This adds the metadata that distinguishes the three real cases:

    verified     applicable_formats is a complete whitelist; a format absent from
                 it is INELIGIBLE, not merely unlisted
    conditional  eligibility turns on something beyond format (runtime, theatrical
                 commitment, local spend); format_conditions carries the rule
    unknown      not established

Every existing row is backfilled to ``unknown``, never to eligible. The whole point
is that an unchecked programme must not present a rebate as confirmed, so the
default has to be the cautious one even though it makes more rows unverified today.

No eligibility values are populated here. Doing that requires per-programme
verification against the tax authority, legislation, film commission or programme
administrator, and a guess written into this column would read downstream as a
verified fact. The columns and the logic land first; the research follows.

Eligibility is deliberately per PROGRAMME rather than per territory: two programmes
in one territory routinely differ, and one may accept short animation while another
is feature-only.

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-08-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m3n4o5p6q7r8"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None

_TABLE = "incentive_programs"

_COLUMNS = (
    # Text rather than a native enum: the rest of this schema uses plain strings for
    # status columns, and widening an enum in Postgres is a migration of its own.
    sa.Column("format_eligibility_status", sa.Text(), nullable=True),
    sa.Column("format_conditions", sa.Text(), nullable=True),
    sa.Column("format_source_url", sa.Text(), nullable=True),
    sa.Column("format_verified_at", sa.Date(), nullable=True),
)


def _existing_columns(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _existing_columns(bind)

    # Additive and idempotent: this database is provisioned by create_all in some
    # environments, so a column may already exist from the model definition.
    for column in _COLUMNS:
        if column.name not in present:
            op.add_column(_TABLE, column)

    # Backfill. Anything with no status is unknown, including rows that already
    # hold an applicable_formats list: a list alone never said whether it was a
    # complete whitelist, so it cannot be promoted to verified without a check.
    op.execute(
        sa.text(
            f"""
            UPDATE {_TABLE}
            SET format_eligibility_status = 'unknown'
            WHERE format_eligibility_status IS NULL
               OR btrim(format_eligibility_status) = ''
            """
        )
    )

    # Guard the vocabulary at the database level so a typo cannot become a fourth
    # status that the application silently treats as unknown.
    op.create_check_constraint(
        "ck_incentive_programs_format_eligibility_status",
        _TABLE,
        "format_eligibility_status IN ('verified', 'conditional', 'unknown')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    present = _existing_columns(bind)

    try:
        op.drop_constraint(
            "ck_incentive_programs_format_eligibility_status", _TABLE, type_="check"
        )
    except Exception:
        # The constraint may not exist if upgrade partially applied; dropping the
        # columns below is the part that matters.
        pass

    for column in reversed(_COLUMNS):
        if column.name in present:
            op.drop_column(_TABLE, column.name)
