"""Add isolated handoff staging, market tracks and source-linked eligibility tables.

No existing festival, distributor, comparable or report row is updated or deleted.
The imported handoff flags are retained for audit, not used as runtime eligibility.

Revision ID: r7s8t9u0v1w2
Revises: c5d6e7f8a9b0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r7s8t9u0v1w2"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "engine_handoff_records" not in existing:
        op.create_table(
            "engine_handoff_records",
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("record_id", sa.String(64), nullable=False),
            sa.Column("source_version", sa.String(64), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("routing", sa.String(40), nullable=True),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("kind", "record_id"),
        )
    if "market_tracks" not in existing:
        op.create_table(
            "market_tracks",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("programme_class", sa.String(64), nullable=True),
            sa.Column("stage_notes", sa.Text(), nullable=True),
            sa.Column("format_notes", sa.Text(), nullable=True),
            sa.Column("status_snapshot", sa.String(40), nullable=True),
            sa.Column("cycle_snapshot", sa.String(40), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("verified_on_snapshot", sa.Date(), nullable=True),
            sa.Column("hard_gates_prose", sa.Text(), nullable=True),
            sa.Column("paid_safe_snapshot", sa.Boolean(), nullable=False),
            sa.Column("source_version", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "opportunity_cycles" not in existing:
        op.create_table(
            "opportunity_cycles",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("record_id", sa.String(64), nullable=False),
            sa.Column("section_name", sa.Text(), nullable=False),
            sa.Column("cycle_open", sa.Date(), nullable=True),
            sa.Column("cycle_deadline", sa.Date(), nullable=True),
            sa.Column("cycle_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("rules_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("verified_on", sa.Date(), nullable=False),
            sa.UniqueConstraint(
                "kind",
                "record_id",
                "section_name",
                "cycle_deadline",
                name="uq_opportunity_cycle_section_deadline",
            ),
        )
        op.create_index("ix_opportunity_cycles_target", "opportunity_cycles", ["kind", "record_id"])
    if "opportunity_rules" not in existing:
        op.create_table(
            "opportunity_rules",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("cycle_id", sa.String(64), nullable=False),
            sa.Column("project_field", sa.String(80), nullable=False),
            sa.Column("operator", sa.String(24), nullable=False),
            sa.Column("expected", sa.JSON(), nullable=False),
            sa.Column("condition", sa.Text(), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("verified_on", sa.Date(), nullable=False),
            sa.ForeignKeyConstraint(["cycle_id"], ["opportunity_cycles.id"]),
        )
        op.create_index("ix_opportunity_rules_cycle", "opportunity_rules", ["cycle_id"])


def downgrade() -> None:
    conn = op.get_bind()
    existing = set(sa.inspect(conn).get_table_names())
    for table in (
        "opportunity_rules",
        "opportunity_cycles",
        "market_tracks",
        "engine_handoff_records",
    ):
        if table in existing and conn.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError(
                f"Refusing to drop populated {table}; export and review its data first"
            )
    for table in (
        "opportunity_rules",
        "opportunity_cycles",
        "market_tracks",
        "engine_handoff_records",
    ):
        if table in existing:
            op.drop_table(table)
