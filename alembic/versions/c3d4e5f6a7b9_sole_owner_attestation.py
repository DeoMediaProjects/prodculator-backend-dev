"""Let a sole owner sign off the gates that ask for a second pair of eyes.

`MARKET_HARD_GATE` and `FESTIVAL_SECTION` require independent QA, and
`validate_claim` refuses a claim whose QA was signed by the person who made
it. That rule is right and the reason for it has not gone away: these claims
decide whether an opportunity is shown as eligible to a paying producer, and a
misread deadline or premiere rule is exactly the thing one pair of eyes
misses.

It is also unsatisfiable here. Prodculator has one owner, the client cannot
review, and 226 of the 289 outstanding claims sit behind it. A rule nobody can
meet does not raise the standard; it stops the work and leaves the research
unsigned.

So the bar moves and the record of where it moved does not disappear.
`sole_owner_attestation` holds the reason a claim was self-signed. With it, a
claim whose QA matches its author is readable. Without it, the original
refusal stands unchanged — the exception has to be taken deliberately and in
writing, never by leaving a column blank.

WHY A COLUMN AND NOT A FLAG

A boolean would record that the exception was taken. This records why, by
whom it was claimed and — because the row already carries `verified_on` and
`verified_by` — when. An audit later can separate a two-person claim from a
one-person one and read the attestation behind every one of the latter, which
is the difference between a documented exception and a quietly lowered
standard.

Nothing is backfilled. Every existing claim keeps the review it actually got.

Revision ID: c3d4e5f6a7b9
Revises: b1c2d3e4f5a7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b9"
down_revision = "b1c2d3e4f5a7"
branch_labels = None
depends_on = None

_TABLE = "source_verifications"
_COLUMN = "sole_owner_attestation"


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in set(inspector.get_table_names()):
        return
    if _COLUMN in {c["name"] for c in inspector.get_columns(_TABLE)}:
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))
    print(f"[{revision}] {_TABLE}.{_COLUMN} added; no claim is backfilled")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in set(inspector.get_table_names()):
        return
    if _COLUMN not in {c["name"] for c in inspector.get_columns(_TABLE)}:
        return
    # Claims that were readable only because of an attestation stop being
    # readable, which is the correct behaviour on a downgrade: without the
    # column there is no record of the exception, and a self-signed claim with
    # no recorded reason is what the original rule refuses.
    stranded = conn.execute(
        sa.text(
            f"SELECT count(*) FROM {_TABLE} "
            f"WHERE {_COLUMN} IS NOT NULL AND {_COLUMN} <> ''"
        )
    ).scalar() or 0
    if stranded:
        print(
            f"[{revision}] WARNING: {stranded} attested claim(s) lose their "
            f"attestation and will read as unverified"
        )
    op.drop_column(_TABLE, _COLUMN)
