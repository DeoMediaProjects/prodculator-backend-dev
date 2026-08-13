"""Distributor format focus: a real column, plus the short-film outlets.

Two problems, one cause.

``format_focus`` existed in the source tool but had no column here — the create
migration flattened it into a line of prose inside ``notes``. Prose cannot be
filtered on, so ``match_distributors`` never saw it, and unlike the festival and
grant matchers it had no format gate at all. A horror short was therefore matched
to A24, Neon and Dark Sky Films: real distributors, all of them feature buyers,
none of which will take a short.

And the dataset held no short-film outlets to match instead, so even a working
gate would have returned nothing.

This adds the column, recovers the values already recorded in ``notes`` (a
restatement of what the curator wrote, not a new claim), and seeds five active
short-film distributors.

On the seeds: only the definitional facts are populated. That DUST programmes
science-fiction shorts and ALTER programmes horror shorts is what these outlets
ARE — it is why they belong in the dataset. Their rights terms, budget fit and
submission mechanics are not asserted, because those change and this migration
cannot check them. Each lands ``provisional`` with ``verified_at`` NULL, which is
the convention this dataset already uses for records awaiting own-site
verification, so they surface in the admin verification queue rather than passing
as checked.

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-08-13
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "o5p6q7r8s9t0"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None

_TABLE = "distributors"

# The five named in FIX-04. Fields left out are left out on purpose.
_SHORT_FILM_DISTRIBUTORS = [
    {
        "name": "DUST",
        "specialty_genres": ["science fiction", "sci-fi", "thriller"],
        "format_focus": ["short"],
        "notes": (
            "Science-fiction short film channel and streaming platform. Programmes "
            "shorts rather than features. Rights terms, submission route and current "
            "acquisition posture are not recorded here and need own-site verification."
        ),
    },
    {
        "name": "ALTER",
        "specialty_genres": ["horror", "thriller", "psychological"],
        "format_focus": ["short"],
        "notes": (
            "Horror short film channel and streaming platform. Programmes shorts "
            "rather than features. Rights terms, submission route and current "
            "acquisition posture are not recorded here and need own-site verification."
        ),
    },
    {
        "name": "Omeleto",
        "specialty_genres": ["all"],
        "format_focus": ["short"],
        "notes": (
            "Short film channel carrying work across genres. Rights terms, submission "
            "route and current acquisition posture need own-site verification."
        ),
    },
    {
        "name": "Short of the Week",
        "specialty_genres": ["all"],
        "format_focus": ["short"],
        "notes": (
            "Curated short film platform carrying work across genres. Rights terms, "
            "submission route and current acquisition posture need own-site "
            "verification."
        ),
    },
    {
        "name": "ShortsTV",
        "specialty_genres": ["all"],
        "format_focus": ["short"],
        "notes": (
            "Short film television network and theatrical distributor of short-film "
            "programmes. Rights terms, submission route and current acquisition "
            "posture need own-site verification."
        ),
    },
]

# "Format focus: feature, short" as written into notes by ae6f70819203.
_NOTES_FORMAT_RE = re.compile(r"^Format focus:\s*(.+)$", re.M)


def _existing_columns(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()

    if "format_focus" not in _existing_columns(bind):
        op.add_column(_TABLE, sa.Column("format_focus", sa.JSON(), nullable=True))

    # Recover what the create migration folded into notes. NULL stays NULL: a
    # distributor that never declared a format focus is unknown, not "all
    # formats", and the matcher treats unknown as no exclusion.
    rows = bind.execute(
        sa.text(f"SELECT id, notes FROM {_TABLE} WHERE notes IS NOT NULL")
    ).fetchall()
    for row_id, notes in rows:
        match = _NOTES_FORMAT_RE.search(notes or "")
        if not match:
            continue
        formats = [f.strip().lower() for f in match.group(1).split(",") if f.strip()]
        if not formats:
            continue
        bind.execute(
            sa.text(f"UPDATE {_TABLE} SET format_focus = :v WHERE id = :i"),
            {"v": json.dumps(formats), "i": row_id},
        )

    now = datetime.now(timezone.utc).isoformat()
    for src in _SHORT_FILM_DISTRIBUTORS:
        row_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"prodculator:distributor:{src['name']}")
        )
        exists = bind.execute(
            sa.text(f"SELECT 1 FROM {_TABLE} WHERE id = :i OR name = :n"),
            {"i": row_id, "n": src["name"]},
        ).fetchone()
        if exists:
            continue
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {_TABLE}
                    (id, name, specialty_genres, specialty_representation,
                     territory_reach, scouts_festivals, format_focus, notes,
                     active_status, verified_at, created_at, updated_at)
                VALUES
                    (:id, :name, :genres, :rep, :reach, :fests, :formats, :notes,
                     'provisional', NULL, :now, :now)
                """
            ),
            {
                "id": row_id,
                "name": src["name"],
                "genres": json.dumps(src["specialty_genres"]),
                "rep": json.dumps(["general"]),
                "reach": json.dumps([]),
                "fests": json.dumps([]),
                "formats": json.dumps(src["format_focus"]),
                "notes": src["notes"],
                "now": now,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(f"DELETE FROM {_TABLE} WHERE name = ANY(:names)"),
        {"names": [d["name"] for d in _SHORT_FILM_DISTRIBUTORS]},
    )
    if "format_focus" in _existing_columns(bind):
        op.drop_column(_TABLE, "format_focus")
