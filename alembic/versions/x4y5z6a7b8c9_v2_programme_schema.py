"""v2_programme_schema

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-08-22

Incentive Engine v2, phases 01 and 02. Adds the programme rule schema, rule
versioning with effective dating, the four separated verification statuses, and
the scenario ingestion tables.

Purely additive. No existing column changes meaning and no existing programme ID
changes, so the current engine keeps working while both schemas coexist. That is
the specification's own sequencing instruction: add v2 schema and rule versioning
without breaking existing programme IDs.

WHAT IS ADDED VERSUS REUSED
---------------------------
The v2 Database Field Specification names 51 fields. Roughly twenty already exist
under a different name, and duplicating them would leave two columns per concept
with no rule about which wins. ``base_rate`` is ``rate_gross``,
``qs_absolute_cap`` is ``qualifying_spend_cap_amount``, ``official_source_url``
is ``source_url``, ``credit_output_cap`` is ``rebate_cap_amount``, and so on.

``app.modules.incentives.v2_contracts.V2_FIELD_MAP`` is the single answer to
"where does this v2 field live", and this migration adds exactly the columns that
map answers with a new name. A test asserts the two stay in step, so a v2 field
cannot quietly acquire a second home.

NEW TABLES
----------
programme_rule_versions   Effective-dated rule history. Prior versions are kept
                          rather than overwritten, so a project whose qualifying
                          period predates a rate change still calculates under
                          the rule that applied to it.
programme_required_inputs Which statutory inputs a programme needs, declared per
                          rule version, so the wizard asks the right questions
                          without a deployment.
territory_scenarios       One alternative territory spend per compared territory.
scenario_calculation_inputs  Producer-supplied statutory bases with provenance.
calculation_results       The deterministic result and its audit trace.

NULL SEMANTICS
--------------
Every amount column here is nullable and stays nullable. NULL means unknown and
zero means known zero, and nothing may coerce one into the other or into a budget
figure. That is the rule the whole rebuild turns on, and it is enforced in
``v2_contracts.resolve_statutory_amount`` rather than by a database default.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "x4y5z6a7b8c9"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None


#: Columns added to incentive_programs, being exactly the v2 fields with no
#: existing home. Kept in the same order as the specification's field groups.
_NEW_PROGRAMME_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    # Identity
    ("programme_id", sa.String(96)),
    ("jurisdiction_country", sa.String(96)),
    ("jurisdiction_subdivision", sa.String(96)),
    ("parent_programme_id", sa.String(96)),
    # Versioning
    ("rule_version", sa.String(32)),
    ("effective_from", sa.Date()),
    ("effective_to", sa.Date()),
    # Scenario
    ("scenario_spend_supported", sa.Boolean()),
    ("scenario_spend_currency_policy", sa.String(32)),
    # Qualifying spend
    ("qs_formula_version", sa.String(32)),
    ("eligible_cost_definition", sa.Text()),
    ("excluded_cost_definition", sa.Text()),
    ("minimum_spend_basis", sa.String(64)),
    # Rate
    ("uplift_rules_json", sa.Text()),
    ("effective_rate_method", sa.String(48)),
    # Caps
    ("per_person_cap_json", sa.Text()),
    ("annual_pool", sa.Float()),
    ("annual_pool_type", sa.String(48)),
    # Access
    ("foreign_producer_access", sa.String(48)),
    ("foreign_producer_route", sa.Text()),
    ("local_entity_requirement", sa.Text()),
    ("application_timing_requirement", sa.Text()),
    ("preapproval_required", sa.Boolean()),
    # Interaction
    ("stacking_allowed", sa.Boolean()),
    ("assistance_treatment_json", sa.Text()),
    ("calculation_order", sa.Integer()),
    ("mutual_exclusion_group", sa.String(64)),
    # Control
    ("calculation_verification_status", sa.String(24)),
    ("bankability_research_status", sa.String(32)),
    ("legal_reference", sa.Text()),
    # Reporting
    ("report_qualification_text", sa.Text()),
    ("report_warning_text", sa.Text()),
    ("missing_input_message", sa.Text()),
    ("confidence_display", sa.String(48)),
)


def _add_missing_columns(conn) -> None:
    existing = {c["name"] for c in sa.inspect(conn).get_columns("incentive_programs")}
    added = 0
    for name, coltype in _NEW_PROGRAMME_COLUMNS:
        if name not in existing:
            op.add_column("incentive_programs", sa.Column(name, coltype, nullable=True))
            added += 1
    print(f"[x4y5z6a7b8c9] incentive_programs: {added} v2 column(s) added")


def upgrade() -> None:
    conn = op.get_bind()
    _add_missing_columns(conn)

    # Every existing record starts unverified for calculation. A source-verified
    # badge must not imply the formula is ready, so the default is the safe end of
    # the gate and each programme is promoted only when its rules are converted.
    result = conn.execute(sa.text("""
        UPDATE incentive_programs
        SET calculation_verification_status = 'blocked'
        WHERE calculation_verification_status IS NULL
    """))
    print(
        f"[x4y5z6a7b8c9] calculation_verification_status defaulted to 'blocked' "
        f"on {result.rowcount} row(s); promote per programme as rules are verified"
    )

    tables = set(sa.inspect(conn).get_table_names())

    # ── rule versioning ──────────────────────────────────────────────────────
    if "programme_rule_versions" not in tables:
        op.create_table(
            "programme_rule_versions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("programme_id", sa.String(96), nullable=False, index=True),
            sa.Column("rule_version", sa.String(32), nullable=False),
            sa.Column("qs_formula_version", sa.String(32)),
            sa.Column("effective_from", sa.Date(), nullable=False),
            # NULL effective_to means "current", which is why version selection
            # must treat it as open ended rather than skipping the row.
            sa.Column("effective_to", sa.Date()),
            sa.Column("rule_json", sa.Text(), nullable=False),
            sa.Column("official_authority", sa.Text()),
            sa.Column("official_source_url", sa.Text()),
            sa.Column("legal_reference", sa.Text()),
            sa.Column("calculation_verification_status", sa.String(24)),
            sa.Column("verified_at", sa.Date()),
            sa.Column("approved_by", sa.String(128)),
            sa.Column("created_at", sa.DateTime()),
            sa.UniqueConstraint(
                "programme_id", "rule_version", name="uq_programme_rule_version"
            ),
        )
        print("[x4y5z6a7b8c9] created programme_rule_versions")

    # ── programme-declared statutory inputs ──────────────────────────────────
    if "programme_required_inputs" not in tables:
        op.create_table(
            "programme_required_inputs",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("programme_id", sa.String(96), nullable=False, index=True),
            sa.Column("rule_version", sa.String(32)),
            sa.Column("input_key", sa.String(64), nullable=False),
            sa.Column("label", sa.Text(), nullable=False),
            sa.Column("input_type", sa.String(32), nullable=False,
                      server_default="currency"),
            sa.Column("required_for_exact", sa.Boolean(), nullable=False,
                      server_default=sa.text("true")),
            sa.Column("help_text", sa.Text()),
            sa.Column("dependency_rules_json", sa.Text()),
            sa.Column("validation_rules_json", sa.Text()),
            sa.Column("missing_input_behavior", sa.String(32)),
            sa.Column("calculation_input_schema_version", sa.String(32)),
            sa.Column("created_at", sa.DateTime()),
            sa.UniqueConstraint(
                "programme_id", "rule_version", "input_key",
                name="uq_programme_input",
            ),
        )
        print("[x4y5z6a7b8c9] created programme_required_inputs")

    # ── scenario ingestion ───────────────────────────────────────────────────
    if "territory_scenarios" not in tables:
        op.create_table(
            "territory_scenarios",
            sa.Column("scenario_id", sa.String(64), primary_key=True),
            sa.Column("report_id", sa.String(64), nullable=False, index=True),
            # Canonical IDs. Free text may still drive creative and location
            # analysis, but it must never resolve to a financial programme.
            sa.Column("territory_id", sa.String(16), nullable=False),
            sa.Column("subdivision_id", sa.String(16)),
            # Nullable on purpose. A selected territory with no spend entered yet
            # is a real state, and coercing it to zero or to the budget is the
            # substitution the rebuild removes.
            sa.Column("scenario_spend", sa.Float()),
            sa.Column("scenario_currency", sa.String(3)),
            sa.Column("scenario_spend_source", sa.String(32)),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint(
                "report_id", "territory_id", "subdivision_id",
                name="uq_scenario_per_territory",
            ),
        )
        print("[x4y5z6a7b8c9] created territory_scenarios")

    if "scenario_calculation_inputs" not in tables:
        op.create_table(
            "scenario_calculation_inputs",
            sa.Column("input_id", sa.String(64), primary_key=True),
            sa.Column("scenario_id", sa.String(64), nullable=False, index=True),
            sa.Column("programme_id", sa.String(96)),
            sa.Column("input_key", sa.String(64), nullable=False),
            # NULL means unknown. Zero means the producer stated zero. Nothing may
            # coerce between the two.
            sa.Column("amount", sa.Float()),
            sa.Column("currency", sa.String(3)),
            sa.Column("input_status", sa.String(24), nullable=False,
                      server_default="unknown"),
            sa.Column("input_source", sa.String(32)),
            sa.Column("schema_version", sa.String(32)),
            sa.Column("notes", sa.Text()),
            sa.Column("entered_at", sa.DateTime()),
            sa.UniqueConstraint(
                "scenario_id", "programme_id", "input_key",
                name="uq_scenario_input",
            ),
        )
        print("[x4y5z6a7b8c9] created scenario_calculation_inputs")

    if "calculation_results" not in tables:
        op.create_table(
            "calculation_results",
            sa.Column("result_id", sa.String(64), primary_key=True),
            sa.Column("scenario_id", sa.String(64), nullable=False, index=True),
            sa.Column("programme_id", sa.String(96), nullable=False),
            sa.Column("rule_version", sa.String(32)),
            sa.Column("calculation_status", sa.String(32), nullable=False),
            sa.Column("qualifying_spend", sa.Float()),
            sa.Column("qualifying_spend_basis", sa.Text()),
            sa.Column("base_rate", sa.Float()),
            sa.Column("effective_rate", sa.Float()),
            sa.Column("gross_incentive", sa.Float()),
            sa.Column("estimated_net_incentive", sa.Float()),
            sa.Column("applied_caps_json", sa.Text()),
            sa.Column("applied_tiers_json", sa.Text()),
            sa.Column("applied_uplifts_json", sa.Text()),
            sa.Column("stacking_adjustments_json", sa.Text()),
            sa.Column("missing_inputs_json", sa.Text()),
            sa.Column("eligibility_conditions_json", sa.Text()),
            sa.Column("input_provenance_json", sa.Text()),
            sa.Column("audit_trace_json", sa.Text()),
            sa.Column("generated_at", sa.DateTime()),
        )
        print("[x4y5z6a7b8c9] created calculation_results")


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())

    for table in (
        "calculation_results",
        "scenario_calculation_inputs",
        "territory_scenarios",
        "programme_required_inputs",
        "programme_rule_versions",
    ):
        if table in tables:
            op.drop_table(table)

    existing = {c["name"] for c in sa.inspect(conn).get_columns("incentive_programs")}
    for name, _coltype in _NEW_PROGRAMME_COLUMNS:
        if name in existing:
            op.drop_column("incentive_programs", name)
