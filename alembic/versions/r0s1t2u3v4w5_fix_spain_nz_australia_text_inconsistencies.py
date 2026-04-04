"""fix_spain_nz_australia_text_inconsistencies

Revision ID: r0s1t2u3v4w5
Revises: q9r0s1t2u3v4
Create Date: 2026-03-29

Three independent text/data inconsistencies identified by cross-checking the
Duchess ($23M EUR, feature) report against official government sources.

ROOT CAUSE 1 — Spain: cap text field €10M overrides the correct cap_amount €20M
----------------------------------------------------------------------------------
Builder priority order for the cap field:
    1. rebate_cap_amount          → NULL for Spain (skipped)
    2. cap TEXT column            → "€10M maximum deduction per project"  ← SHOWN
    3. cap_amount numeric column  → 20,000,000 EUR                        ← IGNORED

Migration f7g8h9i0j1k2 set both cap = "€10M..." and cap_amount = 10M.
Migration m4n5o6p7q8r9 later updated cap_amount to €20M (correct) but never
touched the cap text field. Because the builder checks cap text first, the stale
"€10M" string is returned on every report despite the numeric column being correct.

Official source: Art. 36.2 LIS / ICAA — "the maximum deduction is 20 million euros
for each production; for audiovisual series, the limit is 10 million euros per
episode." The €10M applies to TV series episodes only, not feature films.

ROOT CAUSE 2 — New Zealand: eligibility_rules_json still says NZD 15M
----------------------------------------------------------------------
Migration h9i0j1k2l3m4 correctly updated qualifying_spend_min = 4,000,000 (NZD)
and wrote the corrected value into eligibility_notes and warnings_json. However it
did not update eligibility_rules_json. The seed contains:

    {"rule": "Minimum NZD 15M qualifying NZ production expenditure", "required": true}

This rule is the source of the requirements table in the report — it shows NZD 15M
while the qualifyingSpend field (from qualifying_spend_min) shows NZD 4M.
Internal inconsistency confirmed: NZFC reduced live-action minimum from NZD 15M to
NZD 4M effective 1 January 2026.

Source: NZFC — nzfilm.co.nz/incentives/nzspg-international

ROOT CAUSE 3 — Australia: Location Offset has no eligibility_notes
-------------------------------------------------------------------
The Location Offset's eligibility_notes field is NULL — no migration has ever set
it. The AI sees structured rules (QAPE, Final Certificate) without any explicit
statement that QAPE includes principal photography. With the PDV Offset row also
in the DB (explicitly labelled "Applies to post-production/VFX only"), the AI
incorrectly infers by contrast that the Location Offset is also PDV-restricted.

Fact: QAPE for the Location Offset includes ALL qualifying Australian production
expenditure — pre-production, principal photography, and post-production incurred
in Australia. It is not restricted to PDV work. The PDV Offset is a separate
programme that covers post-production/VFX regardless of where principal
photography occurred.

Source: ATO — ato.gov.au/businesses-and-organisations/income-deductions-offsets-
and-records/offsets-and-rebates/film-offsets
Screen Australia — screenaustralia.gov.au/funding-and-support/producer-offset/
location-offset

Last Verified: 2026-03-29
"""
from __future__ import annotations

import json as _json

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import assert_migration_count

revision = "r0s1t2u3v4w5"
down_revision = "q9r0s1t2u3v4"
branch_labels = None
depends_on = None

# ── 1. Spain — correct cap text to match cap_amount ──────────────────────────
_SPAIN_NEW_CAP = "€20M maximum deduction per feature film project"
_SPAIN_OLD_CAP = "€10M maximum deduction per project"

# ── 2. New Zealand — correct eligibility_rules_json min spend text ────────────
_NZ_NEW_RULES = _json.dumps([
    {
        "rule": "Minimum NZD 4M qualifying NZ production expenditure (reduced from NZD 15M effective 1 January 2026)",
        "required": True,
    },
    {
        "rule": "Must apply to NZFC for provisional approval before photography",
        "required": True,
    },
    {
        "rule": "5% uplift requires demonstrating significant economic benefits to NZ",
        "required": False,
    },
])

_NZ_OLD_RULES = _json.dumps([
    {"rule": "Minimum NZD 15M qualifying NZ production expenditure", "required": True},
    {"rule": "Must apply to NZFC for provisional approval before photography", "required": True},
    {"rule": "5% uplift requires demonstrating significant economic benefits to NZ", "required": False},
])

# ── 3. Australia Location Offset — add eligibility_notes clarifying QAPE scope ─
_AU_NEW_ELIGIBILITY_NOTES = (
    "Applies to ALL qualifying Australian production expenditure (QAPE), including "
    "principal photography, pre-production, and post-production incurred in Australia. "
    "This programme is NOT restricted to post-production or VFX. "
    "The PDV Offset is a separate programme covering post-production/VFX only "
    "(regardless of where principal photography occurred). "
    "QAPE definition: costs directly incurred in Australia for making the film, "
    "including location fees, cast, crew, and production services in Australia."
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Spain: update cap text €10M → €20M ────────────────────────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap              = :new_cap,
            last_verified_at = '2026-03-29'
        WHERE territory = 'Spain'
          AND program ILIKE '%Spain General Tax Incentive%'
          AND status = 'active'
    """), {"new_cap": _SPAIN_NEW_CAP})

    assert_migration_count(
        conn,
        "incentive_programs",
        "territory = 'Spain' AND cap = '€20M maximum deduction per feature film project'",
        expected_min=1,
        migration_id=revision,
    )

    # ── 2. New Zealand: update eligibility_rules_json NZD 15M → 4M ───────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_rules_json = :rules,
            last_verified_at       = '2026-03-29'
        WHERE territory = 'New Zealand'
          AND status    = 'active'
    """), {"rules": _NZ_NEW_RULES})

    assert_migration_count(
        conn,
        "incentive_programs",
        "territory = 'New Zealand' AND eligibility_rules_json LIKE '%NZD 4M%'",
        expected_min=1,
        migration_id=revision,
    )

    # ── 3. Australia: add eligibility_notes for Location Offset ──────────────
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = :notes,
            last_verified_at  = '2026-03-29'
        WHERE territory = 'Australia'
          AND program   = 'Location Offset (Foreign Productions)'
          AND status    = 'active'
    """), {"notes": _AU_NEW_ELIGIBILITY_NOTES})

    assert_migration_count(
        conn,
        "incentive_programs",
        (
            "territory = 'Australia' "
            "AND program = 'Location Offset (Foreign Productions)' "
            "AND eligibility_notes IS NOT NULL"
        ),
        expected_min=1,
        migration_id=revision,
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Restore Spain cap text
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap = :old_cap
        WHERE territory = 'Spain'
          AND program ILIKE '%Spain General Tax Incentive%'
          AND status = 'active'
    """), {"old_cap": _SPAIN_OLD_CAP})

    # Restore NZ eligibility_rules_json
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_rules_json = :rules
        WHERE territory = 'New Zealand'
          AND status    = 'active'
    """), {"rules": _NZ_OLD_RULES})

    # Clear Australia Location Offset eligibility_notes
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET eligibility_notes = NULL
        WHERE territory = 'Australia'
          AND program   = 'Location Offset (Foreign Productions)'
          AND status    = 'active'
    """))
