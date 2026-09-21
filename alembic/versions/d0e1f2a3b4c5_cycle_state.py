"""A cycle can say it is rolling, or that nobody has announced the next call.

`opportunity_cycles.cycle_deadline` has always been nullable and nothing could
write a row without one, because the parser dropped every claim whose deadline
read ROLLING, NOT_ANNOUNCED or UNKNOWN. The 2026-09-21 staging run made the
cost visible: 132 verified MARKET_CYCLE claims, every one of them signed off,
every one discarded on that line — and zero market cycles in the table behind
203 staged tracks and an empty Section 09.

The answers were never wrong. "The next call is not announced" is the finding
the research asked for. There was simply nowhere to put it, so the opportunity
left the system instead.

`cycle_state` is that place:

    DATED           cycle_deadline is a date
    ROLLING         no deadline because the call is always open
    NOT_ANNOUNCED   the next call has not been published
    UNKNOWN         nobody established either way

Only ROLLING is actionable. Locked decision D.4 — "Rolling remains Rolling" —
and a producer can apply to a rolling call today. The other two reach the
engine's own NOT_ACTIONABLE, which exists for exactly this and keeps them out
of recommendations while leaving them in the universe count.

Existing rows are DATED: every one of them was written by a path that required
a date, so backfilling anything else would assert something untrue about rows
that are already correct.

Revision ID: d0e1f2a3b4c5
Revises: b1c2d3e4f5a6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

_TABLE = "opportunity_cycles"
_COLUMN = "cycle_state"


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in set(inspector.get_table_names()):
        return
    if _COLUMN in {c["name"] for c in inspector.get_columns(_TABLE)}:
        return

    # Added with a server default so the column is NOT NULL from the first
    # moment, including for any row a concurrent write inserts between the two
    # statements below.
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN, sa.String(32), nullable=False, server_default=sa.text("'DATED'")
        ),
    )
    staged = conn.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar() or 0
    print(f"[{revision}] existing cycles marked DATED: {staged}")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in set(inspector.get_table_names()):
        return
    if _COLUMN not in {c["name"] for c in inspector.get_columns(_TABLE)}:
        return
    # Undated cycles go with the column. They cannot be represented without it,
    # and leaving them behind would give every rolling call a NULL deadline
    # that the old loader reads as a row to skip — present in the table,
    # invisible to the engine, which is the state this replaced.
    removed = conn.execute(
        sa.text(f"DELETE FROM {_TABLE} WHERE cycle_deadline IS NULL")
    )
    print(f"[{revision}] undated cycles removed: {removed.rowcount or 0}")
    op.drop_column(_TABLE, _COLUMN)
