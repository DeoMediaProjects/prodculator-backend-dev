"""resolve_quebec_qpstc_and_alberta_afttc

Revision ID: t0u1v2w3x4y5
Revises: z6a7b8c9d0e1
Create Date: 2026-08-17

SEQUENCING (moved 2026-08-19)
----------------------------
Originally revised s9t0u1v2w3x4, which was the live head. It was never committed
and never applied, so it sat between production and the cap-constraint fixes in
u1v2w3x4y5z6 / v2w3x4y5z6a7 and blocked them: the deploy runs `git pull` then
`alembic upgrade head`, and an absent revision makes alembic fail to build the
revision map at all (KeyError), so nothing shipped.

It revises z6a7b8c9d0e1. Applied to production on 22 August 2026, so this position is now fixed and must not move: anything inserted before it would be treated as already applied and silently skipped. The two sets are independent — this
one touches Quebec and Alberta only, those touch the UK, Belgium, Netherlands,
Mexico and British Columbia, and neither reads a column the other adds — so the
order carries no data dependency.

Do NOT move this back ahead of them without checking whether it has been
committed. It raises Quebec QPSTC 20% -> 25% and widens its qualifying basis from
labour to total spend, both of which increase reported Quebec rebates, so it
needs sign-off before it reaches production.

Resolves the two open structural flags left on the Canadian provincial records
by the v4 refresh (ab2c3d4e5f61). Both rows were seeded with a flat rate plus a
"do not treat this record as reliable for report generation until reconciled"
warning; both are now reconciled against the provincial programme structures.

ROOT CAUSE 1 — Quebec QPSTC: base rate 20% → 25%, and qualifying spend basis
-----------------------------------------------------------------------------
The refresh seeded rate_gross/rate_net = 20 on the strength of a CMPA figure,
and recorded the disagreement it could not settle in its own warnings_json:

    "One source (non-CMPA) states 25% instead of 20% — CMPA (industry
     association) figure used as more authoritative, but worth a direct
     confirmation call"

That confirmation resolves in favour of 25%: SODEC's production services credit
is a 25% refundable credit on all eligible Quebec spend, with a further 16%
labour bonus for computer-aided special effects and animation. This migration
therefore supersedes the CMPA-sourced 20% and clears the open question.

Two consequences beyond the headline rate:

  * qualifying_spend_type was 'labour', which contradicted the refresh's own
    note ("20% on all-spend production costs (labour + qualified property)").
    The credit applies to labour AND goods/services, so the basis is 'total'.
    This is the field that actually changes calculated rebate values — a
    labour-only basis understates the qualifying pool for a service production.
  * qualifying_spend_min was NULL ('None' in the v4 source). The programme
    carries a CAD 250,000 minimum eligible spend.

The 16% CASE/animation bonus was already documented in warnings_json but was
never represented as a tier or in vfx_uplift_pct, so no consumer of the record
could see it numerically. Both are now populated.

ROOT CAUSE 2 — Alberta AFTTC: the 8% uplift is rural, not ownership-tiered
---------------------------------------------------------------------------
The refresh recorded two irreconcilable candidate structures:

    (a) 22% base + 8% diversity bonus, or
    (b) tiered by Alberta ownership: 25%/26% for <50% Alberta-owned,
        29%/30% for >50% Alberta-owned

Candidate (a) is correct in its numbers and wrong in its label: the 8% uplift
is a RURAL uplift, earned when at least 75% of principal photography takes
place outside the Calgary and Edmonton zones — not a diversity bonus and not a
function of Alberta ownership. Candidate (b) is discarded. The base stays 22%,
reaching 30% with the uplift, on a CAD 500,000 minimum (already correct).

net_rate_pct is kept in step with rate_net on both rows — i9d0e1f2a3b4
established that the two must not diverge, since ReportBuilder reads
net_rate_pct straight into the report skeleton.

Neither row's is_supplementary flag is touched. Per h8c9d0e1f2a3, Quebec and
Alberta each have exactly one programme, and flagging it supplementary would
drop the territory from every report.

Last Verified: 2026-08-17
"""
from __future__ import annotations

import json as _json

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "t0u1v2w3x4y5"
down_revision = "z6a7b8c9d0e1"
branch_labels = None
depends_on = None


# ── 1. Quebec QPSTC ─────────────────────────────────────────────────────────

_QC_NEW_RATE = (
    "25% refundable credit on all eligible Quebec spend (labour + "
    "goods/services); additional 16% on labour for computer-aided special "
    "effects and animation"
)
_QC_OLD_RATE_GROSS = 20.0
_QC_NEW_RATE_GROSS = 25.0

_QC_NEW_TIER_JSON = _json.dumps([
    {
        "label": (
            "Base tier: 25% on all eligible Quebec production spend "
            "(labour and goods/services), minimum CAD 250,000 eligible spend"
        ),
        "rate_gross": 25,
        "tier_type": "informational",
    },
    {
        "label": (
            "CASE/animation bonus: additional 16% on Quebec labour for "
            "computer-aided special effects and animation — applies to the "
            "qualifying labour portion only, not the whole spend"
        ),
        "rate_gross": 16,
        "tier_type": "informational",
    },
])

