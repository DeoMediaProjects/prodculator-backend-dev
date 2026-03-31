"""fix_ireland_481_vfx_tier_type

Revision ID: q9r0s1t2u3v4
Revises: p8q9r0s1t2u3
Create Date: 2026-03-28

ROOT CAUSE
----------
Migration n6o7p8q9r0s1 stamped tier_type = "spend_boundary" on Ireland Section
481's rate_tier_json because Ireland appears in _SPEND_BOUNDARY_PROGRAMS.  The
mechanical model is correct: 40% applies to the first €10M, 32% above.  But the
40% tier has an enabling condition: qualifying VFX spend must reach ≥€1M before
the uplift activates at all.

The validator's spend-boundary path has no mechanism to enforce enabling
conditions — it applies the blended rate purely from the spend boundary amount.
For a non-VFX production (e.g. Brooklyn Nick — "minimal VFX requirements") this
fires unconditionally, overstating the rebate.

Brooklyn Nick ($30M feature, minimal VFX):
    Reported blended rate: ~35.5%  (40% × €10M + 32% × €12.1M) / €22.1M
    Reported rebate:       EUR 7,867,846
    Correct flat rate:     32%
    Correct rebate:        EUR 7,083,696
    Overstatement:         EUR ~784,150

FIX
---
Change tiers[0].tier_type from "spend_boundary" to "informational".

The validator's informational path does not extract a tier boundary and uses
the headline rate_gross (32%) directly.  This is:
  - Always correct for productions that do NOT meet the ≥€1M VFX threshold.
  - Conservative (not overstating) for productions that DO qualify — VFX
    productions should validate the uplift independently with their accountant.

The tiers remain in rate_tier_json so the eligibility_notes can document the
VFX uplift opportunity; they just do not drive the blended-rate calculation.

DESIGN NOTE: A tier_condition field could gate the spend-boundary path on a
runtime check, but this would require VFX budget data to flow into the validator.
The conservative informational approach avoids false uplifts for the majority
of productions and remains auditable.

Source: Finance Act 2025 (Ireland) — Section 481 amendment; Screen Ireland
Budget 2026 announcement.
Last Verified: 2026-03-28
"""
from __future__ import annotations

import json as _json

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "q9r0s1t2u3v4"
down_revision = "p8q9r0s1t2u3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    row = conn.execute(sa.text("""
        SELECT id, rate_tier_json FROM incentive_programs
        WHERE territory = 'Ireland'
          AND program   = 'Section 481 Tax Credit'
          AND status    = 'active'
    """)).fetchone()

    if row is None:
        raise AssertionError(
            f"[{revision}] Ireland Section 481 Tax Credit not found — cannot patch tier_type"
        )

    row_id, tier_json_raw = row
    try:
        tiers = _json.loads(tier_json_raw) if isinstance(tier_json_raw, str) else list(tier_json_raw)
    except (ValueError, TypeError) as exc:
        raise AssertionError(
            f"[{revision}] Ireland Section 481 rate_tier_json is not valid JSON: {exc}"
        ) from exc

    if not isinstance(tiers, list) or not tiers:
        raise AssertionError(
            f"[{revision}] Ireland Section 481 rate_tier_json is empty or not a list"
        )

    # Patch tiers[0].tier_type: spend_boundary → informational
    updated = list(tiers)
    updated[0] = {**updated[0], "tier_type": "informational"}

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_tier_json   = :tiers,
            last_verified_at = '2026-03-28'
        WHERE id = :id
    """), {"tiers": _json.dumps(updated), "id": row_id})

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Ireland' "
            "AND program = 'Section 481 Tax Credit' "
            "AND rate_tier_json LIKE '%\"informational\"%'"
        ),
        expected_min=1,
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()

    row = conn.execute(sa.text("""
        SELECT id, rate_tier_json FROM incentive_programs
        WHERE territory = 'Ireland'
          AND program   = 'Section 481 Tax Credit'
          AND status    = 'active'
    """)).fetchone()

    if row is None:
        return

    row_id, tier_json_raw = row
    try:
        tiers = _json.loads(tier_json_raw) if isinstance(tier_json_raw, str) else list(tier_json_raw)
    except (ValueError, TypeError):
        return

    if not isinstance(tiers, list) or not tiers:
        return

    updated = list(tiers)
    updated[0] = {**updated[0], "tier_type": "spend_boundary"}

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate_tier_json = :tiers
        WHERE id = :id
    """), {"tiers": _json.dumps(updated), "id": row_id})
