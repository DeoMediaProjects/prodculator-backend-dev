"""Add isolated, field-sourced commercial profiles and title relationships.

No legacy distributor, comparable, report or payment row is changed.

Revision ID: t9u0v1w2x3y4
Revises: s8t9u0v1w2x3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t9u0v1w2x3y4"
down_revision = "s8t9u0v1w2x3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "commercial_company_profiles" not in existing:
        op.create_table(
            "commercial_company_profiles",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("legacy_distributor_id", sa.String(64), nullable=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("claims", sa.JSON(), nullable=False),
            sa.Column("rules_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("review_state", sa.String(32), nullable=False),
            sa.Column("reviewed_on", sa.Date(), nullable=True),
        )
        op.create_index(
            "ix_commercial_companies_review", "commercial_company_profiles", ["review_state"]
        )
    if "commercial_comparable_profiles" not in existing:
        op.create_table(
            "commercial_comparable_profiles",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("legacy_comparable_id", sa.String(64), nullable=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("verified_on", sa.Date(), nullable=False),
            sa.Column("claims", sa.JSON(), nullable=False),
            sa.Column("review_state", sa.String(32), nullable=False),
            sa.Column("reviewed_on", sa.Date(), nullable=True),
        )
        op.create_index(
            "ix_commercial_comparables_review", "commercial_comparable_profiles", ["review_state"]
        )
    if "commercial_comparable_relationships" not in existing:
        op.create_table(
            "commercial_comparable_relationships",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("comparable_id", sa.String(64), nullable=False),
            sa.Column("target_kind", sa.String(24), nullable=False),
            sa.Column("target_id", sa.String(64), nullable=False),
            sa.Column("relationship_type", sa.String(40), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("verified_on", sa.Date(), nullable=False),
            sa.Column("review_state", sa.String(32), nullable=False),
            sa.ForeignKeyConstraint(["comparable_id"], ["commercial_comparable_profiles.id"]),
            sa.UniqueConstraint(
                "comparable_id", "target_kind", "target_id", "relationship_type",
                name="uq_commercial_comparable_relationship",
            ),
        )
        op.create_index(
            "ix_commercial_relationship_target", "commercial_comparable_relationships",
            ["target_kind", "target_id"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    existing = set(sa.inspect(conn).get_table_names())
    for table in (
        "commercial_comparable_relationships",
        "commercial_comparable_profiles",
        "commercial_company_profiles",
    ):
        if table in existing and conn.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError(f"Refusing to drop populated {table}; export and review it first")
    for table in (
        "commercial_comparable_relationships",
        "commercial_comparable_profiles",
        "commercial_company_profiles",
    ):
        if table in existing:
            op.drop_table(table)