_QC_NEW_WARNINGS = _json.dumps([
    "French-language productions may qualify for enhanced rates — verify with SODEC",
    "Supplementary to federal PSTC",
    (
        "Base 25% applies to all eligible Quebec spend (labour + goods/services). "
        "The additional 16% CASE/animation bonus applies to qualifying LABOUR only — "
        "do not apply 41% to a whole budget."
    ),
    "Minimum CAD 250,000 eligible Quebec spend",
])

_QC_OLD_WARNINGS = _json.dumps([
    "French-language productions may qualify for enhanced rates — verify with SODEC",
    "Supplementary to federal PSTC",
    "VERIFY FIRST",
    (
        "STRUCTURAL ISSUE: programme name and/or rate may not correctly match the "
        "production-services track relevant to international productions — see notes. "
        "Do not treat this record as reliable for report generation until reconciled."
    ),
    (
        "Additional 16% on labour for VFX/computer animation and chroma-key work "
        "— confirmed by CMPA"
    ),
    (
        "One source (non-CMPA) states 25% instead of 20% — CMPA (industry association) "
        "figure used as more authoritative, but worth a direct confirmation call"
    ),
])

_QC_NEW_NOTES = (
    "6–12 months from SODEC assessment. [RESOLVED 2026-08-17: base rate "
    "confirmed at 25% on all eligible Quebec spend (labour + goods/services), "
    "superseding the 20% CMPA figure the v4 refresh had flagged as needing a "
    "direct confirmation call. Additional 16% labour bonus for computer-aided "
    "special effects and animation. Minimum CAD 250,000 eligible spend. "
    "qualifying_spend_type corrected from 'labour' to 'total' to match the "
    "all-spend basis.] [RENAMED 2026-07: was labelled 'Quebec SODEC Film Tax "
    "Credit' (ambiguous — SODEC administers multiple Quebec credits). Confirmed "
    "as QPSTC, for foreign/service productions. Separate Quebec Film and TV "
    "Production credit (Canadian-controlled, ~40% on labour) is NOT this record.]"
)

_QC_OLD_NOTES = (
    "6–12 months from SODEC assessment. [STRUCTURAL FLAG 2026-07: Record titled "
    "'Quebec SODEC Film Tax Credit' shows 20% — this matches Quebec's Production "
    "Services Tax Credit (for foreign productions, ~20% + 16% VFX uplift), not "
    "the Quebec Film and Television Tax Credit for Quebec-controlled productions "
    "(~33%). Programme name and rate appear mismatched.] [RENAMED 2026-07: was "
    "labelled 'Quebec SODEC Film Tax Credit' (ambiguous — SODEC administers "
    "multiple Quebec credits). Confirmed via CMPA as QPSTC, 20% on all-spend "
    "production costs (labour + qualified property), for foreign/service "
    "productions. Separate Quebec Film and TV Production credit "
    "(Canadian-controlled, ~40% on labour) is NOT this record.]"
)


# ── 2. Alberta AFTTC ────────────────────────────────────────────────────────

_AB_NEW_RATE = (
    "22% refundable credit on eligible Alberta spend; 30% with the 8% rural "
    "uplift (≥75% of principal photography outside the Calgary and Edmonton "
    "zones)"
)

_AB_NEW_TIER_JSON = _json.dumps([
    {
        "label": (
            "Base tier: 22% on eligible Alberta production spend, "
            "minimum CAD 500,000"
        ),
        "rate_gross": 22,
        "tier_type": "informational",
    },
    {
        "label": (
            "Rural uplift: 30% total (22% base + 8%) where at least 75% of "
            "principal photography takes place outside the Calgary and "
            "Edmonton zones"
        ),
        "rate_gross": 30,
        "tier_type": "informational",
    },
])

_AB_NEW_WARNINGS = _json.dumps([
    (
        "Primarily attractive for landscape/location shoots — crew depth thinner "
        "than BC/Ontario"
    ),
    "VERIFY FIRST — combine with federal PSTC",
    "Minimum CAD 500K qualifying Alberta spend",
    (
        "RURAL UPLIFT: the 8% uplift taking the credit to 30% requires ≥75% of "
        "principal photography outside the Calgary and Edmonton zones. A "
        "city-based shoot earns the 22% base only — do not budget 30% by default."
    ),
])

_AB_OLD_WARNINGS = _json.dumps([
    (
        "Primarily attractive for landscape/location shoots — crew depth thinner "
        "than BC/Ontario"
    ),
    "VERIFY FIRST — combine with federal PSTC",
    "Minimum CAD 500K qualifying Alberta spend",
    (
        "STRUCTURAL ISSUE: programme name and/or rate may not correctly match the "
        "production-services track relevant to international productions — see notes. "
        "Do not treat this record as reliable for report generation until reconciled."
    ),
])

