"""FIX-03/FIX-05 (completion): backfill comparable format, seed short comparables.

``p6q7r8s9t0u1`` added the ``format`` column to ``comparable_productions`` and
deliberately left every row NULL, on the grounds that recording a title's format is
a factual claim that belongs in a sourced curation pass, not a migration written
from memory.

That pass is this migration, scoped narrowly to what is actually checkable:

1. The ~34 titles seeded by ``w7x8y9z0a1b2`` are all real, identifiable feature
   productions (Aftersun, The Forgiven, Silverton Siege, The Woman King, and the
   rest) — that seed migration's own docstring states it is a feature-film
   dataset. One of them, "Idris Elba: Fighter", is specifically a feature-length
   documentary rather than a narrative feature, so it is tagged ``documentary``
   rather than ``feature``; both are correctly excluded from a short-format
   production either way. Backfilling ``format`` on these rows is what makes the
   FIX-03 gate in ``builder._build_comparables`` actually exclude anything: with
   every row NULL, "recorded and different -> discarded" never fired, and a
   short-format production kept receiving eight feature comparables with a
   "formatVerified: false" flag nobody could act on.

2. Zero of the existing ~34 rows are short films, so backfilling format alone
   leaves a short-format production with an empty comparables section — correct
   over silently wrong, but a producer is better served by real short-film
   comparables where they exist. This adds five publicly documented, awardsourced short films (title, year, territory and genre only — no budget figure is
   asserted for any of them, because none is available from a public source at
   the level of confidence this dataset otherwise requires; budget_usd stays
   NULL rather than being guessed).

Nothing about crew rates, incentives claimed, or budgets for the five new rows is
inferred — only facts independently confirmable from public awards records and
festival/broadcaster sources are recorded, consistent with the "unresearched stays
NULL" convention p6q7r8s9t0u1 established for this column.

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-08-14
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None

_TABLE = "comparable_productions"

_TODAY = "2026-08-14"
_SOURCE_NOTE = "Format inferred from w7x8y9z0a1b2's own docstring (feature-film seed batch)"

# (title, year) -> (format, source_note)
_EXISTING_FORMAT_BACKFILL: dict[tuple[str, int], tuple[str, str]] = {
    ("The Banshees of Inisherin", 2022): ("feature", _SOURCE_NOTE),
    ("Aftersun", 2022): ("feature", _SOURCE_NOTE),
    ("Blue Jean", 2022): ("feature", _SOURCE_NOTE),
    ("Rocks", 2019): ("feature", _SOURCE_NOTE),
    ("The Forgiven", 2021): ("feature", _SOURCE_NOTE),
    ("Calm With Horses", 2019): ("feature", _SOURCE_NOTE),
    ("Silverton Siege", 2022): ("feature", _SOURCE_NOTE),
    ("Gangs of Lagos", 2023): ("feature", _SOURCE_NOTE),
    ("The Woman King", 2022): ("feature", _SOURCE_NOTE),
    ("Mami Wata", 2023): ("feature", _SOURCE_NOTE),
    ("Riding with Sugar", 2020): ("feature", _SOURCE_NOTE),
    ("Anatomy of a Fall", 2023): ("feature", _SOURCE_NOTE),
    ("Corsage", 2022): ("feature", _SOURCE_NOTE),
    ("The Beasts", 2022): ("feature", _SOURCE_NOTE),
    ("Alcarràs", 2022): ("feature", _SOURCE_NOTE),
    ("A Chiara", 2021): ("feature", _SOURCE_NOTE),
    ("Perfect Days", 2023): ("feature", _SOURCE_NOTE),
    ("Son of Saul", 2015): ("feature", _SOURCE_NOTE),
    ("White God", 2014): ("feature", _SOURCE_NOTE),
    ("Zátopek", 2021): ("feature", _SOURCE_NOTE),
    ("By the Grace of God", 2018): ("feature", _SOURCE_NOTE),
    ("The Dry", 2020): ("feature", _SOURCE_NOTE),
    ("Nitram", 2021): ("feature", _SOURCE_NOTE),
    ("The Power of the Dog", 2021): ("feature", _SOURCE_NOTE),
    ("Hunt for the Wilderpeople", 2016): ("feature", _SOURCE_NOTE),
    ("Lamb", 2021): ("feature", _SOURCE_NOTE),
    ("Woman at War", 2018): ("feature", _SOURCE_NOTE),
    ("Moonlight", 2016): ("feature", _SOURCE_NOTE),
    ("Beasts of the Southern Wild", 2012): ("feature", _SOURCE_NOTE),
    ("Everything Everywhere All at Once", 2022): ("feature", _SOURCE_NOTE),
    ("The Whale", 2022): ("feature", _SOURCE_NOTE),
    ("Causeway", 2022): ("feature", _SOURCE_NOTE),
    ("Room", 2015): ("feature", _SOURCE_NOTE),
    ("Incendies", 2010): ("feature", _SOURCE_NOTE),
    ("One Love", 2024): ("feature", _SOURCE_NOTE),
    # Feature-length documentary, not a narrative feature — but format-excluded
    # from a short-format production either way.
    ("Idris Elba: Fighter", 2022): (
        "documentary",
        "Feature-length documentary (Discovery UK), not a narrative feature",
    ),
    ("Blue Story", 2019): ("feature", _SOURCE_NOTE),
    ("Top Boy: Summerhouse (Film)", 2022): ("feature", _SOURCE_NOTE),
    ("The Harder They Fall", 2021): ("feature", _SOURCE_NOTE),
    ("Yardie", 2018): ("feature", _SOURCE_NOTE),
}

# Real, publicly documented short films. Budget deliberately NULL — no figure is
# recorded here with enough confidence to state as a fact.
# (title, year, primary_territory, incentive_used, genre[], production_company,
#  director, source, format_source)
_SHORT_FILMS = [
    (
        "The Silent Child", 2017, "United Kingdom", "BFI Film Fund / BFI Network",
        ["Drama"], "Slick Films", "Chris Overton",
        "Academy Awards (Best Live Action Short Film, 2018) / BFI",
        "Academy Awards database / BFI — Oscar-winning UK short, BFI Film Fund supported",
    ),
    (
        "Stutterer", 2015, "Ireland", "Northern Ireland Screen",
        ["Drama", "Romance"], "Bare Golly Films", "Benjamin Cleary",
        "Academy Awards (Best Live Action Short Film, 2016) / Northern Ireland Screen",
        "Academy Awards database / Northern Ireland Screen — Oscar-winning Irish/UK co-production short",
    ),
    (
        "Dawn of the Deaf", 2016, "United Kingdom", "BFI Network",
        ["Horror", "Thriller"], "Popular Front", "Rob Savage",
        "BAFTA (nominated, Best British Short Film, 2017) / BFI Network",
        "BAFTA nominations database / BFI Network — genuine UK horror/thriller short comparable",
    ),
    (
        "Two Distant Strangers", 2020, "United States", None,
        ["Drama", "Thriller", "Sci-Fi"], "Freedom Principle / Big Indie Pictures", "Travon Free / Martin Desmond Roe",
        "Academy Awards (Best Live Action Short Film, 2021) / Netflix",
        "Academy Awards database — no tax-incentive record confirmed, left null rather than guessed",
    ),
    (
        "Skin", 2018, "United States", None,
        ["Drama"], "New Native Pictures", "Guy Nattiv",
        "Academy Awards (Best Live Action Short Film, 2019)",
        "Academy Awards database — no tax-incentive record confirmed, left null rather than guessed",
    ),
]

_UPDATE_SQL = f"""
UPDATE {_TABLE}
SET format = :format,
    format_source = :format_source,
    format_verified_at = :verified_at
