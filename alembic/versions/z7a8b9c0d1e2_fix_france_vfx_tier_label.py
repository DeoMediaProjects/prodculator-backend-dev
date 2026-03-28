"""fix_france_vfx_tier_label

Revision ID: z7a8b9c0d1e2
Revises: y6z7a8b9c0d1
Create Date: 2026-03-23

Migration x5y6z7a8b9c0 added the VFX shot-percentage condition to the
France TRIP rate_tier_json label, but included "€2M" in the text. The
rebate calculator's rate-tier logic (validator.py line 395) uses a regex
to detect monetary amounts in tier labels — [£$€](\d+)\s*[mM] — and
treats them as spend-boundary thresholds for blended rate calculation.

The "€2M" in the VFX tier label triggered this regex, causing the
calculator to treat the €2M as a boundary between two rate tiers
(30% below €2M, 40% above), producing a blended rate of ~38.66%
instead of the correct headline 30%.

The France TRIP VFX tiers are INFORMATIONAL (the 40% rate applies only
to VFX-specific expenditure when certain conditions are met), NOT
spend-boundary tiers. The label must not contain a parseable monetary
amount.

This migration rewrites the label to spell out "2 million EUR" instead
of "€2M" so the regex does not match and the headline 30% rate is
preserved.
"""
import json as _json

from alembic import op
import sqlalchemy as sa

revision = "z7a8b9c0d1e2"
down_revision = "y6z7a8b9c0d1"
branch_labels = None
depends_on = None

_TERRITORY = "France"
_PROGRAM = "TRIP (Tax Rebate for International Production)"

_FIXED_TIER = _json.dumps([
    {"label": "Standard qualifying spend", "rate_gross": 30},
    {
        "label": (
            "VFX expenditure (requires: French VFX spend exceeding 2 million EUR AND "
            "at least 15% of shots digitally processed or at least 1.5 digitally "
            "processed shots per minute of finished work)"
        ),
        "rate_gross": 40,
    },
])


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate_tier_json = :tiers "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {"tiers": _FIXED_TIER, "territory": _TERRITORY, "program": _PROGRAM},
    )


def downgrade() -> None:
    # No-op: the previous state was the buggy €2M label from x5y6z7a8b9c0.
    # Reverting would reintroduce the blended-rate bug.
    pass
