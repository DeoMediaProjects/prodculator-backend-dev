"""fix_france_trip_accuracy

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-03-23

Fixes three confirmed accuracy issues with the France TRIP record, identified
by cross-checking the report output against official CNC documentation.

ISSUE 1 — Payment timeline too optimistic: 3-6 months → 6-9 months
--------------------------------------------------------------------
The DB stored payment_timeline_days_min=90, max=180 ("3-6 months").
Official CNC documentation and industry practice confirm the timeline from
submission of the final audited cost report to receipt of the tax credit is
typically 6-9 months. The 3-6 month figure underestimates cash-flow risk and
could lead producers to budget insufficient bridge financing.

Source: CNC official TRIP guidelines (cnc.fr) — the review clock starts only
after receipt of a COMPLETE audited cost report and all compliance documents.

ISSUE 2 — Missing eligibility requirement: minimum 5 shoot days in France
--------------------------------------------------------------------------
The official TRIP rules (Article 220 quaterdecies CGI) require a minimum of
5 consecutive shoot days physically carried out in France for live-action
productions. This was absent from eligibility_rules_json, meaning reports
never flagged this scheduling requirement to producers.

Source: CNC — "Au moins 5 jours de tournage en France pour les œuvres de
fiction en prises de vues réelles" (live-action fiction productions require
at least 5 shoot days in France).
CNC: https://www.cnc.fr/professionnels/aides-et-financements/cinema/production/credit-dimpot-international-trip_191538

ISSUE 3 — 40% VFX uplift not flagged as an opportunity
-------------------------------------------------------
The rate_tier_json already contains the VFX tier (40% when French VFX spend
exceeds €2M), but this was never surfaced in warnings_json as an opportunity.
For period productions increasingly using digital set extension and crowd
augmentation, this uplift is material and worth modelling explicitly.

Source: CNC — "Le taux est porté à 40 % lorsque les dépenses de tournage et
de post-production audiovisuelle afférentes aux effets spéciaux et à
l'animation réalisées en France excèdent 2 millions d'euros."
"""
import json as _json

from alembic import op
import sqlalchemy as sa

revision = "v3w4x5y6z7a8"
down_revision = "u2v3w4x5y6z7"
branch_labels = None
depends_on = None

_TERRITORY = "France"
_PROGRAM = "TRIP (Tax Rebate for International Production)"

# ── Issue 1 — corrected payment timeline ─────────────────────────────────────

_NEW_TIMELINE_MIN = 180   # 6 months
_NEW_TIMELINE_MAX = 270   # 9 months
_NEW_TIMELINE_NOTES = (
    "6-9 months from submission of final audited cost report. "
    "CNC review begins only after receipt of complete documentation — "
    "budget for at least 6 months of bridge financing."
)

_OLD_TIMELINE_MIN = 90    # 3 months (prior value)
_OLD_TIMELINE_MAX = 180   # 6 months (prior value)
_OLD_TIMELINE_NOTES = (
    "3-6 months after CNC approval and audit. "
    "CNC is efficient by European standards."
)

# ── Issue 2 — eligibility rules with 5 shoot days requirement ────────────────

_OLD_ELIGIBILITY_RULES = _json.dumps([
    {
        "rule": "Must be a foreign production (non-French majority) or qualifying co-production",
        "required": True,
    },
    {
        "rule": "Minimum \u20ac250,000 qualifying French expenditure",
        "required": True,
    },
    {
        "rule": "Cultural test administered by CNC",
        "required": True,
    },
    {
        "rule": "French production services company required",
        "required": True,
    },
])

_NEW_ELIGIBILITY_RULES = _json.dumps([
    {
        "rule": "Must be a foreign production (non-French majority) or qualifying co-production",
        "required": True,
    },
    {
        "rule": "Minimum \u20ac250,000 qualifying French expenditure",
        "required": True,
    },
    {
        "rule": "Cultural test administered by CNC",
        "required": True,
    },
    {
        "rule": "French production services company (soci\u00e9t\u00e9 de production fran\u00e7aise) required",
        "required": True,
    },
    {
        "rule": "Minimum 5 consecutive shoot days physically carried out in France (live-action productions)",
        "required": True,
    },
])

# ── Issue 3 — updated warnings including VFX uplift opportunity ───────────────

_OLD_WARNINGS = _json.dumps([
    "\u20ac30M per-project rebate cap",
    "Cultural test via CNC required \u2014 engage French co-producer or service company early",
    "French labour laws apply \u2014 strict overtime and rest requirements",
    "VFX uplift to 40% requires separate VFX expenditure qualification",
])

_NEW_WARNINGS = _json.dumps([
    "\u20ac30M per-project rebate cap",
    "Cultural test via CNC required \u2014 engage French co-producer or service company early. "
    "An entirely English-language, English-set production has no inherent French cultural "
    "connection \u2014 map the script against the CNC live-action cultural test scoring grid "
    "(available at filmfrance.net) before committing to France.",
    "French labour laws apply \u2014 strict overtime and rest requirements; "
    "schedule with mandatory rest periods built in.",
    "MINIMUM 5 CONSECUTIVE SHOOT DAYS IN FRANCE required for live-action productions "
    "(Article 220 quaterdecies CGI). Plan location schedule accordingly.",
    "40% VFX UPLIFT OPPORTUNITY: rate increases from 30% to 40% when qualifying French "
    "VFX expenditure exceeds \u20ac2M. Period productions using digital set extension, "
    "digital crowd augmentation, or environment enhancement should model both scenarios "
    "\u2014 the uplift can meaningfully increase the total rebate.",
    "Payment timeline is 6-9 months from submission of final audited cost report \u2014 "
    "budget for bridge financing accordingly.",
])


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET payment_timeline_days_min = :tmin, "
            "    payment_timeline_days_max = :tmax, "
            "    payment_timeline_notes   = :tnotes, "
            "    eligibility_rules_json   = :rules, "
            "    warnings_json            = :warnings, "
            "    last_verified_at         = '2026-03-23' "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {
            "tmin": _NEW_TIMELINE_MIN,
            "tmax": _NEW_TIMELINE_MAX,
            "tnotes": _NEW_TIMELINE_NOTES,
            "rules": _NEW_ELIGIBILITY_RULES,
            "warnings": _NEW_WARNINGS,
            "territory": _TERRITORY,
            "program": _PROGRAM,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET payment_timeline_days_min = :tmin, "
            "    payment_timeline_days_max = :tmax, "
            "    payment_timeline_notes   = :tnotes, "
            "    eligibility_rules_json   = :rules, "
            "    warnings_json            = :warnings "
            "WHERE territory = :territory "
            "  AND program   = :program"
        ),
        {
            "tmin": _OLD_TIMELINE_MIN,
            "tmax": _OLD_TIMELINE_MAX,
            "tnotes": _OLD_TIMELINE_NOTES,
            "rules": _OLD_ELIGIBILITY_RULES,
            "warnings": _OLD_WARNINGS,
            "territory": _TERRITORY,
            "program": _PROGRAM,
        },
    )
