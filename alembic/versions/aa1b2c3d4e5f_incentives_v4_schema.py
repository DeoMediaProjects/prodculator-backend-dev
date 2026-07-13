"""incentives_v4_schema

Revision ID: aa1b2c3d4e5f
Revises: 4c5f82471e4f
Create Date: 2026-07-09

Aligns incentive_programs with the v4 source-of-truth database
(docs/data-sources/prodculator_incentive_database_v4.html).

ADDITIVE ONLY — no existing column is dropped, renamed, or retyped.

New columns store the v4 display/verification layer alongside the numeric
engine columns:

  rate_gross_display / rate_net_display   full rate strings ("34%", tiered,
                                          "None") — the numeric rate_gross /
                                          rate_net keep the engine floor
  rebate_cap_display / per_person_cap_display
                                          full cap strings; numeric caps stay
                                          in rebate_cap_amount / cap_per_person
  payment_timeline                        display string ("90–180 days", prose)
  notes / authority / ai_rule             v4 editorial + AI guidance fields
  confidence (0–100) / verification_status / bank_pts
                                          honesty gating — low-confidence rows
                                          must degrade rather than present a
                                          confident wrong number
  budget_eligibility_ceiling / annual_programme_cap
                                          eligibility + programme-pool text
  mechanism_pattern / qs_basis / calc_formula / regional_funds_note / cap_type
                                          calculation provenance
  region                                  coarse geographic grouping

pay_reliability from the handoff is NOT added: the repo already has
payment_reliability (float 0–1) consumed by reports/scoring.py — the v4
payReliability values land there instead.

Also:
  * expands ck_incentive_programs_rate_type with 'labour_credit' (new in v4)
  * ensures the UNIQUE (territory, program) index exists (a territory now has
    multiple programmes — territory alone is NOT unique)
  * creates incentive_programs / sync_settings / pending_changes in full when
    absent — local dev DBs were stamped past the original create migrations,
    which skip silently when their table is missing
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "aa1b2c3d4e5f"
down_revision = "4c5f82471e4f"
branch_labels = None
depends_on = None

_UNIQUE_INDEX = "uq_incentive_programs_territory_program"
_RATE_TYPE_CHECK = "ck_incentive_programs_rate_type"

# Superset of l4m5n6o7p8q9's canonical set; v4 adds labour_credit
# (Canada federal/provincial labour-expenditure credits).
_VALID_RATE_TYPES = (
    "cash_rebate",
    "tax_credit",
    "enhanced_tax_credit",
    "refundable_tax_credit",
    "transferable_tax_credit",
    "labour_credit",
    "grant",
    "cash_grant",
    "tax_shelter",
)

_NEW_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("rate_gross_display", sa.Text()),
    ("rate_net_display", sa.Text()),
    ("rebate_cap_display", sa.Text()),
    ("per_person_cap_display", sa.Text()),
    ("payment_timeline", sa.Text()),
    ("notes", sa.Text()),
    ("authority", sa.Text()),
    ("ai_rule", sa.Text()),
    ("confidence", sa.Integer()),
    ("budget_eligibility_ceiling", sa.Text()),
    ("annual_programme_cap", sa.Text()),
    ("mechanism_pattern", sa.Text()),
    ("qs_basis", sa.Text()),
    ("verification_status", sa.Text()),
    ("calc_formula", sa.Text()),
    ("regional_funds_note", sa.Text()),
    ("cap_type", sa.Text()),
    ("bank_pts", sa.Integer()),
    ("region", sa.Text()),
]

# Full accumulated schema (base table + every additive migration to date).
# Used only when the table is missing entirely so a fresh/stamped DB ends up
# identical to one that ran the whole history.
_EXISTING_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("territory", sa.Text()),
    ("program", sa.Text()),
    ("rate", sa.Text()),
    ("cap", sa.Text()),
    ("last_updated", sa.Text()),
    ("status", sa.Text()),
    ("source_url", sa.Text()),
    ("auto_sync_enabled", sa.Boolean()),
    ("last_auto_check", sa.Text()),
    ("created_at", sa.DateTime()),
    ("updated_at", sa.DateTime()),
    # g1h2i3j4k5l6 — enriched territory data
    ("rate_gross", sa.Float()),
    ("rate_net", sa.Float()),
    ("rate_type", sa.Text()),
    ("rate_tier_json", sa.Text()),
    ("cap_amount", sa.Float()),
    ("cap_currency", sa.Text()),
    ("cap_per_person", sa.Float()),
    ("cap_per_person_currency", sa.Text()),
    ("qualifying_spend_min", sa.Float()),
    ("qualifying_spend_cap_pct", sa.Float()),
    ("qualifying_spend_currency", sa.Text()),
    ("payment_timeline_days_min", sa.Integer()),
    ("payment_timeline_days_max", sa.Integer()),
    ("payment_timeline_notes", sa.Text()),
    ("eligibility_rules_json", sa.Text()),
    ("expiry_date", sa.Date()),
    ("currency", sa.Text()),
    ("warnings_json", sa.Text()),
    ("last_verified_at", sa.DateTime()),
    ("source_name", sa.Text()),
    # d5e6f7g8h9i0
    ("qualifying_spend_type", sa.Text()),
    ("qualifying_spend_labour_pct", sa.Float()),
    # e6f7g8h9i0j1
    ("rebate_cap_amount", sa.Float()),
    ("rebate_cap_currency", sa.Text()),
    # k3l4m5n6o7p8 — atomic facts
    ("net_rate_pct", sa.Float()),
    ("payee_note", sa.Text()),
    ("filing_note", sa.Text()),
    # l6m7n8o9p0q1 — regional stacking
    ("scope", sa.Text()),
    ("parent_territory", sa.Text()),
    ("stacking_group", sa.Text()),
    ("stackable_with", sa.Text()),
    # m5n6o7p8q9r0
    ("cultural_test_required", sa.Boolean()),
    ("admin_complexity", sa.Text()),
    # m7n8o9p0q1r2 — nationality / co-production
    ("nationality_requirements", sa.Text()),
    ("co_production_eligible", sa.Boolean()),
    ("co_production_treaties", sa.Text()),
    ("spv_eligible", sa.Boolean()),
    # o6p7q8r9s0t1
    ("cap_basis", sa.Text()),
    ("atl_exempt", sa.Boolean()),
    # q1r2s3t4u5v6
    ("vfx_uplift_pct", sa.Numeric(5, 2)),
    ("programme_level", sa.Text()),
    ("eligibility_notes", sa.Text()),
    # z3d4e5f6g7h8
    ("payment_reliability", sa.Float()),
    # a1b2c3d4e5f7
    ("applicable_formats", sa.Text()),
]


def _rate_type_check_sql() -> str:
    placeholders = ", ".join(f"'{v}'" for v in _VALID_RATE_TYPES)
    return f"rate_type IN ({placeholders})"


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "incentive_programs" not in tables:
        op.create_table(
            "incentive_programs",
            sa.Column("id", sa.Text(), nullable=False),
            *[
                sa.Column(
                    name,
                    col_type,
                    nullable=name not in ("territory", "program"),
                )
                for name, col_type in _EXISTING_COLUMNS + _NEW_COLUMNS
            ],
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(_rate_type_check_sql(), name=_RATE_TYPE_CHECK),
        )
        op.create_index(
            _UNIQUE_INDEX,
            "incentive_programs",
            ["territory", "program"],
            unique=True,
        )
    else:
        existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}
        for name, col_type in _NEW_COLUMNS:
            if name not in existing_cols:
                op.add_column(
                    "incentive_programs", sa.Column(name, col_type, nullable=True)
                )

        indexes = {ix["name"] for ix in inspector.get_indexes("incentive_programs")}
        if _UNIQUE_INDEX not in indexes:
            op.create_index(
                _UNIQUE_INDEX,
                "incentive_programs",
                ["territory", "program"],
                unique=True,
            )

        # Expand the rate_type CHECK with v4's labour_credit. DROP IF EXISTS +
        # recreate is the only portable way to widen a CHECK.
        conn.execute(
            sa.text(
                f"ALTER TABLE incentive_programs "
                f"DROP CONSTRAINT IF EXISTS {_RATE_TYPE_CHECK}"
            )
        )
        op.create_check_constraint(
            _RATE_TYPE_CHECK, "incentive_programs", _rate_type_check_sql()
        )

    # The incentives admin depends on these two tables; stamped local DBs are
    # missing them because b2c3d4e5f6g7 never re-runs. Definitions match
    # b2c3d4e5f6g7 + d738ccebf985 (record_label).
    if "sync_settings" not in tables:
        op.create_table(
            "sync_settings",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("resource_type", sa.Text(), nullable=False),
            sa.Column("schedule", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("last_sync_at", sa.DateTime(), nullable=True),
            sa.Column("next_scheduled", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("resource_type"),
        )

    if "pending_changes" not in tables:
        op.create_table(
            "pending_changes",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("resource_type", sa.Text(), nullable=False),
            sa.Column("resource_id", sa.Text(), nullable=True),
            sa.Column("territory", sa.Text(), nullable=False),
            sa.Column("field", sa.Text(), nullable=False),
            sa.Column("current_value", sa.Text(), nullable=True),
            sa.Column("detected_value", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Text(), nullable=False),
            sa.Column("source", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_by", sa.Text(), nullable=True),
            sa.Column("record_label", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "incentive_programs" not in inspector.get_table_names():
        return
    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}
    for name, _ in _NEW_COLUMNS:
        if name in existing_cols:
            op.drop_column("incentive_programs", name)
    # Restore the pre-v4 CHECK (without labour_credit)
    conn.execute(
        sa.text(
            f"ALTER TABLE incentive_programs "
            f"DROP CONSTRAINT IF EXISTS {_RATE_TYPE_CHECK}"
        )
    )
    narrow = ", ".join(
        f"'{v}'" for v in _VALID_RATE_TYPES if v != "labour_credit"
    )
    op.create_check_constraint(
        _RATE_TYPE_CHECK, "incentive_programs", f"rate_type IN ({narrow})"
    )
