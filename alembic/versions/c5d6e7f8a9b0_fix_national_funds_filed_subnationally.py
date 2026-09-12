"""Five funds filed under a territory their own eligibility text contradicts.

THE BUG
-------
``territory`` is not a label. It drives the ranked-territory signal (+3), the
script-origin signal (+3) and the nationality gate, and matching is string equality
against the territories the producer selected. So a national fund filed under one of
its provinces is invisible to anyone who selected the country, and scores for a region
it does not actually restrict to.

Two of these were found by scanning for national bodies on sub-national territories;
the other three surfaced in the same pass.

EACH CORRECTION COMES FROM THE RECORD'S OWN TEXT
------------------------------------------------
No outside knowledge is used, and nothing here is inferred from a title. In every case
the record's ``eligibility_summary`` states the scope explicitly and the ``territory``
column contradicts it:

  NFVF — Production Funding        Western Cape    -> South Africa
    "South African citizens or permanent residents. Production companies registered
     in South Africa... Covers all three South African sub-territories: Western Cape,
     Gauteng, KwaZulu-Natal."
    The record names Western Cape as ONE OF THREE covered regions, and was filed under
    it as though it were the whole remit.

  NFVF — Co-Production Fund        Gauteng         -> South Africa
    "South African production companies entering into formal international
     co-productions. South African creative lead required."

  Harold Greenberg Fund — Development   British Columbia -> Canada
    "Canadian production companies and writers... Canadian content certification
     required."

  Hot Docs — Blue Ice Fund (Africa Focus)  Western Cape -> Africa
    "African documentary filmmakers at production stage. No nationality restriction
     within Africa."
    Filed under a South African province by a Canadian festival, for a fund whose own
    remit is the continent.

  Sundance Institute — Documentary Film Program  California -> Global
    "International documentary filmmakers, no nationality restriction."
    "Global" is this dataset's own vocabulary for an unrestricted remit (11 other
    records use it), so the value is taken from the dataset rather than coined.

WHAT IS DELIBERATELY LEFT ALONE
-------------------------------
Genuinely sub-national bodies stay sub-national: SODEC is Québec's agency, Creative BC
is British Columbia's, Screen Scotland is Scotland's, and the Canary Islands, NYSCA,
New Mexico Arts, California Arts Council and Alberta Foundation for the Arts records
are all correctly scoped. Only records whose own text contradicts their territory are
touched.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None

_TABLE = "grant_opportunities"

#: (title, wrong_territory, correct_territory). Keyed on the wrong value too, so
#: re-running cannot overwrite a territory someone has since corrected by hand.
_FIXES: tuple[tuple[str, str, str], ...] = (
    ("National Film and Video Foundation — Production Funding", "Western Cape", "South Africa"),
    ("National Film and Video Foundation — Co-Production Fund", "Gauteng", "South Africa"),
    ("Harold Greenberg Fund — Development", "British Columbia", "Canada"),
    ("Hot Docs — Blue Ice Fund (Africa Focus)", "Western Cape", "Africa"),
    ("Sundance Institute — Documentary Film Program", "California", "Global"),
)


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in sa.inspect(conn).get_table_names():
        return

    corrected = 0
    for title, wrong, right in _FIXES:
        result = conn.execute(
            sa.text(f"UPDATE {_TABLE} SET territory = :right "
                    f"WHERE title = :title AND territory = :wrong"),
            {"right": right, "wrong": wrong, "title": title},
        )
        if result.rowcount:
            corrected += result.rowcount
            print(f"[{revision}] {title[:52]}: {wrong} -> {right}")
        else:
            # Not an error: the row may already carry the right value, or have been
            # retired by the dedup review. Announced rather than silent, because a
            # correction that quietly matched nothing is how a bug survives its fix.
            print(f"[{revision}] no change for '{title[:52]}' (not on '{wrong}')")
    print(f"[{revision}] territories corrected: {corrected}")


def downgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in sa.inspect(conn).get_table_names():
        return
    for title, wrong, right in _FIXES:
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET territory = :wrong "
                    f"WHERE title = :title AND territory = :right"),
            {"wrong": wrong, "right": right, "title": title},
        )
