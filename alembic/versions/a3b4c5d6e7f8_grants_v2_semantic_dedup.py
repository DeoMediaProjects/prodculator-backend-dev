"""Grants v2: retire seven semantic duplicates, and repair two successor pointers.

WHY THERE WAS A SECOND DEDUP PASS
---------------------------------
The first cutover review (f2a3b4c5d6e7) caught duplicates by exact title match. That
is precisely the method migration_instructions.md step 7 warns is not sufficient:
"Run semantic duplicate review before production cutover. Title-string dedup is not
sufficient."

It was not. A similarity scan across the 250 live records found seven pairs that are
the same programme recorded under two different title spellings — so both survived the
first pass and both would appear in a report, one fund shown twice:

    Eurimages Co-production Support            / Eurimages — Co-production Support
    IMDA TAP Film — Asia Co-productions        / IMDA TAP — Asia Co-productions Film Grant
    IMDA TAP Film — First Features             / IMDA TAP — First Features Film Grant
    IMDA TAP Film — Global Co-productions      / IMDA TAP — Global Co-productions Film Grant
    FSA — Brazil–Argentina Co-production 2026  / ANCINE/FSA — Brazil–Argentina Co-production 2026
    FSA — Brazil–Portugal Co-production 2026   / ANCINE/FSA — Brazil–Portugal Co-production 2026
    Aide aux cinémas du monde (ACM)            / CNC / Institut français — Aide aux cinémas du monde

HOW THE SURVIVOR WAS CHOSEN
---------------------------
By evidence, not by which title reads better: each pair was scored on how many of
thirteen matching-relevant fields it populates (funding body, territory, type, amount,
currency, official source, deadline, key rule, eligibility, the two verification flags,
stage, formats). The fuller record survives. The margins were not close — the IMDA and
ANCINE survivors carry 12-13 of 13 against 4-5 for the copies retired here.

THE ONE EXCEPTION, AND WHY IT IS NOT A DATA INVENTION
-----------------------------------------------------
Eurimages scored 9 against 8, so the fuller record survives — but its territory reads
"France". Eurimages is the Council of Europe's co-production fund; its remit is its
member states, not one of them. A wrong territory is not a cosmetic problem here: it
feeds the ranked-territory signal and the nationality gate, so as it stands the fund
scores for a French production and is invisible to an Irish or Polish one.

The correct value is taken from the duplicate being retired, which already states
"Eurimages Member States". That is a merge of two records of the same programme, not a
fact invented for it.

NOT DELETED, AND REVERSIBLE
---------------------------
Retired rows keep their data and their ids; only lifecycle_state changes, and the
engine's record-state gate reports GATE_RECORD_NEEDS_REVIEW so the report can say why a
fund is absent. downgrade() restores every row and the original territory.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

_TABLE = "grant_opportunities"

_REASON = (
    "Semantic duplicate of a fuller record for the same programme, found by "
    "similarity review rather than exact-title match. Restore by setting "
    "lifecycle_state back to LIVE."
)

#: (title_to_retire, title_that_survives). Survivor chosen by field completeness.
_DUPLICATES: tuple[tuple[str, str], ...] = (
    ("Eurimages Co-production Support", "Eurimages — Co-production Support"),
    ("IMDA TAP Film — Asia Co-productions", "IMDA TAP — Asia Co-productions Film Grant"),
    ("IMDA TAP Film — First Features", "IMDA TAP — First Features Film Grant"),
    ("IMDA TAP Film — Global Co-productions", "IMDA TAP — Global Co-productions Film Grant"),
    ("FSA — Brazil–Argentina Co-production 2026",
     "ANCINE/FSA — Brazil–Argentina Co-production 2026"),
    ("FSA — Brazil–Portugal Co-production 2026",
     "ANCINE/FSA — Brazil–Portugal Co-production 2026"),
    ("Aide aux cinémas du monde (ACM)",
     "CNC / Institut français — Aide aux cinémas du monde"),
)

#: Territory corrections merged from a retired duplicate onto its survivor.
_TERRITORY_FIXES: tuple[tuple[str, str, str], ...] = (
    ("Eurimages — Co-production Support", "France", "Eurimages Member States"),
)

#: Successor pointers written by the previous review that named a title which does not
#: exist. The fund was always present — the string was wrong — so this repairs the
#: pointer rather than changing any decision.
_SUCCESSOR_FIXES: tuple[tuple[str, str], ...] = (
    ("CNC — Aide aux Cinémas du Monde",
     "CNC / Institut français — Aide aux cinémas du monde"),
)


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in sa.inspect(conn).get_table_names():
        return

    # Repair pointers BEFORE retiring anything, so a survivor check below sees the
    # corrected target.
    for legacy_title, successor in _SUCCESSOR_FIXES:
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET replacement_title = :successor "
                    f"WHERE title = :title"),
            {"successor": successor, "title": legacy_title},
        )

    for title, wrong, right in _TERRITORY_FIXES:
        result = conn.execute(
            sa.text(f"UPDATE {_TABLE} SET territory = :right "
                    f"WHERE title = :title AND territory = :wrong"),
            {"right": right, "wrong": wrong, "title": title},
        )
        if result.rowcount:
            print(f"[{revision}] territory corrected on '{title}': {wrong} -> {right}")

    retired = 0
    for drop_title, keep_title in _DUPLICATES:
        # Never retire a row unless its survivor is actually live. A typo in either
        # title would otherwise silently remove a fund and leave nothing in its place.
        survivor = conn.execute(
            sa.text(f"SELECT count(*) FROM {_TABLE} "
                    f"WHERE title = :title AND lifecycle_state = 'LIVE'"),
            {"title": keep_title},
        ).scalar()
        if not survivor:
            print(f"[{revision}] SKIPPED '{drop_title}' — survivor "
                  f"'{keep_title}' is not live")
            continue
        result = conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET lifecycle_state = 'NEEDS_REVIEW', "
                f"paid_match_eligible = FALSE, replacement_title = :keep, "
                f"archived_reason = :reason "
                f"WHERE title = :drop AND lifecycle_state = 'LIVE'"
            ),
            {"keep": keep_title, "reason": _REASON, "drop": drop_title},
        )
        retired += result.rowcount or 0
    print(f"[{revision}] semantic duplicates retired: {retired}")

    # Every held record must point at something live, or a producer asking "what
    # replaced this" gets a dead name.
    orphaned = conn.execute(sa.text(
        f"SELECT count(*) FROM {_TABLE} h WHERE h.lifecycle_state = 'NEEDS_REVIEW' "
        f"AND h.replacement_title IS NOT NULL AND NOT EXISTS ("
        f"  SELECT 1 FROM {_TABLE} s WHERE s.title = h.replacement_title "
        f"  AND s.lifecycle_state = 'LIVE')"
    )).scalar()
    assert orphaned == 0, f"{orphaned} held record(s) point at a successor that is not live"

    live = conn.execute(sa.text(
        f"SELECT count(*) FROM {_TABLE} WHERE lifecycle_state = 'LIVE'"
    )).scalar()
    print(f"[{revision}] live records after semantic dedup: {live}")


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
    for title, wrong, right in _TERRITORY_FIXES:
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET territory = :wrong "
                    f"WHERE title = :title AND territory = :right"),
            {"wrong": wrong, "right": right, "title": title},
        )
