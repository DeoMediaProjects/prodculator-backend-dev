"""v2_phase00_mechanism_gate

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-08-22

Incentive Engine v2, Phase 00. Stop programmes whose statutory mechanism is not
an entitlement from emitting a rebate figure.

WHY THIS IS FIRST
-----------------
The v2 handoff forbids "automatic numbers for competitive grants, investor tax
shelters, suspended or blocked programmes". Seven records were producing one. At
a GBP 20,000,000 budget the engine returned:

    Belgium Film Tax Shelter          GBP 6,196,581   investor shelter, 42%
    Singapore IMDA                    GBP 8,000,000   competitive award, 40%
    Japan VIPO Location Incentive     GBP 5,319,149   competitive award, 50%
    India Cine Hub                    GBP 2,884,615   official rates conflict
    Mexico EFICINE                    GBP 2,000,000   investor shelter, 10%
    South Korea KOFIC                 GBP   119,048   programme suspended 2026
    Brazil ANCINE placeholder         GBP 4,000,000   no verified programme

None of those percentages is a rate a production can claim against its spend. An
investor shelter returns value to a third party through the tax system; a
competitive award is granted at a committee's discretion. Presenting either with
the same authority as a UK statutory credit overstates available finance in a
document producers take to financiers.

This lands ahead of the engine rebuild because it needs none of it: the mechanism
is data, and the gate is one condition.

WHAT THIS DOES
--------------
1. Adds ``qs_engine_type``, the canonical v2 field. NULL means the record has not
   been migrated to a v2 engine and keeps its present behaviour, so the column
   can be filled in programme by programme without a flag day. Only the
   non-entitlement values are populated here; the remaining engines arrive with
   their verified rules in the migration waves.

2. Sets the mechanism for the seven records above. ReportValidator returns no
   figure for INVESTOR_TAX_SHELTER, COMPETITIVE_GRANT and NO_PROGRAMME.

3. Applies the two status dispositions the reconciliation matrix requires:
   South Korea SUSPEND (row 39), India BLOCK (row 18). Reports load only
   status='active', so these are excluded from ranking and totals outright,
   which is what the specification asks for.

4. Reverses the Belgium rebate ceiling set by u1v2w3x4y5z6. That change made a
   wrong number smaller by capping it. The correct answer is that a shelter is
   not modelled as a rebate at all, so a rebate ceiling on the row is
   misleading; the per-project shelter limit is retained as display text.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not model the potential award for a competitive or investor mechanism.
Those figures come from the programme's own official rules, not from a rate
applied to production spend, and they belong to the v2 result contract where a
number can carry a status of CONDITIONAL and a non-entitlement label. Until then
no figure is the honest output.

Netherlands FPI and Malta are left alone. Both are entitlements whose award is
allocation or uplift gated, which the specification treats as CONDITIONAL rather
than non-entitlement. They need the status vocabulary, not this gate.

SOURCES
-------
Reconciliation matrix rows 5, 18, 23, 27, 38, 39, 45, 48.
Calculation Engine Rules, QS engine table and result statuses.
Final Target Programme Inventory, v2 status column.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w3x4y5z6a7b8"
down_revision = "v2w3x4y5z6a7"
branch_labels = None
depends_on = None


#: (territory, programme name LIKE, engine, reconciliation row)
_MECHANISMS = (
    ("Belgium", "%Tax Shelter%", "INVESTOR_TAX_SHELTER", 5),
    ("Mexico", "%EFICINE%", "INVESTOR_TAX_SHELTER", 27),
    ("Singapore", "%IMDA%", "COMPETITIVE_GRANT", 38),
    ("Japan", "%Location Incentive%", "COMPETITIVE_GRANT", 23),
    ("Brazil", "%ANCINE%", "NO_PROGRAMME", 45),
    ("Nigeria", "%No Formal%", "NO_PROGRAMME", 48),
)

#: (territory, programme name LIKE, new status, reconciliation row)
_STATUS_DISPOSITIONS = (
    ("South Korea", "%KOFIC%", "suspended", 39),
    ("India", "%Cine Hub%", "blocked", 18),
)


def upgrade() -> None:
    conn = op.get_bind()

    existing = {c["name"] for c in sa.inspect(conn).get_columns("incentive_programs")}
    if "qs_engine_type" not in existing:
        op.add_column(
            "incentive_programs",
            sa.Column("qs_engine_type", sa.String(32), nullable=True),
        )

    for territory, name_like, engine, row_no in _MECHANISMS:
        result = conn.execute(sa.text("""
            UPDATE incentive_programs
            SET qs_engine_type = :engine
            WHERE territory = :territory
              AND program ILIKE :name_like
        """), {"engine": engine, "territory": territory, "name_like": name_like})
        print(
            f"[w3x4y5z6a7b8] row {row_no:>2} {territory}: qs_engine_type={engine} "
            f"on {result.rowcount} row(s)"
        )

    for territory, name_like, status, row_no in _STATUS_DISPOSITIONS:
        result = conn.execute(sa.text("""
            UPDATE incentive_programs
            SET status           = :status,
                last_verified_at = '2026-08-22'
            WHERE territory = :territory
              AND program ILIKE :name_like
        """), {"status": status, "territory": territory, "name_like": name_like})
        print(
            f"[w3x4y5z6a7b8] row {row_no:>2} {territory}: status={status} "
            f"on {result.rowcount} row(s)"
        )

    # Belgium: a shelter is not a capped rebate. Keep the ceiling as prose so the
    # per-project limit is still visible, and remove the numeric field the rebate
    # engine would otherwise clamp against.
    result = conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_amount   = NULL,
            rebate_cap_currency = NULL,
            rebate_cap_display  = 'EUR 7,250,000 maximum sheltered per project. '
                                  'This is an investor tax shelter limit, not a '
                                  'production rebate ceiling.'
        WHERE territory = 'Belgium'
          AND program ILIKE '%Tax Shelter%'
    """))
    print(
        f"[w3x4y5z6a7b8] Belgium rebate ceiling withdrawn on {result.rowcount} row(s)"
    )


def downgrade() -> None:
    conn = op.get_bind()

    for territory, name_like, _engine, _row in _MECHANISMS:
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET qs_engine_type = NULL
            WHERE territory = :territory AND program ILIKE :name_like
        """), {"territory": territory, "name_like": name_like})

    for territory, name_like, _status, _row in _STATUS_DISPOSITIONS:
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET status = 'active'
            WHERE territory = :territory AND program ILIKE :name_like
        """), {"territory": territory, "name_like": name_like})

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_amount   = 7250000.0,
            rebate_cap_currency = 'EUR',
            rebate_cap_display  = 'EUR 7,250,000 (~USD 8,000,000) maximum amount '
                                  'that can be sheltered per project'
        WHERE territory = 'Belgium'
          AND program ILIKE '%Tax Shelter%'
    """))

    op.drop_column("incentive_programs", "qs_engine_type")
