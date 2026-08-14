"""FIX-02 (California backfill): record confirmed short-film format exclusion.

``n4o5p6q7r8s9`` added the ``eligible_formats`` tri-state column but seeded it
nowhere — every row, including California's, defaults to "not researched" and a
short film's rebate against every programme is shown as unverified rather than
either confirmed or excluded.

For California Film & Television Tax Credit (Program 4.0) specifically, this is a
researched fact, not an open question: the CFC's own 2026 Program Guidelines
enumerate eligible project types explicitly (TV series/pilot/limited series,
independent and non-independent feature films, relocating TV series, large-scale
competition shows, animated/live-action TV with episodes averaging 20+ minutes).
Short film is not a named category. This is a categorical exclusion, separate from
and in addition to the programme's existing USD 1,000,000 qualifying-spend floor
(``qualifying_spend_min``, already correctly recorded) — a short that somehow
cleared the spend floor would still not be an eligible format.

Source: California Film Commission, Program 4.0 Program Guidelines
(cdn.film.ca.gov, effective Jan 2026).

Before this migration, a short-format production against this programme showed
``FORMAT ELIGIBILITY UNVERIFIED`` — true in the sense that nobody had told the
engine either way, but understating what is actually a confirmed no. After, it
shows a verified ``INELIGIBLE`` verdict citing the real reason, and the row no
longer needs the blanket "format eligibility unverified" caveat riding alongside
its already-correct budget-floor exclusion.

This does not touch ``qualifying_spend_min``, rate, or any other field on the
row — the budget-floor gate that already excludes this production is untouched;
this adds the separate, format-shaped gate the fix package identified as missing.

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-08-14
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "s9t0u1v2w3x4"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None

_TABLE = "incentive_programs"
_TERRITORY = "California"
_PROGRAM = "California Film & Television Tax Credit (Program 4.0)"

# Matches FORMAT_KEYS in app/modules/reports/format_eligibility.py.
_ELIGIBLE_FORMATS = {
    "feature": True,
    "short": False,
    "documentary": None,  # not itemised as its own category in the 2026 guidelines
    "tv_series": True,
    "animation": True,  # only for episodic animation averaging 20+ minutes; see format_notes
    "unscripted": None,
}

_FORMAT_NOTES = (
    "California Film Commission Program 4.0 Guidelines (effective Jan 2026) name "
    "eligible project types explicitly: TV series/pilot/limited series, "
    "independent and non-independent feature films, relocating TV series, "
    "large-scale competition shows, and animated/live-action TV with episodes "
    "averaging 20+ minutes. Short film is not a named category. This is separate "
    "from, and in addition to, the programme's USD 1,000,000 qualifying-spend "
    "floor."
)
_SOURCE_URL = "https://cdn.film.ca.gov/"
_VERIFIED_AT = "2026-08-14"


def _existing_columns(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _existing_columns(bind)
    required = {"eligible_formats", "format_notes", "format_source_url", "format_verified_at"}
    if not required.issubset(present):
        # Columns land via n4o5p6q7r8s9 / m3n4o5p6q7r8; if a database has not run
        # those yet, skip rather than fail — this migration only ever writes into
        # columns another migration owns creating.
        return

    bind.execute(
        sa.text(
            f"""
            UPDATE {_TABLE}
            SET eligible_formats = :eligible_formats,
                format_notes = :format_notes,
                format_source_url = :source_url,
                format_verified_at = :verified_at
            WHERE territory = :territory AND program = :program
            """
        ),
        {
            "eligible_formats": json.dumps(_ELIGIBLE_FORMATS),
            "format_notes": _FORMAT_NOTES,
            "source_url": _SOURCE_URL,
            "verified_at": _VERIFIED_AT,
            "territory": _TERRITORY,
            "program": _PROGRAM,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    present = _existing_columns(bind)
    if "eligible_formats" not in present:
        return
    bind.execute(
        sa.text(
            f"""
            UPDATE {_TABLE}
            SET eligible_formats = NULL,
                format_notes = NULL,
                format_source_url = NULL,
                format_verified_at = NULL
            WHERE territory = :territory AND program = :program
            """
        ),
        {"territory": _TERRITORY, "program": _PROGRAM},
    )
