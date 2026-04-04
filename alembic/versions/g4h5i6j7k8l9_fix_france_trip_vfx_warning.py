"""fix_france_trip_vfx_warning

Revision ID: g4h5i6j7k8l9
Revises: f3g4h5i6j7k8
Create Date: 2026-03-28

ROOT CAUSE
----------
f3g4h5i6j7k8 attempted to fix the France TRIP VFX uplift warning using SQL
REPLACE() on the warnings_json text.  The em-dash character (U+2014) in the
target string produced a silent no-op — the substring did not match exactly.

This migration replaces the entire warnings_json for France TRIP unconditionally,
using the known authoritative list.  The corrected VFX uplift warning explains
that the 40% rate applies to ALL qualifying French expenditure once the VFX
threshold is met (not just the VFX portion).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g4h5i6j7k8l9"
down_revision = "f3g4h5i6j7k8"
branch_labels = None
depends_on = None

_FRANCE_TRIP = "TRIP (Tax Rebate for International Production)"

# Authoritative warnings_json for France TRIP after all corrections.
_WARNINGS_CORRECT = (
    '[\u20ac30M per-project rebate cap", '
    '"Cultural test via CNC required \u2014 engage French co-producer or service '
    'company early. An entirely English-language, English-set production has no '
    'inherent French cultural connection \u2014 map the script against the CNC '
    'live-action cultural test scoring grid (available at filmfrance.net) before '
    'committing to France.", '
    '"French labour laws apply \u2014 strict overtime and rest requirements; '
    'schedule with mandatory rest periods built in.", '
    '"MINIMUM 5 CONSECUTIVE SHOOT DAYS IN FRANCE required for live-action '
    'productions (Article 220 quaterdecies CGI). Plan location schedule '
    'accordingly.", '
    '"40% VFX UPLIFT OPPORTUNITY: when qualifying French VFX spend exceeds \u20ac2M '
    '(AND meets shot ratio requirements), the 40% rate applies to ALL qualifying '
    'French expenditure \u2014 not just the VFX portion. For a \u20ac5M French spend this '
    'is the difference between \u20ac1.5M (30%) and \u20ac2.0M (40%) rebate. Always model '
    'both scenarios for VFX-heavy or period productions with digital environments.", '
    '"Payment timeline is 6-9 months from submission of final audited cost report '
    '\u2014 budget for bridge financing accordingly."]'
)

# Use a hardcoded JSON string to avoid any Python string-escaping issues.
# This is exactly what the DB contains after the direct-SQL fix applied in
# the same session as f3g4h5i6j7k8.
_WARNINGS_JSON = (
    '["\u20ac30M per-project rebate cap",'
    '"Cultural test via CNC required \u2014 engage French co-producer or service company early. An entirely English-language, English-set production has no inherent French cultural connection \u2014 map the script against the CNC live-action cultural test scoring grid (available at filmfrance.net) before committing to France.",'
    '"French labour laws apply \u2014 strict overtime and rest requirements; schedule with mandatory rest periods built in.",'
    '"MINIMUM 5 CONSECUTIVE SHOOT DAYS IN FRANCE required for live-action productions (Article 220 quaterdecies CGI). Plan location schedule accordingly.",'
    '"40% VFX UPLIFT OPPORTUNITY: when qualifying French VFX spend exceeds \u20ac2M (AND meets shot ratio requirements), the 40% rate applies to ALL qualifying French expenditure \u2014 not just the VFX portion. For a \u20ac5M French spend this is the difference between \u20ac1.5M (30%) and \u20ac2.0M (40%) rebate. Always model both scenarios for VFX-heavy or period productions with digital environments.",'
    '"Payment timeline is 6-9 months from submission of final audited cost report \u2014 budget for bridge financing accordingly."]'
)

_WARNINGS_JSON_OLD = (
    '["\u20ac30M per-project rebate cap",'
    '"Cultural test via CNC required \u2014 engage French co-producer or service company early. An entirely English-language, English-set production has no inherent French cultural connection \u2014 map the script against the CNC live-action cultural test scoring grid (available at filmfrance.net) before committing to France.",'
    '"French labour laws apply \u2014 strict overtime and rest requirements; schedule with mandatory rest periods built in.",'
    '"MINIMUM 5 CONSECUTIVE SHOOT DAYS IN FRANCE required for live-action productions (Article 220 quaterdecies CGI). Plan location schedule accordingly.",'
    '"40% VFX UPLIFT OPPORTUNITY: rate increases from 30% to 40% when qualifying French VFX expenditure exceeds \u20ac2M. Period productions using digital set extension, digital crowd augmentation, or environment enhancement should model both scenarios \u2014 the uplift can meaningfully increase the total rebate.",'
    '"Payment timeline is 6-9 months from submission of final audited cost report \u2014 budget for bridge financing accordingly."]'
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json    = :wj,
            last_verified_at = '2026-03-28'
        WHERE territory = 'France'
          AND program   = :program
    """), {"wj": _WARNINGS_JSON, "program": _FRANCE_TRIP})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET warnings_json = :wj
        WHERE territory = 'France'
          AND program   = :program
    """), {"wj": _WARNINGS_JSON_OLD, "program": _FRANCE_TRIP})
