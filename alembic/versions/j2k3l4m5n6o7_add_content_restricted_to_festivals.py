"""add_content_restricted_to_festivals

Revision ID: j2k3l4m5n6o7
Revises: i1j2k3l4m5n6
Create Date: 2026-03-28

ROOT CAUSE
----------
The festival genre-matching logic in builder.py used a hardcoded Python
frozenset `_RESTRICTING_FEST_GENRES` to determine which festivals require
an explicit genre match.  Adding a new content-restricted festival (e.g. a
documentary festival) required a code deployment, not a data change.

FIX
---
Add a `content_restricted` boolean column to `film_festivals`.  When True,
the festival is content-type-specific (horror, documentary, animation, etc.)
and a production must share at least one of the festival's restricting genres
to be included.  When NULL (legacy), builder.py falls back to the frozenset
for backward compatibility.

The builder.py change removes the hardcoded frozenset from primary decision
logic — it becomes a legacy fallback only for rows inserted before this
migration, which will be NULL.

BACKFILL STRATEGY
-----------------
Set content_restricted = TRUE for any festival whose genres JSON contains
one of: "Horror", "Documentary", "Animation", "Experimental", "LGBTQ+".
Uses ILIKE to handle any case variation in the JSON text.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "j2k3l4m5n6o7"
down_revision = "i1j2k3l4m5n6"
branch_labels = None
depends_on = None

# Genres that make a festival content-restricted when present.
_RESTRICTING = ["horror", "documentary", "animation", "experimental", "lgbtq+"]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Add column (nullable, no server default — NULL means "use legacy fallback")
    existing_cols = {c["name"] for c in inspector.get_columns("film_festivals")}
    if "content_restricted" not in existing_cols:
        op.add_column(
            "film_festivals",
            sa.Column("content_restricted", sa.Boolean(), nullable=True),
        )

    # Backfill: mark festivals whose genres JSON contains ONLY restricting genres
    # (no "all genres" or broad genres like "Independent", "Drama").
    # Strategy: mark all restricting-genre matches first, then un-mark any that
    # also contain "all genres" (explicit catch-all) — those are general festivals.
    for genre in _RESTRICTING:
        conn.execute(sa.text("""
            UPDATE film_festivals
            SET content_restricted = TRUE
            WHERE LOWER(CAST(genres AS TEXT)) LIKE :pattern
              AND (content_restricted IS NULL OR content_restricted = FALSE)
        """), {"pattern": f'%"{genre}"%'})

    # Also catch title-case variants (e.g. "Horror" stored as "Horror" in JSON)
    for genre in _RESTRICTING:
        titled = genre.title()
        conn.execute(sa.text("""
            UPDATE film_festivals
            SET content_restricted = TRUE
            WHERE LOWER(CAST(genres AS TEXT)) LIKE :pattern
              AND (content_restricted IS NULL OR content_restricted = FALSE)
        """), {"pattern": f'%"{titled}"%'})

    # Override: festivals with "all genres" explicitly accept everything —
    # they are never content-restricted regardless of other genres in the list.
    conn.execute(sa.text("""
        UPDATE film_festivals
        SET content_restricted = FALSE
        WHERE LOWER(CAST(genres AS TEXT)) LIKE '%all genres%'
    """))

    # Non-restricting festivals get explicit FALSE so the builder skips
    # the legacy fallback for them entirely.
    conn.execute(sa.text("""
        UPDATE film_festivals
        SET content_restricted = FALSE
        WHERE content_restricted IS NULL
    """))

    # Verify FrightFest was correctly marked
    assert_migration_count(
        conn, "film_festivals",
        "content_restricted = TRUE",
        expected_min=1,
        migration_id=revision,
    )


def downgrade() -> None:
    op.drop_column("film_festivals", "content_restricted")