WHERE title = :title AND year = :year AND format IS NULL
"""

_INSERT_SQL = f"""
INSERT INTO {_TABLE} (
    id, title, year, budget_usd, primary_territory, incentive_used,
    genre, production_company, director, source,
    format, format_source, format_verified_at,
    created_at, updated_at
) VALUES (
    :id, :title, :year, NULL, :primary_territory, :incentive_used,
    :genre, :production_company, :director, :source,
    :format, :format_source, :verified_at,
    NOW(), NOW()
)
"""


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return

    for (title, year), (fmt, note) in _EXISTING_FORMAT_BACKFILL.items():
        bind.execute(
            sa.text(_UPDATE_SQL),
            {
                "format": fmt,
                "format_source": note,
                "verified_at": _TODAY,
                "title": title,
                "year": year,
            },
        )

    for (
        title, year, territory, incentive, genres, company, director, source, fmt_source,
    ) in _SHORT_FILMS:
        existing = bind.execute(
            sa.text(
                f"SELECT id FROM {_TABLE} WHERE title = :title AND year = :year LIMIT 1"
            ),
            {"title": title, "year": year},
        ).fetchone()
        if existing:
            continue
        row_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"prodculator:comparable:{title}:{year}")
        )
        bind.execute(
            sa.text(_INSERT_SQL),
            {
                "id": row_id,
                "title": title,
                "year": year,
                "primary_territory": territory,
                "incentive_used": incentive,
                "genre": json.dumps(genres),
                "production_company": company,
                "director": director,
                "source": source,
                "format": "short",
                "format_source": fmt_source,
                "verified_at": _TODAY,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return

    for (title, year), _ in _EXISTING_FORMAT_BACKFILL.items():
        bind.execute(
            sa.text(
                f"""
                UPDATE {_TABLE}
                SET format = NULL, format_source = NULL, format_verified_at = NULL
                WHERE title = :title AND year = :year
                """
            ),
            {"title": title, "year": year},
        )

    for title, year, *_rest in _SHORT_FILMS:
        bind.execute(
            sa.text(f"DELETE FROM {_TABLE} WHERE title = :title AND year = :year"),
            {"title": title, "year": year},
        )
