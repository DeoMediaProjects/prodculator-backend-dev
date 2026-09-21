"""Give a distributor's audit annotations somewhere that is not the report.

PROD-FIX-006, the same defect one table over.

`g7b8c9d0e1f2` separated internal audit annotations out of
`incentive_programs` and gave them `internal_audit_notes` to live in. It did
not cover `distributors`, which had no such column, and the report's last-line
guard has been firing on every generation since:

    PROD-FIX-006 guard fired: internal audit annotation reached client-bound
    report output at 1 location(s):
    report.distributorRecommendations[2].submissionProcess

The guard did its job — it strips the text, so nothing has reached a producer —
but it is a backstop, it logs at ERROR every run, and it says the underlying
record needs correcting. This corrects it.

`submission_process` is read by `ReportBuilder._build_distributor_matches` and
rendered in section 12, so it is the column the guard caught. `notes` is
scrubbed alongside it: it is not read by the report today, and neither was
`qs_basis` when the earlier migration widened its own scope for exactly this
reason — nothing prevented it from being read tomorrow.

Written to find the row rather than name it. The offending record is not in
every environment, and a migration that hardcoded one distributor's id would
be a migration that silently did nothing wherever the data differed.

Revision ID: y6z7a8b9c0d1
Revises: x3y4z5a6b7c8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.audit_notes import (
    AUDIT_SPAN,
    INTERNAL_SENTENCE,
    split_audit_text as _split_audit_text,
)

revision = "y6z7a8b9c0d1"
down_revision = "x3y4z5a6b7c8"
branch_labels = None
depends_on = None

_TABLE = "distributors"
#: Narrative-facing columns on this table. `submission_process` reaches the
#: client report; `notes` does not today, and is included for the same reason
#: the incentive migration went wider than the columns then in use.
_COLUMNS: tuple[str, ...] = ("submission_process", "notes")


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in set(inspector.get_table_names()):
        return

    existing = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "internal_audit_notes" not in existing:
        op.add_column(
            _TABLE, sa.Column("internal_audit_notes", sa.Text(), nullable=True)
        )

    columns = tuple(c for c in _COLUMNS if c in existing)
    if not columns:
        return

    select_cols = ", ".join(("id", "name") + columns)
    rows = conn.execute(sa.text(f"SELECT {select_cols} FROM {_TABLE}")).mappings().all()

    moved_rows = 0
    moved_fragments = 0
    for row in rows:
        updates: dict[str, str | None] = {}
        fragments: list[str] = []
        for col in columns:
            clean, extracted = _split_audit_text(row.get(col))
            if extracted:
                updates[col] = clean
                # Labelled with its origin column, so the data team can see
                # which field the caution was attached to.
                fragments.extend(f"[{col}] {frag}" for frag in extracted)

        if not fragments:
            continue

        updates["internal_audit_notes"] = "\n\n".join(fragments)
        set_clause = ", ".join(f"{c} = :{c}" for c in updates)
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET {set_clause} WHERE id = :row_id"),
            {**updates, "row_id": row["id"]},
        )
        moved_rows += 1
        moved_fragments += len(fragments)
        print(f"[{revision}] cleaned {row['name']}: {len(fragments)} fragment(s)")

    # Re-read and re-apply the detector. A partial cleanup here means the guard
    # keeps firing in production, which is the thing this exists to stop, so it
    # fails the migration rather than reporting success.
    residual: list[str] = []
    for row in conn.execute(sa.text(f"SELECT {select_cols} FROM {_TABLE}")).mappings():
        for col in columns:
            value = row.get(col)
            if not isinstance(value, str) or not value:
                continue
            if AUDIT_SPAN.search(value) or INTERNAL_SENTENCE.search(value):
                residual.append(f"{row['name']} / {col}")

    assert not residual, (
        "PROD-FIX-006: internal audit text still present in distributor "
        "narrative columns after extraction:\n  " + "\n  ".join(residual)
    )

    print(
        f"[{revision}] moved {moved_fragments} audit fragment(s) "
        f"out of {moved_rows} distributor row(s)"
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in set(inspector.get_table_names()):
        return
    existing = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "internal_audit_notes" not in existing:
        return

    # Fold the fragments back into `notes` rather than dropping them. Their
    # original inline positions are not recoverable; the content is.
    if "notes" in existing:
        conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET notes = "
                f"TRIM(COALESCE(notes, '') || ' ' || internal_audit_notes) "
                f"WHERE internal_audit_notes IS NOT NULL "
                f"AND internal_audit_notes <> ''"
            )
        )
    op.drop_column(_TABLE, "internal_audit_notes")
