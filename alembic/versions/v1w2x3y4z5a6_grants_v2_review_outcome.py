"""Record the outcome of the Grants v2 duplicate review: all twenty confirmed.

Migration f2a3b4c5d6e7 held twenty rows at ``NEEDS_REVIEW`` rather than deciding
them, which was right: it could see that a row looked superseded, not that its
successor actually existed. This is the review it asked for.

Every one of the twenty was checked against the live table. Seventeen name a
``replacement_title`` that resolves to a LIVE record. The other three name no
successor in that column, and all three turned out to be duplicates of a live
record whose territory is correct where theirs is wrong — two Telefilm Canada
programmes filed under Ontario when Telefilm is a national body, and a
Netherlands minority co-production fund carried under the generic parent type.
None of the twenty leaves a funding body unrepresented.

So they move to ARCHIVED. The rows are not deleted and the change is reversible:
``downgrade`` puts them back to NEEDS_REVIEW. What changes is the meaning of the
state they sit in — NEEDS_REVIEW says a human must look, and leaving rows there
after a human has looked empties the state of meaning.

This changes no matching behaviour. All twenty already carry
``paid_match_eligible = FALSE`` and were excluded from recommendations the
moment f2a3b4c5d6e7 ran.

Revision ID: v1w2x3y4z5a6
Revises: u0v1w2x3y4z5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v1w2x3y4z5a6"
down_revision = "u0v1w2x3y4z5"
branch_labels = None
depends_on = None

_TABLE = "grant_opportunities"

#: The reason texts f2a3b4c5d6e7 wrote. Matching on them rather than on titles
#: means this migration touches only the rows that migration held, and cannot
#: reach a row that arrived at NEEDS_REVIEW some other way.
_HELD_REASON_PREFIXES: tuple[str, ...] = (
    "Semantic duplicate of a fuller record",
    "Singular/plural title variant of a fuller record",
    "Outside the frozen v2 inventory",
    "Duplicate of the pass17 record",
    "Duplicate of the Pass18 record",
)

_REVIEW_NOTE = (
    "Reviewed 20 September 2026: successor confirmed present and live. "
    "Archived rather than deleted; restore by setting lifecycle_state to LIVE."
)


def _columns(conn) -> set[str]:
    return {c["name"] for c in sa.inspect(conn).get_columns(_TABLE)}


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return
    columns = _columns(conn)
    if not {"lifecycle_state", "archived_reason"} <= columns:
        # A database that has not had the v2 grants migrations has nothing to
        # review. Returning is correct; raising would block an unrelated
        # environment from reaching head.
        return

    archived = 0
    for prefix in _HELD_REASON_PREFIXES:
        result = conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET lifecycle_state = 'ARCHIVED', "
                f"archived_reason = archived_reason || ' ' || :note "
                f"WHERE lifecycle_state = 'NEEDS_REVIEW' "
                f"AND archived_reason LIKE :prefix "
                f"AND archived_reason NOT LIKE '%Reviewed 20 September 2026%'"
            ),
            {"note": _REVIEW_NOTE, "prefix": f"{prefix}%"},
        )
        archived += result.rowcount or 0

    remaining = conn.execute(
        sa.text(
            f"SELECT count(*) FROM {_TABLE} WHERE lifecycle_state = 'NEEDS_REVIEW'"
        )
    ).scalar()
    print(f"[{revision}] reviewed duplicates archived: {archived}")
    print(f"[{revision}] rows still awaiting review: {remaining}")


def downgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return
    if not {"lifecycle_state", "archived_reason"} <= _columns(conn):
        return
    result = conn.execute(
        sa.text(
            f"UPDATE {_TABLE} SET lifecycle_state = 'NEEDS_REVIEW', "
            f"archived_reason = replace(archived_reason, ' ' || :note, '') "
            f"WHERE archived_reason LIKE '%Reviewed 20 September 2026%'"
        ),
        {"note": _REVIEW_NOTE},
    )
    print(f"[{revision}] returned {result.rowcount or 0} row(s) to NEEDS_REVIEW")
