"""add_tier_type_to_rate_tiers

Revision ID: n6o7p8q9r0s1
Revises: m5n6o7p8q9r0
Create Date: 2026-03-28

PROBLEM
-------
The rate calculator (validator.py _compute_corrected_rebate) uses a regex to
detect "spend-boundary" tiers — tiers where a monetary threshold divides the
qualifying spend so that different rates apply to different portions (e.g. UK
IFTC: 53% on first £15M, 34% on the rest).

The regex matches any tier label containing a currency symbol followed by a
number and "M". This misfires when a label legitimately mentions a monetary
amount that is NOT a spend-boundary:

    France TRIP VFX tier: "...French VFX spend exceeding €2M..."
    → regex matches "€2M" → tier_boundary = 2,000,000
    → qualifying spend (£20M) >> 2M → blended rate triggered
    → (30% × £2M + 40% × £18M) / £20M = 39% (instead of 30%)
    → gross rebate overstated by ~€2.08M

FIX
---
Add an explicit "tier_type" field to tiers[0] of every rate_tier_json row.

    "spend_boundary" — rates apply to different portions of the same qualifying
                       spend; blended rate calculation is required.
                       Programs: UK IFTC, Spain General, Canary Islands,
                       Ireland Section 481.

    "informational"  — tiers describe categories or conditions; the headline
                       rate_gross/rate_net on the programme row is correct for
                       the primary calculation scenario. No blending.
                       Programs: all others with rate_tier_json.

The validator checks tier_type first. If it is "spend_boundary" (or absent, for
legacy rows), the regex runs. If it is any other explicit value, the regex is
skipped and the headline rate is used. Once this migration has run everywhere
the regex legacy path is dead code and can be removed.

CLASSIFICATION LOGIC
--------------------
Classification uses an explicit positive list of known spend-boundary programmes
rather than relying on the regex over the current label text. This makes the
migration order-independent: it produces the correct result regardless of which
other migrations (including z7a8b9c0d1e2, which rewrites the France TRIP label)
have already been applied.

depends_on = "z7a8b9c0d1e2" is also set so Alembic enforces the order when both
branches are present, providing defence-in-depth — but the explicit list means
this migration is safe even when applied standalone.
"""
from __future__ import annotations

import json as _json

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "n6o7p8q9r0s1"
down_revision = "m5n6o7p8q9r0"
depends_on = "z7a8b9c0d1e2"
branch_labels = None

# Positive list of programmes where rate_tier_json encodes a genuine spend-boundary:
# different headline rates apply to different spend portions and a blended rate
# must be computed. All other programmes with rate_tier_json use informational
# tiers — the DB headline rate_gross/rate_net is the correct calculation basis.
_SPEND_BOUNDARY_PROGRAMS: frozenset[str] = frozenset([
    "UK Independent Film Tax Credit (IFTC)",
    "Spain General Tax Incentive for Film Production",
    "Canary Islands Tax Incentive",
    "Section 481 Tax Credit",
])


def upgrade() -> None:
    conn = op.get_bind()

    rows = conn.execute(
        sa.text(
            "SELECT id, program, rate_tier_json FROM incentive_programs "
            "WHERE rate_tier_json IS NOT NULL"
        )
    ).fetchall()

    stamped = 0
    for row_id, program, tier_json_raw in rows:
        try:
            tiers = _json.loads(tier_json_raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(tiers, list) or not tiers:
            continue
        if tiers[0].get("tier_type"):
            # Already stamped — idempotent.
            continue

        tier_type = (
            "spend_boundary" if program in _SPEND_BOUNDARY_PROGRAMS else "informational"
        )

        updated = list(tiers)
        updated[0] = {"tier_type": tier_type, **updated[0]}
        conn.execute(
            sa.text(
                "UPDATE incentive_programs SET rate_tier_json = :tiers WHERE id = :id"
            ),
            {"tiers": _json.dumps(updated), "id": row_id},
        )
        stamped += 1

    # Verify at least one row was stamped (guards against silent no-op if column
    # name or table structure has changed).
    assert_migration_count(
        conn,
        "incentive_programs",
        "rate_tier_json LIKE '%\"tier_type\"%'",
        expected_min=max(stamped, 1),
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()

    rows = conn.execute(
        sa.text(
            "SELECT id, rate_tier_json FROM incentive_programs "
            "WHERE rate_tier_json IS NOT NULL"
        )
    ).fetchall()

    for row_id, tier_json_raw in rows:
        try:
            tiers = _json.loads(tier_json_raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(tiers, list) or not tiers:
            continue
        if "tier_type" not in tiers[0]:
            continue

        updated = list(tiers)
        updated[0] = {k: v for k, v in updated[0].items() if k != "tier_type"}
        conn.execute(
            sa.text(
                "UPDATE incentive_programs SET rate_tier_json = :tiers WHERE id = :id"
            ),
            {"tiers": _json.dumps(updated), "id": row_id},
        )
