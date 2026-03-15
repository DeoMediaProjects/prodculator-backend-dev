"""normalise_territory_data

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
Create Date: 2026-03-15 14:00:00.000000

Normalise territory strings across all dataset tables to match the canonical
labels defined in app.core.territories.Territory enum.

Fixes:
- comparable_productions: "United States of America" → "United States"
- incentive_programs: NULL status → "active", "Active" → "active"
"""
from alembic import op
import sqlalchemy as sa

revision = "z0a1b2c3d4e5"
down_revision = "y9z0a1b2c3d4"
branch_labels = None
depends_on = None

# Territory name corrections: {old_value: new_value}
# These must match Territory.label from app.core.territories
_TERRITORY_FIXES: dict[str, str] = {
    "United States of America": "United States",
}

# Tables that store territory strings and their column names
_TERRITORY_COLUMNS = [
    ("incentive_programs", "territory"),
    ("comparable_productions", "primary_territory"),
    ("grant_opportunities", "territory"),
    ("territory_weather", "territory"),
    # crew_costs uses ISO codes in "country" — no fix needed
    # film_festivals uses "location" (free text) — no fix needed
]


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Fix territory name inconsistencies ───────────────────────────
    for table, column in _TERRITORY_COLUMNS:
        for old_val, new_val in _TERRITORY_FIXES.items():
            result = conn.execute(
                sa.text(
                    f"UPDATE {table} SET {column} = :new_val "
                    f"WHERE {column} = :old_val"
                ),
                {"old_val": old_val, "new_val": new_val},
            )
            if result.rowcount:  # type: ignore[union-attr]
                print(f"  {table}.{column}: {old_val!r} → {new_val!r} ({result.rowcount} rows)")

    # ── 2. Fix incentive_programs status inconsistencies ────────────────
    # Set NULL → "active" (legacy rows that predate the status column)
    result = conn.execute(
        sa.text(
            "UPDATE incentive_programs SET status = 'active' "
            "WHERE status IS NULL"
        )
    )
    if result.rowcount:  # type: ignore[union-attr]
        print(f"  incentive_programs.status: NULL → 'active' ({result.rowcount} rows)")

    # Normalise "Active" → "active"
    result = conn.execute(
        sa.text(
            "UPDATE incentive_programs SET status = 'active' "
            "WHERE status = 'Active'"
        )
    )
    if result.rowcount:  # type: ignore[union-attr]
        print(f"  incentive_programs.status: 'Active' → 'active' ({result.rowcount} rows)")


def downgrade() -> None:
    conn = op.get_bind()

    # Reverse territory fixes
    for table, column in _TERRITORY_COLUMNS:
        for old_val, new_val in _TERRITORY_FIXES.items():
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET {column} = :old_val "
                    f"WHERE {column} = :new_val"
                ),
                {"old_val": old_val, "new_val": new_val},
            )

    # Note: we do NOT reverse the status fix — "active" is the correct value
    # and we cannot distinguish which rows were originally NULL vs "Active".
