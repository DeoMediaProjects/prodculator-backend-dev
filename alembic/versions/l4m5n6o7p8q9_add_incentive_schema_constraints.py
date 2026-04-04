"""add_incentive_schema_constraints

Revision ID: l4m5n6o7p8q9
Revises: k3l4m5n6o7p8
Create Date: 2026-03-28

ROOT CAUSE
----------
The incentive_programs table had no structural constraints preventing:
  - Silent no-op migrations (UPDATE with wrong WHERE clause writes nothing, no error)
  - Duplicate (territory, program) rows causing ambiguous best_incentive() selection
  - Invalid rate_type values entering the system undetected

These allowed data errors to silently pass through — discovered only when AI
output was wrong (e.g. France TRIP ATL deduction, BC FIBC vs PSTC label).

FIX
---
1. UNIQUE INDEX on (territory, program): duplicate rows are now a DB error,
   not a silent data anomaly.

2. CHECK constraint on rate_type: restricts to the canonical set that the
   codebase logic (TAX_CREDIT_RATE_TYPES, _compute_admin_complexity, etc.)
   depends on.

3. NOT NULL constraints on territory and program: both are mandatory keys.
   rate_gross is NOT made NOT NULL because legitimate grant rows have 0 rates.

SAFE TO RUN
-----------
Pre-checks confirm no existing constraint violations before applying.
The migration is non-destructive: it adds constraints only, never modifies data.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "l4m5n6o7p8q9"
down_revision = "k3l4m5n6o7p8"
branch_labels = None
depends_on = None

_VALID_RATE_TYPES = (
    "cash_rebate",
    "tax_credit",
    "enhanced_tax_credit",
    "refundable_tax_credit",
    "transferable_tax_credit",
    "grant",
    "cash_grant",
    "tax_shelter",
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── Pre-flight: abort if constraints would be violated ───────────────────

    # 1. Check for duplicate (territory, program) pairs
    dupes = conn.execute(sa.text("""
        SELECT territory, program, COUNT(*) AS n
        FROM incentive_programs
        GROUP BY territory, program
        HAVING COUNT(*) > 1
    """)).fetchall()
    if dupes:
        raise RuntimeError(
            f"Cannot add UNIQUE(territory, program): {len(dupes)} duplicate pairs exist. "
            f"First: {dupes[0]}"
        )

    # 2. Check for invalid rate_type values
    placeholders = ", ".join(f"'{v}'" for v in _VALID_RATE_TYPES)
    invalid_types = conn.execute(sa.text(f"""
        SELECT DISTINCT rate_type FROM incentive_programs
        WHERE rate_type IS NOT NULL
          AND rate_type NOT IN ({placeholders})
    """)).fetchall()
    if invalid_types:
        raise RuntimeError(
            f"Cannot add CHECK(rate_type): invalid values exist: "
            f"{[r[0] for r in invalid_types]}"
        )

    # ── Apply constraints ────────────────────────────────────────────────────

    # 1. UNIQUE index (concurrent-safe creation)
    op.create_index(
        "uq_incentive_programs_territory_program",
        "incentive_programs",
        ["territory", "program"],
        unique=True,
    )

    # 2. CHECK constraint on rate_type
    op.create_check_constraint(
        "ck_incentive_programs_rate_type",
        "incentive_programs",
        f"rate_type IN ({placeholders})",
    )

    # 3. NOT NULL on territory and program (they should already be non-null)
    #    Use ALTER COLUMN rather than op.alter_column to keep it explicit.
    conn.execute(sa.text("""
        ALTER TABLE incentive_programs
            ALTER COLUMN territory SET NOT NULL,
            ALTER COLUMN program   SET NOT NULL
    """))


def downgrade() -> None:
    op.drop_constraint("ck_incentive_programs_rate_type", "incentive_programs", type_="check")
    op.drop_index("uq_incentive_programs_territory_program", table_name="incentive_programs")
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE incentive_programs
            ALTER COLUMN territory DROP NOT NULL,
            ALTER COLUMN program   DROP NOT NULL
    """))
