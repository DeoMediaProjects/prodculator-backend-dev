"""The United States row is active again, under the name that says it is not.

Two findings from the 2026-09-21 production inventory, with one cause.

FINDING 1 — three statuses still read "Active" with a capital A

`v1w2x3y4z5a6`/`w2x3y4z5a6b7` normalised these and reported zero changed, so
these three were written afterwards, by hand or by an admin edit. The column is
compared as `= 'active'` in places and lowercased in others, and the v2
scenario service's own docstring records what that cost the last time: four
programmes "contributed no questions at all", silently, so their territory card
rendered empty and their statutory calculator could never receive an input.

FINDING 2 — and one of the three is the United States

    ('United States', 'No federal film incentive (state programmes apply)')

That is the corrected name `x3y4z5a6b7c8` gave the mis-filed row, carrying a
status that contradicts it. The picker lowercases before comparing, so 'Active'
reads as active, the country gains `hasOwnIncentive: true`, and it stops being
a grouping control: it reappears in "Expected spend per territory" asking a
producer for a figure against a programme whose own name says it does not
exist, and offers itself as a Must Film In commitment.

There is no federal United States film incentive. New York, California,
Georgia, Illinois, Louisiana and New Mexico each run their own, which is why
the country is selectable purely as a container for its states.

This migration does two things and is idempotent:

  1. Lowercases every `status`, so the case distinction cannot decide anything
     anywhere. The vocabulary in production is active / suspended / blocked /
     no_programme / admin_verify_required; lowercasing changes no meaning.
  2. Returns any country-level United States row that is not already
     `no_programme` to `no_programme`.

Step 2 is scoped to `territory = 'United States'` exactly. The states are
stored under their own territory names, so a real New York or California
programme is untouched — and the point of the correction is that those are
where the actual incentives live.

Revision ID: b1c2d3e4f5a6
Revises: a9b8c7d6e5f4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None

_TABLE = "incentive_programs"
_COUNTRY = "United States"


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return

    # 1 — case can no longer decide anything.
    mixed = conn.execute(
        sa.text(
            f"UPDATE {_TABLE} SET status = lower(status) "
            f"WHERE status IS NOT NULL AND status <> lower(status)"
        )
    )
    print(f"[{revision}] statuses lowercased: {mixed.rowcount or 0}")

    # 2 — the country itself confers nothing, whatever its rows say.
    corrected = conn.execute(
        sa.text(
            f"UPDATE {_TABLE} SET status = 'no_programme' "
            f"WHERE territory = :country "
            f"AND (status IS NULL OR lower(status) <> 'no_programme')"
        ),
        {"country": _COUNTRY},
    )
    print(
        f"[{revision}] United States country-level rows returned to "
        f"no_programme: {corrected.rowcount or 0}"
    )

    # Report what the country now carries, so a second mis-filed row shows up
    # here rather than in a producer's intake form.
    remaining = conn.execute(
        sa.text(f"SELECT program, status FROM {_TABLE} WHERE territory = :country"),
        {"country": _COUNTRY},
    ).fetchall()
    for program, status in remaining:
        print(f"[{revision}]   {status:<16} {program}")


def downgrade() -> None:
    # Deliberately not reversible. Restoring a capital letter changes nothing a
    # reader can act on, and restoring an active United States row would put
    # back a programme that does not exist. The rows themselves are untouched.
    pass
