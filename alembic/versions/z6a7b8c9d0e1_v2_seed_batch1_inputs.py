"""v2_seed_batch1_inputs

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-08-22

Gives the six Batch 1 programmes a canonical identity, a statutory engine, and the
statutory inputs the wizard must ask for.

WHY ONLY SIX
------------
These are the only programmes with an executable formula in either handoff pack.
Master specification section 9 gives UK AVEC, UK IFTC, UK VFX, Canada federal
PSTC, CPTC and British Columbia PSTC, each with an official source. California,
Georgia and Illinois are named in the milestone gate but appear in no rulebook and
carry no rate, bucket definition or ceiling anywhere, so they cannot be seeded
without inventing rules.

CALCULATION STAYS BLOCKED
-------------------------
Declaring the inputs is not the same as approving the rules. The pack requires
official-source provenance, effective dates and administrator approval before a
programme may produce a figure, so ``calculation_verification_status`` stays
``blocked`` from migration x4y5z6a7b8c9 and an administrator promotes each
programme deliberately.

Questions are deliberately not gated on that. Collecting core expenditure while
the rule awaits approval is harmless and means the data is already there when it
lands; refusing to ask would make approval a second round trip to the producer.

WHY THE UK NEEDS TWO INPUTS AND THE DEMO ASKS FOR ONE
-----------------------------------------------------
The co-production demo collects a single base per territory. The UK rule is
``QS = MIN(actual UK core expenditure, 80% of relevant global core expenditure)``,
which needs both. Seeding only the local figure would leave the lower-of rule
unable to run and the UK permanently uncalculable, so both are declared required.

SOURCES
-------
UK AVEC, IFTC, VFX   HMRC and GOV.UK Creative Industries Expenditure Credit
                     guidance, via master specification sections 9.1 to 9.3.
Canada PSTC, CPTC    Canada Revenue Agency and CAVCO, via sections 9.4 and 9.5.
British Columbia     Government of British Columbia, via section 9.6.
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "z6a7b8c9d0e1"
down_revision = "y5z6a7b8c9d0"
branch_labels = None
depends_on = None


#: (programme_id, territory, programme name LIKE, country, subdivision, engine)
_IDENTITY = (
    ("GB_AVEC", "United Kingdom", "UK Audio-Visual Expenditure Credit%",
     "GB", None, "CORE_LOWER_OF"),
    ("GB_IFTC", "United Kingdom", "AVEC (Enhanced/IFTC)%",
     "GB", None, "CORE_LOWER_OF"),
    ("GB_VFX_ENHANCED", "United Kingdom", "UK VFX Expenditure Credit%",
     "GB", None, "VFX_ONLY"),
    ("CA_FEDERAL_PSTC", "Canada", "Production Services Tax Credit%",
     "CA", None, "QUALIFIED_LABOUR"),
    ("CA_CPTC", "Canada", "Canadian Film or Video Production Tax Credit%",
     "CA", None, "QUALIFIED_LABOUR"),
    ("CA_BC_PSTC", "British Columbia", "BC Production Services Tax Credit%",
     "CA", "CA-BC", "QUALIFIED_LABOUR"),
)

#: (programme_id, input_key, label, required_for_exact, help_text)
#:
#: Labels and help text are producer facing. They name the statutory term and
#: then explain it, which is the plain-English rule from the narrative
#: specification: use the correct term, then say what it means.
_REQUIRED_INPUTS = (
    ("GB_AVEC", "local_core_expenditure", "UK core expenditure", True,
     "Core production costs incurred in the UK: pre-production, principal "
     "photography and post-production. Not your whole UK spend, and not your "
     "total budget."),
    ("GB_AVEC", "global_core_expenditure", "Relevant global core expenditure", True,
     "The production's total core costs worldwide. The credit uses the lower of "
     "your UK core costs and 80 percent of this figure, so both are needed."),
    ("GB_IFTC", "local_core_expenditure", "UK core expenditure", True,
     "Core production costs incurred in the UK. The enhanced rate applies to the "
     "lower of this and 80 percent of relevant global core costs, capped at "
     "GBP 12,000,000 of qualifying expenditure."),
    ("GB_IFTC", "global_core_expenditure", "Relevant global core expenditure", True,
     "Total core costs worldwide. Independent film relief is only available "
     "where total core expenditure stays under GBP 23,500,000."),
    ("GB_VFX_ENHANCED", "vfx_expenditure", "Qualifying UK visual effects expenditure",
     True,
     "UK visual effects costs only. This cannot be inferred from your overall UK "
     "spend, and the 80 percent core cap does not apply to it."),
    ("CA_FEDERAL_PSTC", "qualified_labour", "Qualified Canadian labour expenditure",
     True,
     "The portion of Canadian payroll the programme allows into the calculation. "
     "The federal credit is 16 percent of this figure after any provincial "
     "assistance is deducted, never a percentage of your Canadian spend."),
    ("CA_CPTC", "qualified_labour", "Qualified labour expenditure", True,
     "Qualified labour for an eligible Canadian production. Capped at 60 percent "
     "of production cost net of assistance. This is the Canadian-content route, "
     "not the foreign service production route."),
    ("CA_BC_PSTC", "qualified_labour", "Accredited qualified BC labour", True,
     "Accredited British Columbia labour expenditure. The provincial credit is 36 "
     "percent of this figure, and it counts as assistance that reduces the "
     "federal calculation, so the two rates are never simply added."),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upgrade() -> None:
    conn = op.get_bind()

    for programme_id, territory, name_like, country, subdivision, engine in _IDENTITY:
        result = conn.execute(sa.text("""
            UPDATE incentive_programs
            SET programme_id             = :programme_id,
                jurisdiction_country     = :country,
                jurisdiction_subdivision = :subdivision,
                qs_engine_type           = :engine
            WHERE territory = :territory
              AND program ILIKE :name_like
              AND (status = 'active' OR status IS NULL)
        """), {
            "programme_id": programme_id, "country": country,
            "subdivision": subdivision, "engine": engine,
            "territory": territory, "name_like": name_like,
        })
        print(
            f"[z6a7b8c9d0e1] {programme_id:<16} engine={engine:<18} "
            f"on {result.rowcount} row(s)"
        )
        if result.rowcount == 0:
            print(
                f"[z6a7b8c9d0e1] WARNING {programme_id} matched no row. The "
                f"programme name has probably changed; the wizard will ask no "
                f"questions for it."
            )

    inputs_table = sa.table(
        "programme_required_inputs",
        sa.column("id", sa.String),
        sa.column("programme_id", sa.String),
        sa.column("input_key", sa.String),
        sa.column("label", sa.String),
        sa.column("input_type", sa.String),
        sa.column("required_for_exact", sa.Boolean),
        sa.column("help_text", sa.String),
        sa.column("missing_input_behavior", sa.String),
        sa.column("calculation_input_schema_version", sa.String),
        sa.column("created_at", sa.DateTime),
    )

    now = _now()
    rows = [
        {
            "id": f"{programme_id}:{input_key}",
            "programme_id": programme_id,
            "input_key": input_key,
            "label": label,
            "input_type": "currency",
            "required_for_exact": required,
            "help_text": help_text,
            # Every one of these engines refuses to substitute a base it does not
            # have, so an absent input is a status rather than an estimate.
            "missing_input_behavior": "requires_cost_breakdown",
            "calculation_input_schema_version": "1",
            "created_at": now,
        }
        for programme_id, input_key, label, required, help_text in _REQUIRED_INPUTS
    ]

    existing = {
        r[0] for r in conn.execute(
            sa.text("SELECT id FROM programme_required_inputs")
        )
    }
    fresh = [r for r in rows if r["id"] not in existing]
    if fresh:
        conn.execute(inputs_table.insert(), fresh)
    print(
        f"[z6a7b8c9d0e1] programme_required_inputs: {len(fresh)} declared, "
        f"{len(rows) - len(fresh)} already present"
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        DELETE FROM programme_required_inputs
        WHERE programme_id IN :ids
    """).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": [p for p, *_ in _IDENTITY]},
    )

    for programme_id, *_ in _IDENTITY:
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET programme_id             = NULL,
                jurisdiction_country     = NULL,
                jurisdiction_subdivision = NULL,
                qs_engine_type           = NULL
            WHERE programme_id = :programme_id
        """), {"programme_id": programme_id})
