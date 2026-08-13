"""Format on comparable productions, so the section can be gated on it.

``comparable_productions`` records title, year, budget, territory, genre and the
incentive used. It does not record what the production WAS. So a micro-budget
supernatural short was offered eight comparables — Aftersun, Rocks, Blue Jean,
The Forgiven and others — every one of them a feature, on the strength of
territory and genre alone.

This is FIX-03 Stage 1: the column and the gate. It is deliberately not the
research. A row whose format is null keeps its place and is marked unverified
rather than being discarded, because discarding every null would empty the
section on today's data and tell the producer nothing at all. A row whose format
IS recorded and differs from the production's is dropped outright.

Nothing is backfilled here. Recording that a given title was a feature is a
factual claim about a real production, and it belongs in the curated dataset of
Stage 2 with a source and a verified date beside it — not written from memory
into a migration where nobody can check it later.

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-08-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "p6q7r8s9t0u1"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None

_TABLE = "comparable_productions"

_COLUMNS = (
    # Canonical format token (app.core.formats), or NULL for not-yet-researched.
    sa.Column("format", sa.Text(), nullable=True),
    # Provenance for the value above, so Stage 2's curation is auditable.
    sa.Column("format_source", sa.Text(), nullable=True),
    sa.Column("format_verified_at", sa.Date(), nullable=True),
)


def _existing_columns(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _existing_columns(bind)
    for column in _COLUMNS:
        if column.name not in present:
            op.add_column(_TABLE, column)


def downgrade() -> None:
    bind = op.get_bind()
    present = _existing_columns(bind)
    for column in reversed(_COLUMNS):
        if column.name in present:
            op.drop_column(_TABLE, column.name)
