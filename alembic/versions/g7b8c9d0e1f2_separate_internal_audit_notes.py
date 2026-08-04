"""separate_internal_audit_notes

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-04

PROD-FIX-006 (CRITICAL) — internal QA notes leaking into client-facing PDFs.

Internal data-audit annotations written for the admin/data team were appearing
verbatim in the Tax Incentive Analysis section of client PDF and Excel output,
e.g. the Serbia entry in the A_Quiet_Place report:

    "[FLAGGED 2026-07: found historical evidence of first-come-first-served
     annual budget allocation ... needs direct confirmation from Film Center
     Serbia before treating 'No cap' as reliable.]"

Root cause: migration ab2c3d4e5f61 (incentives v4 refresh) wrote the seed
`notes` string into BOTH `notes` and `eligibility_notes`:

    "notes": notes,
    "eligibility_notes": notes,

`eligibility_notes` is read by ReportBuilder._build_single_estimate and
appended to the client-facing `requirements` list, and by the Excel exporter.
The audit annotations were embedded inline in that same string.

This migration establishes the schema-level separation the brief requires,
rather than a downstream filter that could miss an edge case:

  1. Adds `internal_audit_notes` (TEXT) — admin/data team only, never read by
     the report generator.
  2. Extracts every bracketed audit annotation out of the five narrative-facing
     columns into `internal_audit_notes`, leaving the clean prose behind.
  3. Leaves the extracted content fully intact and queryable for the data team
     via IncentiveDataManager.

Scope is deliberately wider than the three programmes surfaced in QA: the
same annotation style appears in `qs_basis`, `calc_formula` and
`annual_programme_cap` across the dataset (e.g. New York's annual cap carries
an inline "ADMIN VERIFY" instruction). Those columns are not read by the
report builder today, but nothing prevented it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.audit_notes import (
    AUDIT_SPAN,
    INTERNAL_SENTENCE,
    NARRATIVE_COLUMNS as _NARRATIVE_COLUMNS,
    split_audit_text as _split_audit_text,
)

revision = "g7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1 — Admin-only audit column. Nothing in app/modules/reports reads this.
    # On a fresh build ab2c3d4e5f61 has already created it (it needs somewhere
    # to put the fragments it strips at ingestion), so tolerate its presence.
    if not any(
        c["name"] == "internal_audit_notes"
        for c in sa.inspect(conn).get_columns("incentive_programs")
    ):
        op.add_column(
            "incentive_programs",
            sa.Column("internal_audit_notes", sa.Text(), nullable=True),
        )

    # 2 — Extract audit annotations out of every narrative-facing column.
    select_cols = ", ".join(("id",) + _NARRATIVE_COLUMNS)
    rows = conn.execute(
        sa.text(f"SELECT {select_cols} FROM incentive_programs")
    ).mappings().all()

    moved_rows = 0
    moved_fragments = 0

    for row in rows:
        updates: dict[str, str | None] = {}
        audit_fragments: list[str] = []

        for col in _NARRATIVE_COLUMNS:
            clean, extracted = _split_audit_text(row.get(col))
            if extracted:
                updates[col] = clean
                # Label each fragment with its origin column so the data team
                # can see which field the caution was attached to.
                audit_fragments.extend(f"[{col}] {frag}" for frag in extracted)

        if not audit_fragments:
            continue

        updates["internal_audit_notes"] = "\n\n".join(audit_fragments)
        set_clause = ", ".join(f"{c} = :{c}" for c in updates)
        conn.execute(
            sa.text(f"UPDATE incentive_programs SET {set_clause} WHERE id = :row_id"),
            {**updates, "row_id": row["id"]},
        )
        moved_rows += 1
        moved_fragments += len(audit_fragments)

    # 3 — Fail the migration rather than ship a partial cleanup. Re-read the
    # table and re-apply the detector: if anything still matches, the
    # extraction missed a variant, and that must surface here rather than in a
    # client PDF.
    residual: list[str] = []
    for row in conn.execute(
        sa.text(f"SELECT {select_cols}, territory, program FROM incentive_programs")
    ).mappings():
        for col in _NARRATIVE_COLUMNS:
            value = row.get(col)
            if not value or not isinstance(value, str):
                continue
            if AUDIT_SPAN.search(value) or INTERNAL_SENTENCE.search(value):
                residual.append(f"{row['territory']} / {row['program']} / {col}")

    assert not residual, (
        "PROD-FIX-006: internal audit text still present in narrative-facing "
        "columns after extraction:\n  " + "\n  ".join(residual)
    )

    print(
        f"PROD-FIX-006: moved {moved_fragments} audit fragment(s) "
        f"out of {moved_rows} incentive_programs row(s)"
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Fold the audit notes back into `notes` so no data is lost on downgrade.
    # The original per-column inline positions are not recoverable; the
    # fragments are appended instead, which is lossless in content.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET notes = COALESCE(notes || ' ', '') || internal_audit_notes
        WHERE internal_audit_notes IS NOT NULL
    """))

    op.drop_column("incentive_programs", "internal_audit_notes")
