"""v2_coproduction_structure

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-08-22

Incentive Engine v2. Adds the production structure mode and the co-production
layer from the reference implementation handoff.

WHY THE MODE IS STRUCTURAL RATHER THAN COSMETIC
-----------------------------------------------
The same spend figure means two different things depending on the mode.

In comparison mode a territory spend is one alternative. Spends are never summed,
need not total the budget, and the territories rank against each other because the
producer will choose one.

In co-production mode a territory spend is an allocation inside a single
production. Allocations reconcile to the structure budget, partners carry a
participation share, and the report must NOT rank the territories as if the
producer has to choose one of them: France and Germany are complementary parts of
the same film.

Storing the mode after the fact would leave existing rows ambiguous, which is why
it lands with the scenario tables rather than after them.

CO-PRODUCTION IS NOT A SECOND PRODUCT
-------------------------------------
The reference package is explicit that the existing treaty dataset stays the
source for whether a route exists, and that treaty data must not be duplicated
into the incentive database. So this migration stores the producer's declared
structure and its calculation state; it stores no treaty facts. A treaty match is
resolved from the existing dataset at read time.

Combined public support is recorded but is never available finance until the
cumulation review passes. That is the whole reason ``cumulation_status`` defaults
to ``not_checked`` rather than to anything permissive.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "y5z6a7b8c9d0"
down_revision = "x4y5z6a7b8c9"
branch_labels = None
depends_on = None


_SCENARIO_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    # Co-production only. Null in comparison mode, where a participation share
    # has no meaning because the territories are alternatives.
    ("participation_percent", sa.Float()),
    ("partner_status", sa.String(16)),
    ("structure_id", sa.String(64)),
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "territory_scenarios" in tables:
        existing = {c["name"] for c in inspector.get_columns("territory_scenarios")}
        added = 0
        for name, coltype in _SCENARIO_COLUMNS:
            if name not in existing:
                op.add_column(
                    "territory_scenarios", sa.Column(name, coltype, nullable=True)
                )
                added += 1
        print(f"[y5z6a7b8c9d0] territory_scenarios: {added} column(s) added")

    if "co_production_structures" not in tables:
        op.create_table(
            "co_production_structures",
            sa.Column("structure_id", sa.String(64), primary_key=True),
            sa.Column("report_id", sa.String(64), nullable=False, index=True),
            # comparison | coproduction | undecided. Undecided keeps comparison
            # logic and surfaces co-production opportunities separately, so it is
            # recorded distinctly from comparison even though it calculates the
            # same way.
            sa.Column("mode", sa.String(16), nullable=False,
                      server_default="comparison"),
            sa.Column("total_budget", sa.Float()),
            sa.Column("budget_currency", sa.String(3)),
            # Spend inside the structure not attributed to a partner territory, so
            # allocations can reconcile without inventing a territory.
            sa.Column("unallocated_spend", sa.Float()),
            # The producer's declared intent. Whether a treaty actually applies is
            # answered by the existing treaty dataset, and approval is a further
            # step again. Storing intent here does not assert either.
            sa.Column("co_production_route", sa.String(128)),
            sa.Column("supranational_support_interest", sa.String(32)),
            # reconciled | under_allocated | over_allocated | not_assessable.
            # Reported, never enforced: a producer mid-entry is legitimately
            # under-allocated and an over-allocation may be a currency difference.
            sa.Column("reconciliation_status", sa.String(24)),
            sa.Column("unreconciled_amount", sa.Float()),
            # not_checked | requires_review | requires_fx | passed | blocked.
            # Combined support is never presented as available finance until this
            # passes, which is why the default is the unchecked end.
            sa.Column("cumulation_status", sa.String(24), nullable=False,
                      server_default="not_checked"),
            sa.Column("combined_public_support", sa.Float()),
            sa.Column("combined_support_currency", sa.String(3)),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
        )
        print("[y5z6a7b8c9d0] created co_production_structures")

    # Selective supranational support, held separately from the incentive table
    # because it is not an entitlement and must never be ranked as one.
    if "supranational_support_records" not in tables:
        op.create_table(
            "supranational_support_records",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("structure_id", sa.String(64), nullable=False, index=True),
            sa.Column("fund_name", sa.String(128), nullable=False),
            # potential | application_required | applied | awarded. Only an
            # evidenced award may ever be treated as committed finance.
            sa.Column("support_status", sa.String(32), nullable=False,
                      server_default="potential"),
            sa.Column("potential_amount", sa.Float()),
            sa.Column("currency", sa.String(3)),
            sa.Column("application_requirement", sa.Text()),
            sa.Column("evidence_reference", sa.Text()),
            sa.Column("created_at", sa.DateTime()),
        )
        print("[y5z6a7b8c9d0] created supranational_support_records")


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())

    for table in ("supranational_support_records", "co_production_structures"):
        if table in tables:
            op.drop_table(table)

    if "territory_scenarios" in tables:
        existing = {
            c["name"] for c in sa.inspect(conn).get_columns("territory_scenarios")
        }
        for name, _coltype in _SCENARIO_COLUMNS:
            if name in existing:
                op.drop_column("territory_scenarios", name)
