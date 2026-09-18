"""Add the source verification ledger. Additive; changes no existing row.

Every v2 cutover gate — incentive engine classification, grants migration
decisions, commercial profiles, market rules and cycles, festival sections —
records its evidence here rather than in five differently-shaped columns.

Revision ID: u0v1w2x3y4z5
Revises: t9u0v1w2x3y4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u0v1w2x3y4z5"
down_revision = "t9u0v1w2x3y4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "source_verifications" in existing:
        return
    op.create_table(
        "source_verifications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("gate", sa.String(48), nullable=False),
        # The subject lives in its own engine's ID space, so this is
        # deliberately not a foreign key: a festival section and an incentive
        # programme are not rows in the same table, and constraining to one
        # would mean a ledger per engine, which is what this replaces.
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_basis", sa.Text(), nullable=False),
        sa.Column("verified_on", sa.Date(), nullable=False),
        sa.Column("verified_by", sa.String(128), nullable=False),
        sa.Column("review_state", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column(
            "requires_independent_qa",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("qa_by", sa.String(128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        # One current claim per field of a subject. A second claim for the same
        # field is a correction and replaces the first, rather than both
        # standing and an engine acting on whichever it reads first.
        #
        # Declared inline rather than added afterwards because SQLite cannot
        # ALTER TABLE ADD CONSTRAINT, and the isolated migration tests run on
        # SQLite — a constraint that only exists on Postgres is a constraint
        # nothing tests.
        sa.UniqueConstraint(
            "gate", "subject_id", "field", name="uq_source_verifications_subject_field"
        ),
    )
    op.create_index(
        "ix_source_verifications_gate_subject",
        "source_verifications",
        ["gate", "subject_id"],
    )
    op.create_index(
        "ix_source_verifications_state", "source_verifications", ["review_state"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "source_verifications" not in set(sa.inspect(bind).get_table_names()):
        return
    rows = bind.execute(sa.text("SELECT COUNT(*) FROM source_verifications")).scalar()
    if rows:
        # Dropping this table would discard research, not schema. Whoever wants
        # it gone empties it deliberately first.
        raise RuntimeError(
            f"source_verifications holds {rows} recorded verifications; "
            "clear them deliberately before downgrading"
        )
    op.drop_table("source_verifications")
