"""fix_australia_producer_offset_eligibility

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-03-23

ROOT CAUSE
----------
The Australia "Producer Offset" row has nationality_requirements = '["AU"]'
(set by g8h9i0j1k2l3) which correctly marks it as Australian-producers-only.
However, spv_eligible was never set to False on that row.

best_incentive() in helpers.py excludes a row as "domestic-corp-only" only
when BOTH conditions are true:
  • nationality_requirements is a non-empty list, AND
  • spv_eligible is explicitly False

Without spv_eligible = False the Producer Offset (40%) was not excluded and
beat the Location Offset (30%) in every report for Australia, producing three
downstream errors:

  1. Wrong programme shown (Producer Offset, domestic-only, vs Location Offset
     which is the correct programme for foreign productions)
  2. Wrong currency — Producer Offset has no currency field so the calculation
     fell back to GBP instead of AUD
  3. "Data not available" payment timeline — Producer Offset lacks
     payment_timeline_* fields; Location Offset has the correct 4-9 month value

FIX
---
Set spv_eligible = False on the Producer Offset row. No other changes needed:
  • Location Offset rate was already corrected to 30% in e6f7g8h9i0j1
  • Location Offset currency is already AUD
  • Location Offset payment_timeline is already populated

SOURCE
------
Screen Australia — https://www.screenaustralia.gov.au/funding-and-support/producer-offset
ATO — https://www.ato.gov.au/businesses-and-organisations/income-deductions-offsets-and-records/offsets-and-rebates/film-offsets
"""
from alembic import op
import sqlalchemy as sa

revision = "u2v3w4x5y6z7"
down_revision = "t1u2v3w4x5y6"
branch_labels = None
depends_on = None

_TERRITORY = "Australia"
_PROGRAM = "Producer Offset"


def upgrade() -> None:
    conn = op.get_bind()

    # Set spv_eligible = False so _is_domestic_corp_only() returns True and
    # best_incentive() excludes this row in favour of Location Offset.
    # No status filter: Producer Offset is uniquely identified by territory +
    # program.  Adding AND status = 'active' risks a silent no-op if the row
    # has status = '' (empty string), which the Python service filter accepts
    # but SQL '= 'active'' does not.
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET spv_eligible = FALSE, "
            "    last_verified_at = '2026-03-23' "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {"territory": _TERRITORY, "program": _PROGRAM},
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Restore spv_eligible to NULL (the unset state prior to this migration)
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET spv_eligible = NULL "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {"territory": _TERRITORY, "program": _PROGRAM},
    )
