"""fix_czech_cap_text_and_gmpf_format_eligibility

Revision ID: t2u3v4w5x6y7
Revises: s1t2u3v4w5x6
Create Date: 2026-03-29

ROOT CAUSE 1 — Czech Film Fund: cap TEXT field still shows "CZK 150M"
----------------------------------------------------------------------
Migration m4n5o6p7q8r9 updated cap_amount = 450,000,000 correctly, but
never updated the cap TEXT field. The builder reads cap TEXT before cap_amount
(same priority-chain bug as Spain fixed in r0s1t2u3v4w5). The seed value:

    "CZK 150M per applicant per year (~€6M)"

was never overwritten. The January 2025 Czech Audiovisual Fund reform tripled
the cap from CZK 150M to CZK 450M and changed the structure from
"per applicant per year" to "per project".

Source: Czech Audiovisual Fund Act (January 2025); The Prague Reporter

ROOT CAUSE 2 — GMPF eligibility_rules_json: "Minimum €8M" not updated
-----------------------------------------------------------------------
Migration p8q9r0s1t2u3 updated qualifying_spend_min to €13M and rewrote
warnings_json, but did NOT update eligibility_rules_json. The seed value:

    {"rule": "Minimum €8M qualifying German expenditure", "required": true}

was never overwritten (same NZ/Spain pattern: numeric column corrected but
human-readable text field left stale).

The requirements table in the tax incentive section reads eligibility_rules_json
directly, so it displayed "Minimum €8M" while qualifying_spend_min was €13M —
internal inconsistency visible in the report.

ROOT CAUSE 3 — GMPF has no theatrical feature eligibility restriction
---------------------------------------------------------------------
The GMPF (German Motion Picture Fund), post-January 2025 reform, primarily
targets international co-productions destined for streaming/VoD distribution
on international platforms. It is a competitive grant administered by the FFA.

Theatrical feature films should assess DFFF I (Deutscher Filmförderfonds I)
eligibility first. DFFF I is the automatic investment grant for qualifying
theatrical productions — 30% of German expenditure, up to €25M per film,
minimum €15M qualifying German spend. GMPF and DFFF I target different
distribution models; a production planning a theatrical release should confirm
with the FFA which programme applies before committing to either process.

No migration has ever added this distinction. eligibility_notes was NULL,
so the AI had no explicit format restriction to read — flagged in both
the Brooklyn Nick and The Duchess reviews as an unresolved issue.

Source: FFA (Filmförderungsanstalt) GMPF Guidelines 2026; DFFF I Guidelines 2025

Last Verified: 2026-03-29
"""
from __future__ import annotations

import json as _json

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "t2u3v4w5x6y7"
down_revision = "s1t2u3v4w5x6"
branch_labels = None
depends_on = None

# ── 1. Czech Film Fund — correct cap TEXT field ───────────────────────────────

_CZECH_NEW_CAP = "CZK 450M per project (~€18M)"
_CZECH_OLD_CAP = "CZK 150M per applicant per year (~€6M)"

# ── 2. GMPF — correct eligibility_rules_json (€8M → €13M) ───────────────────

_GMPF_NEW_RULES = _json.dumps([
    {
        "rule": (
            "Minimum €13M qualifying German expenditure "
            "(or 40% of total production budget, whichever is higher)"
        ),
        "required": True,
    },
    {
        "rule": "Must demonstrate significant cultural and economic contribution to Germany",
        "required": True,
    },
    {
        "rule": "German co-producer or service company required",
        "required": True,
    },
    {
        "rule": (
            "GMPF primarily targets streaming/VoD co-productions — "
            "theatrical feature films should verify DFFF I eligibility with the FFA first"
        ),
        "required": False,
    },
])

_GMPF_OLD_RULES = _json.dumps([
    {
        "rule": "Minimum €8M qualifying German expenditure",
        "required": True,
    },
    {
        "rule": "Must demonstrate significant cultural and economic contribution to Germany",
        "required": True,
    },
    {
        "rule": "German co-producer or service company required",
        "required": True,
    },
])

# ── 3. GMPF — add eligibility_notes for theatrical vs streaming format restriction

_GMPF_ELIGIBILITY_NOTES = (
    "GMPF (German Motion Picture Fund) primarily targets international co-productions "
    "destined for streaming and VoD distribution on international platforms. "
    "It is a competitive grant administered by the FFA — approval is required before principal photography. "
    "Theatrical feature films should assess DFFF I (Deutscher Filmförderfonds I) eligibility first: "
    "DFFF I is an automatic investment grant at 30% of qualifying German expenditure, "
    "capped at €25M per film, with a minimum €15M qualifying German spend requirement. "
    "GMPF and DFFF I serve different distribution models — confirm with the FFA which "
    "programme applies to your specific production before committing to either application process."
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Czech Film Fund — update cap TEXT field ────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap              = :new_cap,
            last_verified_at = '2026-03-29'
        WHERE territory = 'Czech Republic'
          AND program ILIKE '%Czech%'
          AND status = 'active'
    """), {"new_cap": _CZECH_NEW_CAP})

    assert_migration_count(
        conn,
        "incentive_programs",
        "territory = 'Czech Republic' AND cap = 'CZK 450M per project (~€18M)'",
        expected_min=1,
        migration_id=revision,
    )

    # ── 2. GMPF — update eligibility_rules_json ───────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_rules_json = :rules,
            last_verified_at       = '2026-03-29'
        WHERE territory = 'Germany'
          AND program   = 'German Motion Picture Fund (GMPF)'
          AND status    = 'active'
    """), {"rules": _GMPF_NEW_RULES})

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Germany' "
            "AND program = 'German Motion Picture Fund (GMPF)' "
            "AND eligibility_rules_json LIKE '%13M%'"
        ),
        expected_min=1,
        migration_id=revision,
    )

    # ── 3. GMPF — add eligibility_notes for format restriction ────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = :notes,
            last_verified_at  = '2026-03-29'
        WHERE territory = 'Germany'
          AND program   = 'German Motion Picture Fund (GMPF)'
          AND status    = 'active'
    """), {"notes": _GMPF_ELIGIBILITY_NOTES})

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Germany' "
            "AND program = 'German Motion Picture Fund (GMPF)' "
            "AND eligibility_notes IS NOT NULL"
        ),
        expected_min=1,
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Restore Czech cap TEXT field
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap = :old_cap
        WHERE territory = 'Czech Republic'
          AND program ILIKE '%Czech%'
          AND status = 'active'
    """), {"old_cap": _CZECH_OLD_CAP})

    # Restore GMPF eligibility_rules_json
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_rules_json = :rules
        WHERE territory = 'Germany'
          AND program   = 'German Motion Picture Fund (GMPF)'
          AND status    = 'active'
    """), {"rules": _GMPF_OLD_RULES})

    # Clear GMPF eligibility_notes
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = NULL
        WHERE territory = 'Germany'
          AND program   = 'German Motion Picture Fund (GMPF)'
          AND status    = 'active'
    """))
