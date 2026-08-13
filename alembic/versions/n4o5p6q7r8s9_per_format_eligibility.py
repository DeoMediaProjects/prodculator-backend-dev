"""Per-format eligibility tri-state, plus the theatrical-release condition.

``m3n4o5p6q7r8`` added a whitelist plus a status. That model can say "this list is
authoritative" or "nobody checked", but it cannot say what is actually true of the
research: that somebody established short films are excluded from a programme while
nobody has yet looked at documentary. A whitelist collapses both into one silence.

So eligibility becomes per format, tri-state:

    true   researched, and this format qualifies
    false  researched, and this format does not qualify
    null   NOT RESEARCHED — behaviour must be exactly as before

The last one carries the weight. Every row lands all-null here, so this migration
changes no report: ``evaluate_format_eligibility`` consults the per-format value
first and falls through to the existing whitelist/status logic whenever it is null.
The columns and the gate land now; the research lands per programme, with a source
and a date, and only ever from the programme's own terms.

Nothing is seeded. The one inference that would be legitimate — deriving booleans
from a row already marked ``verified`` with a complete whitelist — is a no-op on
this dataset, because no row carries that combination. It is implemented anyway so
the derivation exists if such a row is ever curated before the research runs.

``theatrical_release_required`` is separate from the format map on purpose. It is
not a property of the format: a programme can accept shorts and still require a
theatrical commitment the short will never meet, and folding that into
``eligible_formats.short = false`` would record the wrong reason and mislead anyone
who later tried to verify it.

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-08-13
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "n4o5p6q7r8s9"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None

_TABLE = "incentive_programs"

_COLUMNS = (
    # Text holding a JSON object, matching how applicable_formats is already
    # stored on this table. Keys are the canonical format tokens.
    sa.Column("eligible_formats", sa.Text(), nullable=True),
    sa.Column("format_notes", sa.Text(), nullable=True),
    sa.Column("theatrical_release_required", sa.Boolean(), nullable=True),
    sa.Column("theatrical_release_note", sa.Text(), nullable=True),
)

# The six the report asks about. Kept in step with FORMAT_KEYS in
# app/modules/reports/format_eligibility.py.
_FORMAT_KEYS = (
    "feature", "short", "documentary", "tv_series", "animation", "unscripted",
)


def _existing_columns(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _existing_columns(bind)

    for column in _COLUMNS:
        if column.name not in present:
            op.add_column(_TABLE, column)

    # Derive ONLY from a row that already declares its whitelist authoritative.
    # For such a row the booleans are restatements of what the curator recorded,
    # not new claims: a format on a verified whitelist is true, one absent from it
    # is false. Every other row stays null.
    rows = bind.execute(
        sa.text(
            f"""
            SELECT id, applicable_formats
            FROM {_TABLE}
            WHERE format_eligibility_status = 'verified'
              AND applicable_formats IS NOT NULL
              AND btrim(applicable_formats) NOT IN ('', '[]')
              AND eligible_formats IS NULL
            """
        )
    ).fetchall()

    for row_id, raw in rows:
        try:
            listed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(listed, list) or not listed:
            continue
        # Compared case-insensitively and with separators normalised, because the
        # column historically stored display labels ("Feature Film", "TV Series").
        normalised = {
            str(v).strip().lower().replace(" ", "_").replace("-", "_")
            for v in listed
        }
        derived = {key: (key in normalised) for key in _FORMAT_KEYS}
        bind.execute(
            sa.text(f"UPDATE {_TABLE} SET eligible_formats = :v WHERE id = :i"),
            {"v": json.dumps(derived), "i": row_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    present = _existing_columns(bind)
    for column in reversed(_COLUMNS):
        if column.name in present:
            op.drop_column(_TABLE, column.name)
