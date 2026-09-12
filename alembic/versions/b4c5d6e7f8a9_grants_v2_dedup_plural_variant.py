"""Grants v2: the last duplicate — a singular/plural title variant.

The similarity scan in a3b4c5d6e7f8 compared titles as written, so a pair differing
only by a trailing "s" scored below the threshold that pass used and survived it:

    Netherlands Film Fund + HBF — Minority Co-production    (pass17, 13 of 13 fields)
    Netherlands Film Fund + HBF — Minority Co-productions   (pass14,  3 of 13 fields)

Same programme, same partners, same territory. Re-running the scan with singular and
plural collapsed found this and nothing else, so the live set is now free of records
that normalise to the same title.

It is the same shape as the seven retired in a3b4c5d6e7f8 — a thin pass14 capture
alongside the fuller pass17 record that superseded it — and it is resolved the same
way: the record carrying the evidence survives.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

_TABLE = "grant_opportunities"
_DROP = "Netherlands Film Fund + HBF — Minority Co-productions"
_KEEP = "Netherlands Film Fund + HBF — Minority Co-production"
_REASON = (
    "Singular/plural title variant of a fuller record for the same programme. "
    "Restore by setting lifecycle_state back to LIVE."
)


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in sa.inspect(conn).get_table_names():
        return

    survivor = conn.execute(
        sa.text(f"SELECT count(*) FROM {_TABLE} "
                f"WHERE title = :title AND lifecycle_state = 'LIVE'"),
        {"title": _KEEP},
    ).scalar()
    if not survivor:
        print(f"[{revision}] SKIPPED — survivor '{_KEEP}' is not live")
        return

    result = conn.execute(
        sa.text(
            f"UPDATE {_TABLE} SET lifecycle_state = 'NEEDS_REVIEW', "
            f"paid_match_eligible = FALSE, replacement_title = :keep, "
            f"archived_reason = :reason "
            f"WHERE title = :drop AND lifecycle_state = 'LIVE'"
        ),
        {"keep": _KEEP, "reason": _REASON, "drop": _DROP},
    )
    print(f"[{revision}] retired {result.rowcount or 0} plural-variant duplicate")

    live = conn.execute(sa.text(
        f"SELECT count(*) FROM {_TABLE} WHERE lifecycle_state = 'LIVE'"
    )).scalar()
    print(f"[{revision}] live records: {live}")


def downgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in sa.inspect(conn).get_table_names():
        return
    conn.execute(
        sa.text(f"UPDATE {_TABLE} SET lifecycle_state = 'LIVE', "
                f"paid_match_eligible = NULL, replacement_title = NULL, "
                f"archived_reason = NULL WHERE archived_reason = :reason"),
        {"reason": _REASON},
    )
