"""split_australia_producer_offset_by_format

Revision ID: d2e3f4a5b6c7
Revises: b8c9d0e1f2a3
Create Date: 2026-08-30

PROBLEM
-------
The Australia "Producer Offset" row carried a single rate_gross of 40%, with
the lower rate for TV / non-theatrical work documented only in this project's
migration history (m4n5o6p7q8r9) rather than anywhere a report or the
calculator could read it:

    "Producer Offset for Australian producers: 40% for theatrically-released
    feature films; 30% is correct only for TV and non-theatrical formats."
    — m4n5o6p7q8r9, sourced to Screen Australia / ATO film industry
      incentives 2025.

That is the same blended-rate problem already solved for California (one
row, three qualifying-spend regimes with different rates — see
a7b8c9d0e1f2) and for the UK (AVEC / VFX Uplift / IFTC already exist as three
separate rows rather than one crammed string). This migration applies the
same treatment here: split into two rows, one per rate regime, so both
figures are visible as their own incentive line item instead of one hiding
the other.

NOT A NEW FACT
---------------
Both rates (40% feature, 30% TV/non-theatrical) were already established and
sourced by m4n5o6p7q8r9 — this migration surfaces the second figure that
was already true of the data, it does not introduce a new claim.

SCOPE, DELIBERATELY LIMITED
----------------------------
``applicable_formats`` is set on both rows for documentation, but
``format_eligibility_status`` is left unset (defaults to "unknown", per
format_eligibility.py's documented default for every existing row) rather
than "verified". Claiming "verified" would assert a complete whitelist this
migration has not cross-checked against Screen Australia's full guidelines
(e.g. animation, documentary edge cases) — an unjustified claim is worse than
an absent one here, per that module's own governing rule.

Both rows inherit the existing domestic-producer-only gating
(nationality_requirements, spv_eligible=False, set by g8h9i0j1k2l3 and
u2v3w4x5y6z7) unchanged: Producer Offset remains excluded from every foreign
production's report exactly as before. This migration only affects the rare
case of a genuinely Australian-owned production, where it now sees both
rates instead of only the feature-film one.
"""
from __future__ import annotations

import json as _json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

from app.alembic_utils import assert_migration_count

revision = "d2e3f4a5b6c7"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None

_TERRITORY = "Australia"
_ORIGINAL_PROGRAM = "Producer Offset"
_FEATURE_PROGRAM = "Producer Offset — Theatrical Feature Film"
_TV_PROGRAM = "Producer Offset — Television / Non-Theatrical"

_FEATURE_TIERS = _json.dumps([
    {"tier_type": "informational", "label": "Theatrical feature film",
     "rate_gross": 40, "rate_net": 40},
    {"label": "Television / non-theatrical format", "rate_gross": 30, "rate_net": 30},
])
_TV_TIERS = _json.dumps([
    {"tier_type": "informational", "label": "Television / non-theatrical format",
     "rate_gross": 30, "rate_net": 30},
    {"label": "Theatrical feature film", "rate_gross": 40, "rate_net": 40},
])

_SOURCE_NOTE = (
    "Split from the single 'Producer Offset' row so the TV / non-theatrical "
    "rate is a visible line item rather than only documented in migration "
    "history. Source: Screen Australia / ATO film industry incentives 2025 "
    "(as cited by m4n5o6p7q8r9)."
)


def _row_id(program: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"prodculator:incentive:{_TERRITORY}:{program}"))


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    original = conn.execute(
        sa.text(
            "SELECT * FROM incentive_programs "
            "WHERE territory = :territory AND program = :program"
        ),
        {"territory": _TERRITORY, "program": _ORIGINAL_PROGRAM},
    ).mappings().first()

    if original is None:
        # Already split by a previous run, or the seed data has moved on.
        # Idempotent no-op rather than a hard failure.
        return

    original_id = original["id"]

    # 1 — Relabel the existing row as the feature-film regime. Its rate_gross
    # of 40 is already correct (set by m4n5o6p7q8r9) and is left untouched.
    conn.execute(
        sa.text(
            "UPDATE incentive_programs SET "
            "  program = :program, "
            "  applicable_formats = :formats, "
            "  rate_tier_json = :tiers, "
            "  last_verified_at = :now "
            "WHERE id = :id"
        ),
        {
            "program": _FEATURE_PROGRAM,
            "formats": _json.dumps(["Feature Film"]),
            "tiers": _FEATURE_TIERS,
            "now": now,
            "id": original_id,
        },
    )

    # 2 — Clone the row for the TV / non-theatrical regime. Cloning the full
    # row (rather than reconstructing it column-by-column) guarantees every
    # governance field — nationality_requirements, spv_eligible, cap fields,
    # eligibility_rules_json, warnings_json, source_url — carries over
    # unchanged, so the new row is excluded from foreign-production reports
    # exactly like the original.
    new_row = dict(original)
    new_row["id"] = _row_id(_TV_PROGRAM)
    new_row["program"] = _TV_PROGRAM
    new_row["programme_id"] = None  # avoid colliding with the original's, if any
    new_row["rate_gross"] = 30
    new_row["rate_net"] = 30
    new_row["applicable_formats"] = _json.dumps(["TV Series", "Documentary"])
    new_row["rate_tier_json"] = _TV_TIERS
    new_row["last_verified_at"] = now
    new_row["created_at"] = now
    new_row["updated_at"] = now

    columns = list(new_row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    column_list = ", ".join(columns)
    conn.execute(
        sa.text(
            f"INSERT INTO incentive_programs ({column_list}) VALUES ({placeholders})"  # noqa: S608
        ),
        new_row,
    )

    assert_migration_count(
        conn, "incentive_programs",
        f"territory = '{_TERRITORY}' AND program IN "
        f"('{_FEATURE_PROGRAM}', '{_TV_PROGRAM}')",
        expected_min=2,
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "DELETE FROM incentive_programs "
            "WHERE territory = :territory AND program = :program"
        ),
        {"territory": _TERRITORY, "program": _TV_PROGRAM},
    )
    conn.execute(
        sa.text(
            "UPDATE incentive_programs SET "
            "  program = :program, "
            "  applicable_formats = NULL, "
            "  rate_tier_json = NULL "
            "WHERE territory = :territory AND program = :feature_program"
        ),
        {
            "program": _ORIGINAL_PROGRAM,
            "territory": _TERRITORY,
            "feature_program": _FEATURE_PROGRAM,
        },
    )
