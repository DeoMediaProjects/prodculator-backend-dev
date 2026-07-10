"""add_audience_focus

Revision ID: b0819203a4c5
Revises: af708192a3b4
Create Date: 2026-07-10

Adds audience_focus (JSON) to film_festivals and distributors, populated
from the canonical handoff datasets (festivals_database.json /
distributors_database.json). The matching engine scores declared target
audience against this field.

Data-integrity rule (handoff §3): audience_focus is present only where the
festival/distributor has SOURCED audience positioning — 6 festivals and 3
distributors. Absent means "no declared focus", not a mismatch. Never
backfilled by inference.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "b0819203a4c5"
down_revision = "af708192a3b4"
branch_labels = None
depends_on = None

_FESTIVAL_AUDIENCE: dict[str, list[str]] = {
    "Frameline (San Francisco International LGBTQ+ Film Festival)": ["lgbtq_audience"],
    "Outfest Los Angeles": ["lgbtq_audience"],
    "Chicago International Children's Film Festival": ["kids_family"],
    "New York International Children's Film Festival": ["kids_family"],
    "Out On Film — Atlanta's International LGBTQ Film Festival": ["lgbtq_audience"],
    "Provincetown International Film Festival": ["lgbtq_audience"],
}

_DISTRIBUTOR_AUDIENCE: dict[str, list[str]] = {
    "Breaking Glass Pictures": ["lgbtq_audience"],
    "Wolfe Video": ["lgbtq_audience"],
    "Angel Studios": ["kids_family"],
}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    for table, values in (
        ("film_festivals", _FESTIVAL_AUDIENCE),
        ("distributors", _DISTRIBUTOR_AUDIENCE),
    ):
        if table not in inspector.get_table_names():
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if "audience_focus" not in cols:
            op.add_column(table, sa.Column("audience_focus", sa.JSON(), nullable=True))
        for name, focus in values.items():
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET audience_focus = :focus WHERE name = :name"  # noqa: S608
                ),
                {"focus": json.dumps(focus), "name": name},
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in ("film_festivals", "distributors"):
        if table not in inspector.get_table_names():
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if "audience_focus" in cols:
            op.drop_column(table, "audience_focus")
