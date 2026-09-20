"""The United States carries no federal film incentive; only its states do.

A row filed under territory "United States" held the programme name "New York
State Film Tax Credit Program (Production)" with no rate. It is a duplicate of
the real New York row, mis-filed against the country.

One row, two consequences.

The report could offer a producer a United States rebate. There is no federal
film incentive in the United States — New York, California, Georgia, Illinois,
Louisiana and New Mexico each run their own, and a national figure is not a
thing that exists.

And the intake picker treats a country with a programme of its own as a
selectable territory rather than a grouping control. So "United States" appeared
in Expected spend per territory beside Illinois, asking a producer for a figure
against a programme that cannot exist, and offered itself as a Must Film In
commitment. Selecting it spent a territory slot on nothing.

The row is corrected rather than deleted. `no_programme` is a statement the
system already knows how to make — the code that builds the picker says a
`no_programme` row "states an absence, so it confers nothing" — and it is more
useful than silence: the report can tell a producer that US incentives are
state-level, which is the thing they need to know. Deleting the row would leave
the country looking merely unexamined.

The real New York programme is untouched and keeps its 30% rate.

Revision ID: x3y4z5a6b7c8
Revises: z5a6b7c8d9e0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "x3y4z5a6b7c8"
down_revision = "z5a6b7c8d9e0"
branch_labels = None
depends_on = None

_TABLE = "incentive_programs"

_CORRECTED_NAME = "No federal film incentive (state programmes apply)"
_NOTE = (
    "The United States has no federal film incentive. Production incentives are "
    "administered by individual states — New York, California, Georgia, "
    "Illinois, Louisiana and New Mexico among them — and each is modelled on its "
    "own record. Select a state rather than the country."
)
#: The mis-filed row, by the programme name it was carrying. Matched on the
#: name rather than the ID because the ID is a random UUID rather than the
#: deterministic ones the migrations generate, which is itself a sign the row
#: arrived by hand.
_MISFILED_NAME = "New York State Film Tax Credit Program (Production)"


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return
    columns = {c["name"] for c in sa.inspect(conn).get_columns(_TABLE)}

    sets = ["status = 'no_programme'", "program = :name"]
    params: dict[str, object] = {"name": _CORRECTED_NAME, "misfiled": _MISFILED_NAME}
    for column, value in (("notes", _NOTE), ("eligibility_notes", _NOTE)):
        if column in columns:
            sets.append(f"{column} = :note")
            params["note"] = value

    result = conn.execute(
        sa.text(
            f"UPDATE {_TABLE} SET {', '.join(sets)} "
            f"WHERE territory = 'United States' AND program = :misfiled"
        ),
        params,
    )
    print(f"[{revision}] United States rows corrected to no_programme: {result.rowcount or 0}")

    # Any other country-level United States row would have the same effect, so
    # report rather than assume this was the only one.
    remaining = conn.execute(
        sa.text(
            f"SELECT program FROM {_TABLE} WHERE territory = 'United States' "
            f"AND lower(status) IN ('active', '')"
        )
    ).fetchall()
    if remaining:
        print(
            f"[{revision}] WARNING: United States still carries active "
            f"programme row(s): {[r[0] for r in remaining]}"
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return
    conn.execute(
        sa.text(
            f"UPDATE {_TABLE} SET status = 'active', program = :misfiled "
            f"WHERE territory = 'United States' AND program = :name"
        ),
        {"name": _CORRECTED_NAME, "misfiled": _MISFILED_NAME},
    )
    print(f"[{revision}] restored the mis-filed United States row")