_AB_NEW_NOTES = (
    "6–12 months from Alberta Culture assessment. [RESOLVED 2026-08-17: of the "
    "two structures the v4 refresh could not reconcile, candidate (a) is "
    "correct in its numbers but was mislabelled — the 8% uplift is a RURAL "
    "uplift, earned when ≥75% of principal photography takes place outside the "
    "Calgary and Edmonton zones, not a diversity bonus and not a function of "
    "Alberta company ownership. Base 22%, reaching 30% with the uplift, on a "
    "CAD 500,000 minimum. Candidate (b) — the 25%/26% and 29%/30% "
    "ownership-tiered structure — is discarded.]"
)

_AB_OLD_NOTES = (
    "6–12 months from Alberta Culture assessment. [STRUCTURAL FLAG 2026-07: "
    "Record shows flat 22% — real structure is tiered by Alberta company "
    "ownership: 25% (26% if 30+ shoot days) for <50% Alberta-owned, rising to "
    "29% (30% if 30+ shoot days) for >50% Alberta-owned, with different minimum "
    "department-head requirements. Flat 22% does not match either tier found in "
    "fresh research — needs reconciliation against Alberta Film Commission "
    "directly.] [UNRESOLVED 2026-07: two conflicting structures found in fresh "
    "research — (a) 22% base +8% diversity bonus, or (b) tiered by Alberta "
    "ownership: 25%/26% for <50% Alberta-owned, 29%/30% for >50% Alberta-owned, "
    "with different shoot-day and department-head thresholds. Could not "
    "reconcile with confidence — needs a direct check against the Alberta Film "
    "Commission before this record is used for report generation.]"
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Quebec — 25% base, all-spend basis, CAD 250K floor, 16% CASE ──────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate                      = :rate,
            rate_gross                = :rate_gross,
            rate_net                  = :rate_gross,
            net_rate_pct              = :rate_gross,
            rate_gross_display        = '25%',
            rate_net_display          = '25%',
            vfx_uplift_pct            = 16,
            qualifying_spend_type     = 'total',
            qualifying_spend_min      = 250000,
            qualifying_spend_currency = 'CAD',
            rate_tier_json            = :tiers,
            warnings_json             = :warnings,
            notes                     = :notes,
            last_verified_at          = '2026-08-17'
        WHERE territory = 'Quebec'
          AND program   = 'Quebec Production Services Tax Credit (QPSTC)'
    """), {
        "rate": _QC_NEW_RATE,
        "rate_gross": _QC_NEW_RATE_GROSS,
        "tiers": _QC_NEW_TIER_JSON,
        "warnings": _QC_NEW_WARNINGS,
        "notes": _QC_NEW_NOTES,
    })

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Quebec' "
            "AND rate_gross = 25 "
            "AND qualifying_spend_type = 'total' "
            "AND notes LIKE '%RESOLVED 2026-08-17%'"
        ),
        expected_min=1,
        migration_id=revision,
    )

    # ── 2. Alberta — 22% base + 8% rural uplift = 30% ────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate                      = :rate,
            qualifying_spend_min      = 500000,
            qualifying_spend_currency = 'CAD',
            rate_tier_json            = :tiers,
            warnings_json             = :warnings,
            notes                     = :notes,
            last_verified_at          = '2026-08-17'
        WHERE territory = 'Alberta'
          AND program   = 'Alberta Film and Television Tax Credit (AFTTC)'
    """), {
        "rate": _AB_NEW_RATE,
        "tiers": _AB_NEW_TIER_JSON,
        "warnings": _AB_NEW_WARNINGS,
        "notes": _AB_NEW_NOTES,
    })

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Alberta' "
            "AND rate LIKE '%rural uplift%' "
            "AND notes LIKE '%RESOLVED 2026-08-17%'"
        ),
        expected_min=1,
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Restore Quebec: 20% CMPA figure, labour basis, no floor, no uplift value
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate                      = '20%',
            rate_gross                = :rate_gross,
            rate_net                  = :rate_gross,
            net_rate_pct              = :rate_gross,
            rate_gross_display        = '20%',
            rate_net_display          = '20%',
            vfx_uplift_pct            = NULL,
            qualifying_spend_type     = 'labour',
            qualifying_spend_min      = NULL,
            qualifying_spend_currency = NULL,
            rate_tier_json            = NULL,
            warnings_json             = :warnings,
            notes                     = :notes
        WHERE territory = 'Quebec'
          AND program   = 'Quebec Production Services Tax Credit (QPSTC)'
    """), {
        "rate_gross": _QC_OLD_RATE_GROSS,
        "warnings": _QC_OLD_WARNINGS,
        "notes": _QC_OLD_NOTES,
    })

    # Restore Alberta: flat 22%, unresolved flags, no tiers. The CAD 500,000
    # floor pre-dates this migration (parsed from the v4 qsMin "CAD 500,000"),
    # so it is preserved rather than nulled.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rate                      = '22%',
            qualifying_spend_min      = 500000,
            qualifying_spend_currency = 'CAD',
            rate_tier_json            = NULL,
            warnings_json             = :warnings,
            notes                     = :notes
        WHERE territory = 'Alberta'
          AND program   = 'Alberta Film and Television Tax Credit (AFTTC)'
    """), {
        "warnings": _AB_OLD_WARNINGS,
        "notes": _AB_OLD_NOTES,
    })
