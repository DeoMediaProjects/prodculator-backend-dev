"""fix_gmpf_rebate_cap_and_spend_requirements

Revision ID: p8q9r0s1t2u3
Revises: o7p8q9r0s1t2
Create Date: 2026-03-28

ROOT CAUSES
-----------

1. GMPF rebate_cap_amount was NULL — rebate ceiling never enforced.

   The per-film maximum grant is €5,000,000 per GMPF Guidelines 2026 §7.3:
   "The maximum total subsidy amount is 5 million euros per film."
   The €20M figure stored in cap_amount is the per-series cap — not the film cap.

   Brooklyn Nick ($30M feature): computed rebate = EUR 7,812,900; capped = EUR 5,000,000.
   Overstatement: EUR 2,812,900.

   Without rebate_cap_amount the validator never applied the ceiling.  The fix
   adds rebate_cap_amount = 5,000,000 EUR so the hard cap is enforced.

2. cap_amount = 20,000,000 (EUR) was a series cap stored as a budget-threshold.

   cap_amount signals the validator to switch to an alternative programme when
   budget_gbp exceeds the raw numeric value.  For GMPF there is no alternative
   programme — the €20M series cap is irrelevant for film rows.  Having any
   non-zero cap_amount risks the validator treating it as a GBP budget threshold
   (20,000,000 GBP ≈ £20M), triggering a spurious programme-switch check for
   any production > £20M.  Cleared to NULL.

3. qualifying_spend_min = 8,000,000 (EUR) does not correspond to any official
   GMPF threshold.

   Per GMPF Guidelines 2026 §4.1, eligibility requires:
     a) Total production costs ≥ €25,000,000 AND
     b) German production costs ≥ max(€13,000,000, 40% of total production costs).

   The €8M figure had no basis in the guidelines.  Updated to the German
   production costs minimum (€13M), the binding threshold for most productions.
   The €25M total production cost requirement is described in eligibility_notes.

Source: German Federal Film Fund (FFA) — GMPF Guidelines 2026 (in force Jan 2025)
Last Verified: 2026-03-28
"""
from __future__ import annotations

import json as _json

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "p8q9r0s1t2u3"
down_revision = "o7p8q9r0s1t2"
branch_labels = None
depends_on = None

_GMPF_WARNINGS = _json.dumps([
    (
        "MINIMUM SPEND: Total production costs ≥ €25M AND German production costs "
        "≥ max(€13M, 40% of total). Neither threshold is approximate — verify both "
        "with your production accountant before submitting."
    ),
    "Competitive grant — not automatically awarded; approval required from FFA",
    (
        "Stackable with DFFF (German Federal Film Fund / Deutscher Filmförderfonds): "
        "combined benefit can reach 60% on eligible German spend (DFFF 30% + GMPF 30%)"
    ),
    "Rate increased from 25% to 30% effective January 2025 DFFF/GMPF reform",
    "Per-film maximum grant: €5M (per-series maximum: €20M — film row uses €5M cap)",
])


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_amount       = 5000000.0,
            rebate_cap_currency     = 'EUR',
            cap_amount              = NULL,
            qualifying_spend_min    = 13000000.0,
            qualifying_spend_currency = 'EUR',
            warnings_json           = :warnings,
            last_verified_at        = '2026-03-28'
        WHERE territory = 'Germany'
          AND program   = 'German Motion Picture Fund (GMPF)'
          AND status    = 'active'
    """), {"warnings": _GMPF_WARNINGS})

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Germany' "
            "AND program = 'German Motion Picture Fund (GMPF)' "
            "AND rebate_cap_amount = 5000000.0 "
            "AND cap_amount IS NULL"
        ),
        expected_min=1,
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()

    _old_warnings = _json.dumps([
        "High minimum spend threshold (€8M) — only for large-scale productions",
        "Competitive grant — not guaranteed",
        (
            "Stackable with DFFF — combined benefit can reach 60% on eligible German spend "
            "(DFFF 30% + GMPF 30%)"
        ),
        "Rate increased from 25% to 30% and cap reduced from €25M to €20M effective January 2025 reform",
    ])

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_amount       = NULL,
            rebate_cap_currency     = NULL,
            cap_amount              = 20000000.0,
            qualifying_spend_min    = 8000000.0,
            qualifying_spend_currency = 'EUR',
            warnings_json           = :warnings
        WHERE territory = 'Germany'
          AND program   = 'German Motion Picture Fund (GMPF)'
          AND status    = 'active'
    """), {"warnings": _old_warnings})
