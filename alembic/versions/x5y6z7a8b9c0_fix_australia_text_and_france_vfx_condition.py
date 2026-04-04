"""fix_australia_text_and_france_vfx_condition

Revision ID: x5y6z7a8b9c0
Revises: w4x5y6z7a8b9
Create Date: 2026-03-23

Fixes three confirmed data accuracy issues identified by cross-checking the
second generated report against official government sources.

ISSUE 1 — Australia Location Offset: QAPE minimum text says A$15M, should be A$20M
-------------------------------------------------------------------------------------
Migration d5e6f7g8h9i0 correctly updated qualifying_spend_min to 20,000,000
but the human-readable text in eligibility_rules_json and warnings_json was
never updated and still says "AUD 15M" / "A$15M". This causes the report to
display conflicting information: the Qualifying Spend field shows A$20M (from
the numeric column) while the requirements text says A$15M.

Source: Screen Australia — the threshold was raised to A$20M when the Location
Offset rate increased to 30% (effective 1 July 2023).
https://www.screenaustralia.gov.au/funding-and-support/producer-offset/location-offset

ISSUE 2 — Australia Location Offset: "provisional certificate" terminology is wrong
------------------------------------------------------------------------------------
The eligibility_rules_json and payment_timeline_notes refer to a "provisional
certificate from the Australian Minister for the Arts" required 6-8 weeks
before photography. This is incorrect.

Per ATO and Screen Australia official documentation:
- The Location Offset requires a FINAL CERTIFICATE from the Minister for the
  Arts, which can only be applied for AFTER the production has ceased incurring
  qualifying Australian production expenditure (i.e. after production wraps).
- There is no "provisional certificate" step before photography for the
  Location Offset (that concept applies to the Producer Offset's provisional
  certificate, which is a different programme).
- What a producer needs pre-shoot is to ensure their production structure
  meets the eligibility criteria and that QAPE will exceed the A$20M threshold.

Source: ATO — https://www.ato.gov.au/businesses-and-organisations/income-deductions-offsets-and-records/offsets-and-rebates/film-offsets

ISSUE 3 — France TRIP: VFX uplift shot-percentage condition missing
-------------------------------------------------------------------
The 40% VFX uplift rate is correctly stored in rate_tier_json and mentioned
in warnings_json, but the qualifying condition is incomplete. The official
CNC rules require BOTH:
  a) French VFX expenditure exceeds €2M, AND
  b) At least 15% of the production's shots are digitally processed, OR
     an average of at least 1.5 digitally processed shots per minute of
     the finished work.

This shot-percentage threshold was not documented in the DB, causing the
report to present the VFX uplift as triggered solely by spend — which could
lead a producer to budget for €2M in French VFX spend without verifying
whether the production meets the shot-processing threshold.

Source: CNC — Article 220 quaterdecies CGI and CNC TRIP guidelines.
"""
import json as _json

from alembic import op
import sqlalchemy as sa

revision = "x5y6z7a8b9c0"
down_revision = "w4x5y6z7a8b9"
branch_labels = None
depends_on = None


# ── Issue 1 & 2: Australia Location Offset ────────────────────────────────────

_AU_TERRITORY = "Australia"
_AU_PROGRAM = "Location Offset (Foreign Productions)"

_AU_NEW_ELIGIBILITY_RULES = _json.dumps([
    {
        "rule": "Minimum AUD 20M qualifying Australian production expenditure (QAPE)",
        "required": True,
    },
    {
        "rule": (
            "Final Certificate from the Australian Minister for the Arts — "
            "applied for AFTER the production has ceased incurring QAPE "
            "(i.e. after production wraps, not before photography)"
        ),
        "required": True,
    },
    {
        "rule": "Must be a foreign-owned production (Australian productions use Producer Offset)",
        "required": True,
    },
])

_AU_OLD_ELIGIBILITY_RULES = (
    '[{"rule":"Minimum AUD 15M qualifying Australian production expenditure (QAPE)","required":true},'
    '{"rule":"Provisional certificate from Australian Minister for the Arts","required":true},'
    '{"rule":"Must be a foreign-owned production (Australian productions use Producer Offset)","required":true}]'
)

_AU_NEW_WARNINGS = _json.dumps([
    (
        "AUD 20M minimum QAPE threshold \u2014 your total qualifying Australian "
        "production expenditure must exceed A$20M. Mid-budget productions "
        "should verify allocated Australian spend meets this threshold early."
    ),
    (
        "Final Certificate from the Minister for the Arts is applied for AFTER "
        "production wraps (not before photography). Ensure your production "
        "structure and QAPE strategy are confirmed pre-shoot \u2014 the certificate "
        "process itself occurs post-production."
    ),
    "ATO assessment timeline can extend to 9 months after Final Certificate lodgement.",
])

_AU_OLD_WARNINGS = (
    '["AUD 15M minimum QAPE threshold \u2014 mid-budget productions may not qualify",'
    '"Provisional certificate from Minister required \u2014 apply 8+ weeks before photography",'
    '"ATO assessment timeline can extend to 9 months"]'
)

_AU_NEW_TIMELINE_NOTES = (
    "4-9 months after ATO assessment of the Final Certificate. "
    "The Final Certificate is applied for after production wraps "
    "(after QAPE ceases), not before photography."
)

_AU_OLD_TIMELINE_NOTES = (
    "4-9 months after ATO assessment. "
    "Provisional certificate from Arts Minister required first (6-8 weeks)."
)


# ── Issue 3: France TRIP VFX uplift condition ─────────────────────────────────

_FR_TERRITORY = "France"
_FR_PROGRAM = "TRIP (Tax Rebate for International Production)"

# Update rate_tier_json to include the shot-percentage condition in the label
_FR_NEW_RATE_TIER = _json.dumps([
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

_FR_OLD_RATE_TIER = _json.dumps([
    {"label": "Standard qualifying spend", "rate_gross": 30},
    {"label": "VFX expenditure", "rate_gross": 40},
])


def upgrade() -> None:
    conn = op.get_bind()

    # ── Issues 1 & 2: Australia Location Offset text corrections ──────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET eligibility_rules_json = :rules, "
            "    warnings_json          = :warnings, "
            "    payment_timeline_notes  = :timeline, "
            "    last_verified_at        = '2026-03-23' "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {
            "rules": _AU_NEW_ELIGIBILITY_RULES,
            "warnings": _AU_NEW_WARNINGS,
            "timeline": _AU_NEW_TIMELINE_NOTES,
            "territory": _AU_TERRITORY,
            "program": _AU_PROGRAM,
        },
    )

    # ── Issue 3: France TRIP VFX uplift condition ─────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate_tier_json   = :tiers, "
            "    last_verified_at = '2026-03-23' "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {
            "tiers": _FR_NEW_RATE_TIER,
            "territory": _FR_TERRITORY,
            "program": _FR_PROGRAM,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Restore Australia
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET eligibility_rules_json = :rules, "
            "    warnings_json          = :warnings, "
            "    payment_timeline_notes  = :timeline "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {
            "rules": _AU_OLD_ELIGIBILITY_RULES,
            "warnings": _AU_OLD_WARNINGS,
            "timeline": _AU_OLD_TIMELINE_NOTES,
            "territory": _AU_TERRITORY,
            "program": _AU_PROGRAM,
        },
    )

    # Restore France TRIP tiers
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate_tier_json = :tiers "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {
            "tiers": _FR_OLD_RATE_TIER,
            "territory": _FR_TERRITORY,
            "program": _FR_PROGRAM,
        },
    )
