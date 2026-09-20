"""Give a staged festival section somewhere to keep its premiere requirement.

``opportunity_catalogue`` read the premiere requirement from the verification
ledger, keyed by cycle or record id under the field name
``premiere_requirement``. The research records it somewhere else and at a
different grain: inside a ``section_rules`` blob, one claim per festival, with
the requirement written per section on its own line.

So the lookup found nothing, and it could not have found the right thing even if
the field name had matched — one festival's claim holds several sections'
requirements, and Cannes' Competition and its Short Film Corner do not impose
the same one. A single value keyed by festival would have applied one section's
rule to all of them.

The requirement belongs to the cycle, like ``section_name`` and
``cycle_deadline``. This gives it a column there, and the ledger stays what it
is: the record of what a person read, not the shape the engine queries.

Nullable, and nothing is backfilled. An unrecorded requirement sequences as
NEEDS_CONFIRMATION, which is the correct answer and not a gap — the freeze
carries prose on 34 of 380 festivals and nothing on the rest.

Revision ID: z5a6b7c8d9e0
Revises: y4z5a6b7c8d9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "z5a6b7c8d9e0"
down_revision = "y4z5a6b7c8d9"
branch_labels = None
depends_on = None

_TABLE = "opportunity_cycles"
_COLUMN = "premiere_requirement"


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return
    if _COLUMN not in {c["name"] for c in sa.inspect(conn).get_columns(_TABLE)}:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(16), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return
    if _COLUMN not in {c["name"] for c in sa.inspect(conn).get_columns(_TABLE)}:
        return
    # A premiere requirement took two independent reviewers to record. Dropping
    # the column discards that, and a festival whose requirement is unknown
    # sequences differently from one whose requirement is NONE.
    if conn.execute(
        sa.text(f"SELECT 1 FROM {_TABLE} WHERE {_COLUMN} IS NOT NULL LIMIT 1")
    ).first():
        raise RuntimeError(
            "Refusing to discard reviewed premiere requirements; export them first"
        )
    op.drop_column(_TABLE, _COLUMN)
