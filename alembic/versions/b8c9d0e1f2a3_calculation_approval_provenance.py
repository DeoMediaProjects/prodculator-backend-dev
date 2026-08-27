"""Who approved a programme's formula, and when.

``calculation_verification_status`` already exists and every programme sits at
``blocked``, which is the safe end of the gate. What is missing is the provenance
of a promotion: a status that can be changed with no record of who changed it or
on what basis is not a verification gate, it is a flag.

Three columns, all nullable, because the overwhelming majority of rows are
correctly unapproved and an approval date on an unapproved row would be a lie.

Deliberately NOT a general audit table. The admin audit log already records the
mutation; what belongs on the row is the current answer to "on whose authority is
this formula being used", because that is the question a producer's lawyer asks
and it must be answerable without joining a log.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

_COLUMNS = (
    # The administrator who promoted it. Free text rather than a foreign key: an
    # approval must survive the reviewer's account being deleted, and losing the
    # provenance to a cascade would be worse than losing the join.
    ("calculation_verified_by", sa.String(255)),
    ("calculation_verified_at", sa.DateTime(timezone=True)),
    # What was checked. Required by the endpoint rather than by the column,
    # because backfilling a note onto history we do not have would invent one.
    ("calculation_verification_note", sa.Text()),
)


def upgrade() -> None:
    conn = op.get_bind()
    existing = {c["name"] for c in sa.inspect(conn).get_columns("incentive_programs")}
    added = 0
    for name, coltype in _COLUMNS:
        if name not in existing:
            op.add_column("incentive_programs", sa.Column(name, coltype, nullable=True))
            added += 1
    print(f"[b8c9d0e1f2a3] incentive_programs: {added} approval column(s) added")

    # Any row already sitting at a non-blocked status predates this provenance and
    # cannot be attributed to anyone. Returning it to blocked would be destructive;
    # marking it as unattributed is honest and visible in the admin queue.
    result = conn.execute(sa.text("""
        UPDATE incentive_programs
        SET calculation_verification_note = 'Promoted before approval provenance '
                                            'was recorded. Reviewer unknown.'
        WHERE calculation_verification_status IS NOT NULL
          AND calculation_verification_status <> 'blocked'
          AND calculation_verification_note IS NULL
    """))
    print(
        f"[b8c9d0e1f2a3] {result.rowcount} pre-existing promotion(s) marked "
        f"unattributed"
    )


def downgrade() -> None:
    conn = op.get_bind()
    existing = {c["name"] for c in sa.inspect(conn).get_columns("incentive_programs")}
    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("incentive_programs", name)
