"""Give a staged company somewhere to keep its access route and portfolio group.

``CompanyProfile`` has carried ``access_route`` and ``portfolio_group`` since the
frozen score was wired up. The scorer reads both — access path is 5 of the 100
points, and the group key is what stops a parent and its own label spending two
of a producer's five package slots on one commercial route.

Neither had a column. ``parse_commercial_catalogue`` built every profile without
them, so every company defaulted to ACCESS_ROUTE_UNKNOWN and grouped only with
itself. The defaults are the safe ones, which is why this was invisible: the
report was not wrong, it was uniformly uninformed.

Both are nullable and neither is backfilled. There are no staged companies yet,
and inventing a route for a row that has none is the one thing the access
vocabulary exists to prevent — ``ACCESS_ROUTE_UNKNOWN`` is a real state, not a
missing value, and the importer writes it explicitly.

Revision ID: y4z5a6b7c8d9
Revises: w2x3y4z5a6b7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "y4z5a6b7c8d9"
down_revision = "w2x3y4z5a6b7"
branch_labels = None
depends_on = None

_TABLE = "commercial_company_profiles"
_COLUMNS = (
    ("access_route", sa.String(32)),
    ("portfolio_group", sa.String(128)),
)


def upgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return
    existing = {column["name"] for column in sa.inspect(conn).get_columns(_TABLE)}
    for name, kind in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _TABLE not in set(sa.inspect(conn).get_table_names()):
        return
    existing = {column["name"] for column in sa.inspect(conn).get_columns(_TABLE)}
    for name, _ in _COLUMNS:
        if name not in existing:
            continue
        # A stated refusal and a published contact are sourced facts, and
        # dropping the column discards them silently. Restoring them means
        # re-reading 101 company pages, so this refuses rather than assuming the
        # operator knows that.
        if conn.execute(
            sa.text(f"SELECT 1 FROM {_TABLE} WHERE {name} IS NOT NULL LIMIT 1")
        ).first():
            raise RuntimeError(
                f"Refusing to discard sourced {name} values; export and review them first"
            )
        op.drop_column(_TABLE, name)
