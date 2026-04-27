"""add_cultural_test_and_admin_complexity

Revision ID: m5n6o7p8q9r0
Revises: l4m5n6o7p8q9
Create Date: 2026-03-28

ROOT CAUSE
----------
culturalTestLikelihood and adminComplexity were produced by builder.py heuristics
that used rate_type and other columns as proxies.  The heuristics produced wrong
values for programmes where the proxy didn't hold — e.g. UK VFX Expenditure Credit
has rate_type='enhanced_tax_credit' but has NO cultural test (it is an expenditure-
based credit).  The heuristic returned "High (85%)" for a programme that has zero
cultural test requirement.

Encoding data facts as code was the root problem:
  - Wrong values were fixed by code deploys, not data corrections
  - No developer could audit the output from the DB alone
  - Adding a new exception required a code change

FIX
---
Add two authoritative columns to incentive_programs:

  cultural_test_required  BOOLEAN
    TRUE  = programme requires a cultural/content points test
    FALSE = no cultural test (spend threshold or expenditure-based only)
    NULL  = not yet assessed (builder treats as FALSE / "N/A")

  admin_complexity  TEXT  CHECK(admin_complexity IN ('Low', 'Medium', 'High'))
    Authoritative per-programme complexity rating
    NULL = not yet assessed (builder treats as "Medium")

The builder now reads these columns directly — two lines of code, no logic.
Any data correction is a plain SQL UPDATE, no code deploy needed.

BACKFILL STRATEGY
-----------------
Staged approach — most conservative rule first, then specific overrides:

Stage 1: Universal safe rules (no known exceptions)
  - cash_rebate / grant / cash_grant → cultural_test_required = FALSE
    Spending-threshold and cash-payment programmes universally lack cultural tests.
  - tax_shelter → cultural_test_required = FALSE
    Tax shelters are economic instruments, not cultural gatekeepers.

Stage 2: Specific known programmes (authoritative, from programme documentation)
  Populated only for programmes confirmed during the accuracy review.
  All others remain NULL until a future data review populates them.

PROGRAMMES POPULATED
--------------------
cultural_test_required = FALSE, admin_complexity = 'Low'
  - United Kingdom / VFX Expenditure Credit (Uplift)  — expenditure-based, no test

cultural_test_required = TRUE, admin_complexity = 'Medium'
  - United Kingdom / Film Tax Relief (any variant)    — BFI cultural test required
  - United Kingdom / High-End Television Tax Relief   — BFI cultural test required

cultural_test_required = TRUE, admin_complexity = 'High'
  - British Columbia / BC Film Incentive BC (FIBC)    — Canadian content certification

cultural_test_required = FALSE, admin_complexity = 'Medium'
  - France / TRIP (Tax Rebate for International Production) — spending threshold

cultural_test_required = FALSE, admin_complexity = 'Low'
  - British Columbia / BC Production Services Tax Credit (PSTC) — spending threshold
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration

revision = "m5n6o7p8q9r0"
down_revision = "l4m5n6o7p8q9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ── Add columns ──────────────────────────────────────────────────────────
    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}
    if "cultural_test_required" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("cultural_test_required", sa.Boolean(), nullable=True),
        )
    if "admin_complexity" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column(
                "admin_complexity",
                sa.Text(),
                nullable=True,
                comment="Low | Medium | High",
            ),
        )
    op.create_check_constraint(
        "ck_incentive_programs_admin_complexity",
        "incentive_programs",
        "admin_complexity IN ('Low', 'Medium', 'High')",
    )

    # ── Stage 1: universal safe rules ────────────────────────────────────────
    # Cash rebates / grants — universally no cultural test
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cultural_test_required = FALSE
        WHERE rate_type IN ('cash_rebate', 'grant', 'cash_grant')
          AND cultural_test_required IS NULL
    """))

    # Tax shelters — economic instruments, not cultural gatekeepers
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cultural_test_required = FALSE
        WHERE rate_type = 'tax_shelter'
          AND cultural_test_required IS NULL
    """))

    # ── Stage 2: specific known programmes ───────────────────────────────────

    # UK VFX Expenditure Credit — expenditure-based, no cultural test
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cultural_test_required = FALSE,
            admin_complexity       = 'Low',
            last_verified_at       = '2026-03-28'
        WHERE territory = 'United Kingdom'
          AND program   = 'VFX Expenditure Credit (Uplift)'
    """))
    assert_migration(
        conn, "incentive_programs",
        "territory = 'United Kingdom' AND program = 'VFX Expenditure Credit (Uplift)'",
        {"cultural_test_required": False, "admin_complexity": "Low"},
        migration_id=revision,
    )

    # UK Film Tax Relief variants — BFI cultural test required
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cultural_test_required = TRUE,
            admin_complexity       = 'Medium',
            last_verified_at       = '2026-03-28'
        WHERE territory = 'United Kingdom'
          AND (program ILIKE '%Film Tax Relief%'
            OR program ILIKE '%High-End Television%'
            OR program ILIKE '%Animation Tax Relief%'
            OR program ILIKE '%Children%s Television%')
    """))

    # BC FIBC — Canadian content certification (complex)
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cultural_test_required = TRUE,
            admin_complexity       = 'High',
            last_verified_at       = '2026-03-28'
        WHERE territory = 'British Columbia'
          AND program ILIKE '%Film Incentive BC%'
    """))

    # France TRIP — spending threshold, no cultural test
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cultural_test_required = FALSE,
            admin_complexity       = 'Medium',
            last_verified_at       = '2026-03-28'
        WHERE territory = 'France'
          AND program   = 'TRIP (Tax Rebate for International Production)'
    """))
    assert_migration(
        conn, "incentive_programs",
        "territory = 'France' AND program = 'TRIP (Tax Rebate for International Production)'",
        {"cultural_test_required": False, "admin_complexity": "Medium"},
        migration_id=revision,
    )

    # BC PSTC — spending threshold, no cultural test
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cultural_test_required = FALSE,
            admin_complexity       = 'Low',
            last_verified_at       = '2026-03-28'
        WHERE territory = 'British Columbia'
          AND program   = 'BC Production Services Tax Credit (PSTC)'
    """))
    assert_migration(
        conn, "incentive_programs",
        "territory = 'British Columbia' AND program = 'BC Production Services Tax Credit (PSTC)'",
        {"cultural_test_required": False, "admin_complexity": "Low"},
        migration_id=revision,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_incentive_programs_admin_complexity", "incentive_programs", type_="check"
    )
    op.drop_column("incentive_programs", "admin_complexity")
    op.drop_column("incentive_programs", "cultural_test_required")
