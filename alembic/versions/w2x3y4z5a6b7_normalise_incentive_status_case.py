"""Lowercase five programme statuses that read "Active" with a capital A.

The production inventory found `incentive_programs.status` holding both `active`
and `Active`. Every reader lowercases before comparing — except the scenario
question service, which filtered with a SQL equality and is case-sensitive in
Postgres, so those rows produced no wizard questions at all. The producer saw an
empty territory card, had nothing to fill in, and the statutory calculator then
had no qualifying-spend input, so the programme could never produce a figure.

That read path is already fixed. This fixes the data, because a fix that depends
on every future reader remembering to lowercase is one careless `.eq()` away from
coming back — and the next person to write that line will have no reason to
suspect the column is mixed-case.

Only exact case variants of the known status vocabulary are touched. A status
this migration does not recognise is left alone: an unexpected value is a
finding, and quietly lowercasing it would hide the finding while changing the
row.

Revision ID: w2x3y4z5a6b7
Revises: v1w2x3y4z5a6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w2x3y4z5a6b7"
down_revision = "v1w2x3y4z5a6"
branch_labels = None
depends_on = None

_TABLE = "incentive_programs"

#: The canonical vocabulary. Any row whose status matches one of these
#: case-insensitively but not exactly is normalised to the canonical spelling.
_CANONICAL: tuple[str, ...] = (
    "active",
    "suspended",
    "blocked",
    "no_programme",
    "admin_verify_required",
)


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return

    changed = 0
    for canonical in _CANONICAL:
        result = conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET status = :canonical "
                f"WHERE lower(status) = :canonical AND status <> :canonical"
            ),
            {"canonical": canonical},
        )
        changed += result.rowcount or 0

    unknown = [
        row[0]
        for row in conn.execute(
            sa.text(
                f"SELECT DISTINCT status FROM {_TABLE} "
                f"WHERE status IS NOT NULL AND lower(status) <> ALL(:known)"
            ),
            {"known": list(_CANONICAL)},
        )
    ]
    print(f"[{revision}] statuses normalised to canonical case: {changed}")
    if unknown:
        # Reported, not touched. A status nobody recognises is a finding about
        # the data, and normalising its case would change the row while hiding
        # the thing worth looking at.
        print(f"[{revision}] statuses outside the known vocabulary, left alone: {unknown}")


def downgrade() -> None:
    # Case is not information. There is nothing to restore, and re-capitalising
    # five arbitrary rows would recreate the defect rather than undo a change.
    print(f"[{revision}] no-op: letter case carried no meaning to restore")
