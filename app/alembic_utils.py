"""Shared utilities for Alembic migration upgrade/downgrade functions.

Provides post-migration assertions so silent no-ops are caught immediately
rather than discovered in the next accuracy review.

Usage in a migration::

    from app.alembic_utils import assert_migration

    def upgrade() -> None:
        conn = op.get_bind()
        conn.execute(sa.text("UPDATE incentive_programs SET rate = '35%' WHERE ..."))
        assert_migration(conn, "incentive_programs",
            "territory = 'British Columbia' AND program = 'BC FIBC'",
            {"rate": "35% of qualified BC labour"},
            migration_id="d1e2f3g4h5i6")
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa


def assert_migration(
    conn: Any,
    table: str,
    where: str,
    expected: dict[str, Any],
    *,
    migration_id: str = "",
) -> None:
    """Assert that at least one row in *table* matches all *expected* column values.

    Raises ``AssertionError`` immediately if:
    - No rows match the WHERE clause (the UPDATE was a silent no-op), or
    - Any row's column value doesn't match the expected value.

    Args:
        conn: SQLAlchemy connection (from ``op.get_bind()``).
        table: Table name.
        where: SQL WHERE clause (no ``WHERE`` keyword).
        expected: Dict of ``{column: expected_value}``. Use ``None`` to assert NULL.
        migration_id: Migration revision ID for clearer error messages.
    """
    cols = ", ".join(expected.keys())
    query = sa.text(f"SELECT {cols} FROM {table} WHERE {where}")  # noqa: S608
    rows = conn.execute(query).fetchall()

    prefix = f"[{migration_id}] " if migration_id else ""

    if not rows:
        raise AssertionError(
            f"{prefix}Migration produced 0 rows matching: "
            f"SELECT FROM {table} WHERE {where}"
        )

    for row in rows:
        row_dict = dict(zip(expected.keys(), row))
        for col, want in expected.items():
            got = row_dict.get(col)
            if want is None:
                if got is not None:
                    raise AssertionError(
                        f"{prefix}{table}.{col}: expected NULL, got {got!r}"
                    )
            else:
                if got != want:
                    raise AssertionError(
                        f"{prefix}{table}.{col}: expected {want!r}, got {got!r}"
                    )


def validate_rate_tiers(tiers: list[dict], *, context: str = "") -> None:
    """Raise ValueError if any tier that sets rate_gross is missing an explicit tier_type.

    Call this in migration upgrade() functions before writing rate_tier_json to the DB.
    Prevents the rate calculator's regex heuristic from silently misclassifying new tiers
    as spend-boundary thresholds when they are actually informational or conditional.

    Valid tier_type values:
        "spend_boundary"  — different rates apply to different portions of the same
                            qualifying spend (e.g. UK IFTC: 53% on first £15M, 34%
                            on remainder). Triggers blended rate calculation.
        "informational"   — tiers describe categories or conditions; the headline
                            rate_gross/rate_net on the programme row is the correct
                            rate for the primary calculation scenario. No blending.

    Args:
        tiers: Parsed list of tier dicts (from rate_tier_json).
        context: Optional label (e.g. "France TRIP") for clearer error messages.
    """
    valid_types = {"spend_boundary", "informational"}
    prefix = f"{context}: " if context else ""
    for i, tier in enumerate(tiers):
        if tier.get("rate_gross") is None:
            continue
        tier_type = tier.get("tier_type")
        if not tier_type:
            label = tier.get("label") or f"tier[{i}]"
            raise ValueError(
                f"{prefix}Tier '{label}' sets rate_gross but has no 'tier_type'. "
                "Set \"tier_type\": \"spend_boundary\" (rates apply to different "
                "portions of the qualifying spend — blending required) or "
                "\"tier_type\": \"informational\" (rates describe categories or "
                "conditions — headline rate is used for calculation)."
            )
        if tier_type not in valid_types:
            label = tier.get("label") or f"tier[{i}]"
            raise ValueError(
                f"{prefix}Tier '{label}' has unrecognised tier_type={tier_type!r}. "
                f"Valid values: {sorted(valid_types)}"
            )


def assert_migration_count(
    conn: Any,
    table: str,
    where: str,
    expected_min: int,
    *,
    migration_id: str = "",
) -> None:
    """Assert that at least *expected_min* rows match *where* after a migration."""
    query = sa.text(f"SELECT COUNT(*) FROM {table} WHERE {where}")  # noqa: S608
    count = conn.execute(query).scalar() or 0
    prefix = f"[{migration_id}] " if migration_id else ""
    if count < expected_min:
        raise AssertionError(
            f"{prefix}Expected ≥{expected_min} rows in {table} WHERE {where}, "
            f"got {count}"
        )
