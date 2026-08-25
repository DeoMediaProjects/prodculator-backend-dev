"""v2_california_program_4

Revision ID: a7b8c9d0e1f2
Revises: t0u1v2w3x4y5
Create Date: 2026-08-22

California Program 4.0, sourced from the official guidelines. This is the record
the milestone gate needs and that neither handoff pack contained.

SOURCE
------
California Film Commission, "CA Film and Television Tax Credit Program 4.0
Program Guidelines", version dated 1 January 2026.
https://cdn.film.ca.gov/wp-content/uploads/2025/08/4.0-Program-Guidelines-1.pdf
Programme runs 1 July 2025 to 30 June 2030. Annual allocation USD 750,000,000,
USD 3.75bn across five years.

A CONFLICT THAT HAD TO BE RESOLVED
----------------------------------
The Film Commission's own summary page at film.ca.gov/tax-credit/the-basics-4-0/
states a 25 percent base credit. The Program Guidelines state 35 percent. The
guidelines are the operative document and carry a version date, so 35 percent is
used and the summary page is treated as stale. Recording this because a later
reader checking the easier-to-find page will see a different number.

WHY THREE RECORDS RATHER THAN ONE
---------------------------------
The target inventory lists one California entry, but the guidelines define three
regimes that differ in rate, ceiling and transferability. The QA matrix requires
independent and non-independent to apply different qualified-expenditure
ceilings, which a single record cannot express. Splitting follows the pack's own
pattern for Germany, Portugal, New York and New Zealand.

    Non-independent   35% on up to USD 120m QE, effective credit cap USD 42m.
                      Refundable, non-transferable.
    Relocating TV     40% on up to USD 120m QE, effective cap USD 48m.
                      Non-transferable. Later seasons revert to 35% as Recurring TV.
    Independent film  35% on up to USD 20m QE, effective cap USD 7m.
                      Transferable or refundable.

UPLIFTS ARE NOT A RATE ON THE WHOLE BASE
----------------------------------------
Three uplifts each apply to their own subset of spend, which is why the engine is
MULTI_BUCKET and why each subset is a separate declared input:

    5%  Visual effects. TV (except Relocating TV and Animation series) and
        non-independent features. Gated: California VFX must reach USD 10m or be
        at least 75 percent of worldwide VFX cost.
    5%  Out of zone. Qualified wage and non-wage expenditure outside the LA zone.
    10% Local hire labour. Qualified wages to California residents who both
        reside outside the LA zone and work outside the LA zone. Relocating TV
        receives 5 percent. Does not apply to Animation.

Summing the headline percentages to 55 percent would be wrong, which is the same
mistake as adding federal and provincial Canadian rates.

CALCULATION REMAINS BLOCKED
---------------------------
Sourcing is not approval. The pack requires official-source provenance, effective
dates and administrator sign-off before a programme may produce a figure, so
``calculation_verification_status`` stays ``blocked``. The inputs are declared so
the wizard can collect them, and an administrator promotes the records once the
rules are reviewed.

NOT YET MODELLED, AND DELIBERATELY SO
-------------------------------------
Program 4.0 allocates by jobs ratio ranking within application windows rather than
first come first served, so an approved application is not a given. That is an
eligibility and allocation concern rather than a rate, and it belongs with the
allocation work rather than being buried in a rate record.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "t0u1v2w3x4y5"
branch_labels = None
depends_on = None


_SOURCE_URL = (
    "https://cdn.film.ca.gov/wp-content/uploads/2025/08/4.0-Program-Guidelines-1.pdf"
)
_AUTHORITY = "California Film Commission"
_LEGAL_REFERENCE = (
    "CA Film and Television Tax Credit Program 4.0 Program Guidelines, "
    "1 January 2026"
)
_EFFECTIVE_FROM = "2025-07-01"
_EFFECTIVE_TO = "2030-06-30"
_ANNUAL_POOL = 750_000_000.0

#: Uplifts, stored structurally so no consumer infers them from prose.
_UPLIFTS = [
    {
        "key": "visual_effects",
        "rate_percent": 5.0,
        "applies_to_input": "vfx_expenditure",
        "condition": (
            "California VFX expenditure of at least USD 10,000,000, or at least "
            "75 percent of total worldwide VFX cost"
        ),
        "excluded_categories": ["Relocating TV series", "Animation series",
                                "Animation films"],
    },
    {
        "key": "out_of_zone",
        "rate_percent": 5.0,
        "applies_to_input": "out_of_zone_expenditure",
        "condition": (
            "Qualified wage and non-wage expenditure for original photography "
            "outside the LA zone"
        ),
        "excluded_categories": ["Relocating TV series", "Animation series"],
    },
    {
        "key": "local_hire_labour",
        "rate_percent": 10.0,
        "applies_to_input": "local_hire_wages",
        "condition": (
            "Qualified wages to California residents who both reside outside the "
            "LA zone and work outside the LA zone"
        ),
        "excluded_categories": ["Animation films", "Animation TV series"],
        "reduced_rate_percent": 5.0,
        "reduced_for_categories": ["Relocating TV series"],
    },
]

#: (programme_id, programme name, base rate, QE ceiling, output cap, transferable)
_RECORDS = (
    ("US_CA_PROGRAM_4_NON_INDEPENDENT",
     "California Film & Television Tax Credit (Program 4.0) — Non-Independent",
     35.0, 120_000_000.0, 42_000_000.0, False),
    ("US_CA_PROGRAM_4_RELOCATING_TV",
     "California Film & Television Tax Credit (Program 4.0) — Relocating TV",
     40.0, 120_000_000.0, 48_000_000.0, False),
    ("US_CA_PROGRAM_4_INDEPENDENT",
     "California Film & Television Tax Credit (Program 4.0) — Independent Film",
     35.0, 20_000_000.0, 7_000_000.0, True),
)

#: Every record needs the base, and each uplift base it can earn.
_INPUTS = (
    ("qualified_production_expenditure", "Qualified California expenditure", True,
     "Wages and non-wage spend that qualify under the programme's expenditure "
     "chart. The credit applies to this figure, not to your total California "
     "spend and not to your budget."),
    ("vfx_expenditure", "Qualified California visual effects expenditure", False,
     "California visual effects costs. Earns an extra 5 percent, but only if they "
     "reach USD 10,000,000 or make up at least 75 percent of your worldwide "
     "visual effects cost."),
    ("out_of_zone_expenditure", "Qualified expenditure outside the LA zone", False,
     "Wage and non-wage spend on original photography outside the Los Angeles "
     "zone. Earns an extra 5 percent on that portion only."),
    ("local_hire_wages", "Qualified local hire wages", False,
     "Wages to California residents who both live outside the LA zone and work "
     "outside it. Earns an extra 10 percent on those wages, or 5 percent for a "
     "relocating television series."),
)


def _row_id(territory: str, program: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                          f"prodculator:incentive:{territory}:{program}"))


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    # The existing row becomes the non-independent record. Correcting it rather
    # than replacing it keeps the identity the rest of the system already knows.
    existing = conn.execute(sa.text("""
        SELECT id FROM incentive_programs
        WHERE territory = 'California' AND program ILIKE '%Program 4.0%'
        LIMIT 1
    """)).scalar()

    for index, (programme_id, name, rate, ceiling, output_cap, transferable) in enumerate(
        _RECORDS
    ):
        values = {
            "programme_id": programme_id,
            "program": name,
            "rate_gross": rate,
            "rate_net": rate,
            "qs_absolute_cap": ceiling,
            "output_cap": output_cap,
            "uplifts": json.dumps(_UPLIFTS),
            "source_url": _SOURCE_URL,
            "authority": _AUTHORITY,
            "legal_reference": _LEGAL_REFERENCE,
            "effective_from": _EFFECTIVE_FROM,
            "effective_to": _EFFECTIVE_TO,
            "annual_pool": _ANNUAL_POOL,
            "now": now,
        }

        if index == 0 and existing:
            values["id"] = existing
            conn.execute(sa.text("""
                UPDATE incentive_programs SET
                    programme_id                    = :programme_id,
                    program                         = :program,
                    jurisdiction_country            = 'US',
                    jurisdiction_subdivision        = 'US-CA',
                    qs_engine_type                  = 'MULTI_BUCKET',
                    rate_gross                      = :rate_gross,
                    rate_net                        = :rate_net,
                    qualifying_spend_cap_amount     = :qs_absolute_cap,
                    qualifying_spend_cap_currency   = 'USD',
                    rebate_cap_amount               = :output_cap,
                    rebate_cap_currency             = 'USD',
                    uplift_rules_json               = :uplifts,
                    annual_pool                     = :annual_pool,
                    annual_pool_type                = 'allocation_window',
                    preapproval_required            = true,
                    source_url                      = :source_url,
                    authority                       = :authority,
                    legal_reference                 = :legal_reference,
                    effective_from                  = CAST(:effective_from AS DATE),
                    effective_to                    = CAST(:effective_to AS DATE),
                    last_verified_at                = '2026-08-22',
                    calculation_verification_status = 'blocked',
                    status                          = 'active',
                    updated_at                      = :now
                WHERE id = :id
            """), values)
            print(f"[a7b8c9d0e1f2] {programme_id}: corrected the existing row")
            continue

        values["id"] = _row_id("California", name)
        already = conn.execute(sa.text(
            "SELECT 1 FROM incentive_programs WHERE programme_id = :programme_id"
        ), {"programme_id": programme_id}).scalar()
        if already:
            print(f"[a7b8c9d0e1f2] {programme_id}: already present, left alone")
            continue

        conn.execute(sa.text("""
            INSERT INTO incentive_programs (
                id, territory, program, programme_id,
                jurisdiction_country, jurisdiction_subdivision,
                qs_engine_type, rate_gross, rate_net, rate_type, currency,
                qualifying_spend_cap_amount, qualifying_spend_cap_currency,
                rebate_cap_amount, rebate_cap_currency,
                uplift_rules_json, annual_pool, annual_pool_type,
                preapproval_required, source_url, authority, legal_reference,
                effective_from, effective_to, last_verified_at,
                calculation_verification_status, status, is_supplementary,
                created_at, updated_at
            ) VALUES (
                :id, 'California', :program, :programme_id,
                'US', 'US-CA',
                'MULTI_BUCKET', :rate_gross, :rate_net, 'refundable_tax_credit', 'USD',
                :qs_absolute_cap, 'USD',
                :output_cap, 'USD',
                :uplifts, :annual_pool, 'allocation_window',
                true, :source_url, :authority, :legal_reference,
                CAST(:effective_from AS DATE), CAST(:effective_to AS DATE),
                '2026-08-22',
                'blocked', 'active', false,
                :now, :now
            )
        """), values)
        print(f"[a7b8c9d0e1f2] {programme_id}: inserted at {rate:g}%")

    # Independent credits are transferable; the other two are not.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET report_qualification_text =
            'Independent film credits may be sold or transferred, or refunded. '
            'Non-independent and relocating television credits are refundable but '
            'not transferable.'
        WHERE programme_id IN (
            'US_CA_PROGRAM_4_INDEPENDENT', 'US_CA_PROGRAM_4_NON_INDEPENDENT',
            'US_CA_PROGRAM_4_RELOCATING_TV'
        )
    """))

    inputs_table = sa.table(
        "programme_required_inputs",
        sa.column("id", sa.String), sa.column("programme_id", sa.String),
        sa.column("input_key", sa.String), sa.column("label", sa.String),
        sa.column("input_type", sa.String),
        sa.column("required_for_exact", sa.Boolean),
        sa.column("help_text", sa.String),
        sa.column("missing_input_behavior", sa.String),
        sa.column("calculation_input_schema_version", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    existing_inputs = {
        r[0] for r in conn.execute(sa.text("SELECT id FROM programme_required_inputs"))
    }
    rows = []
    for programme_id, *_ in _RECORDS:
        for input_key, label, required, help_text in _INPUTS:
            row_id = f"{programme_id}:{input_key}"
            if row_id in existing_inputs:
                continue
            rows.append({
                "id": row_id,
                "programme_id": programme_id,
                "input_key": input_key,
                "label": label,
                "input_type": "currency",
                "required_for_exact": required,
                "help_text": help_text,
                # Only the base is required for a figure. An absent uplift base
                # means the uplift is not earned, not that nothing can be
                # calculated, so the programme stays calculable without it.
                "missing_input_behavior": (
                    "requires_cost_breakdown" if required else "conditional_allowed"
                ),
                "calculation_input_schema_version": "1",
                "created_at": now,
            })
    if rows:
        conn.execute(inputs_table.insert(), rows)
    print(f"[a7b8c9d0e1f2] programme_required_inputs: {len(rows)} declared")


def downgrade() -> None:
    conn = op.get_bind()
    ids = [p for p, *_ in _RECORDS]

    conn.execute(
        sa.text("DELETE FROM programme_required_inputs WHERE programme_id IN :ids")
        .bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": ids},
    )
    conn.execute(
        sa.text("""
            DELETE FROM incentive_programs
            WHERE programme_id IN ('US_CA_PROGRAM_4_RELOCATING_TV',
                                   'US_CA_PROGRAM_4_INDEPENDENT')
        """)
    )
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET programme_id = NULL, qs_engine_type = NULL,
            uplift_rules_json = NULL, annual_pool = NULL,
            jurisdiction_country = NULL, jurisdiction_subdivision = NULL
        WHERE programme_id = 'US_CA_PROGRAM_4_NON_INDEPENDENT'
    """))
