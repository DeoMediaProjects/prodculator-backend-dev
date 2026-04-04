"""add_qualifying_spend_type_and_fix_rates

Revision ID: d5e6f7g8h9i0
Revises: c4d5e6f7g8h9
Create Date: 2026-03-19 12:00:00.000000

Adds two new schema columns and fixes three confirmed rate errors discovered
through systematic verification against official government sources.

NEW COLUMNS
-----------
qualifying_spend_type (TEXT, default 'total')
  Describes what category of expenditure the rate applies to:
    'total'       — applies to all qualifying production spend (default)
    'labour'      — applies to qualifying labour expenditure only
                    (Canada PSTC, BC FIBC — rate applied to estimated labour %)
    'pdv'         — applies to post-production / VFX / digital work only
                    (Australia PDV Offset)
    'local_spend' — applies to in-territory expenditure only
                    (France TRIP, Germany DFFF, Italy, Czech, Hungary, SA, Malta,
                    Iceland, Spain — model note added; cap-pct still applied where known)

qualifying_spend_labour_pct (FLOAT)
  For 'labour' type: estimated % of total budget that is qualifying labour.
  For 'pdv' type: estimated % of total budget that is qualifying PDV work.
  Not applicable for 'total' / 'local_spend' types.

RATE CORRECTIONS (official government sources, 2026-03-19)
----------------------------------------------------------
1. BC Film Incentive BC Tax Credit (FIBC) — 36% → 40%
   Source: creativebc.com — "40% basic rate for productions with principal
   photography start date after December 31, 2024 (increased from 35%)"
   Note: our previous migration set 36%, but the correct current rate is 40%.

2. Germany DFFF (German Federal Film Fund) — 20% → 30%
   Source: ffa.de — "up to 30% of eligible German production costs (max €5M per
   film)" for projects starting on or after February 1, 2025 (previously 20-25%).

3. Czech Republic Film Fund Incentive Programme — 20% → 25%
   Source: filmcommission.cz — "25% rebate" for live-action features, series, and
   documentaries (35% for animation/digital only projects).

STRUCTURAL DATA UPDATES
-----------------------
- Germany DFFF: qualifying_spend_cap_pct = 80 (German costs capped at 80% of total
  budget per official programme rules — "Qualifying German Spend ... only up to 80%
  of total production costs")
- Italy: qualifying_spend_cap_pct = 75 (Italian production expenditure capped at 75%
  of total budget per official MiC Film Fund rules)
- Australia Location Offset: qualifying_spend_min = 20,000,000 AUD for feature films
  (updated from 15M — Screen Australia guidelines confirm AUD $20M minimum for
  features as of 2024)
- South Africa: spv_eligible = True (the programme explicitly requires a South African
  SPCV, which IS the foreign producer's own Special Purpose Corporate Vehicle)
"""
from alembic import op
import sqlalchemy as sa

revision = "d5e6f7g8h9i0"
down_revision = "c4d5e6f7g8h9"
branch_labels = None
depends_on = None


# ─── Rate corrections ────────────────────────────────────────────────────────

_BC_TERRITORY = "British Columbia"
_BC_PROGRAM = "BC Film Incentive BC Tax Credit (FIBC)"
_BC_NEW_RATE_GROSS = 40.0
_BC_NEW_RATE_NET = 40.0
_BC_NEW_RATE = "40% of qualified BC labour"
_BC_OLD_RATE_GROSS = 36.0  # set by previous migration c4d5e6f7g8h9
_BC_OLD_RATE_NET = 36.0
_BC_OLD_RATE = "36% of qualified BC labour"

_DE_TERRITORY = "Germany"
_DE_PROGRAM = "DFFF (German Federal Film Fund)"
_DE_NEW_RATE_GROSS = 30.0
_DE_NEW_RATE_NET = 30.0
_DE_NEW_RATE = "30% of eligible German production costs (max €5M)"
_DE_OLD_RATE_GROSS = 20.0
_DE_OLD_RATE_NET = 20.0
_DE_OLD_RATE = "20% of German production costs"

_CZ_TERRITORY = "Czech Republic"
_CZ_PROGRAM = "Czech Film Fund Incentive Programme"
_CZ_NEW_RATE_GROSS = 25.0
_CZ_NEW_RATE_NET = 25.0
_CZ_NEW_RATE = "25% of qualifying Czech production expenditure"
_CZ_OLD_RATE_GROSS = 20.0
_CZ_OLD_RATE_NET = 20.0
_CZ_OLD_RATE = "20% of qualifying Czech production expenditure"


def upgrade() -> None:
    conn = op.get_bind()

    # ── Schema: add new columns ───────────────────────────────────────────────
    op.add_column(
        "incentive_programs",
        sa.Column("qualifying_spend_type", sa.Text(), server_default="total", nullable=True),
    )
    op.add_column(
        "incentive_programs",
        sa.Column("qualifying_spend_labour_pct", sa.Float(), nullable=True),
    )

    # ── 1. Set defaults for all existing rows ────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'total' "
            "WHERE qualifying_spend_type IS NULL"
        )
    )

    # ── 2. Labour-only credits ────────────────────────────────────────────────
    # Canada Federal PSTC: 16% applies to qualifying Canadian labour only
    # Typical qualifying Canadian labour = 35% of total production budget
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'labour', "
            "    qualifying_spend_labour_pct = 35.0, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Canada' "
            "  AND program = 'Canada Federal PSTC (Production Services Tax Credit)'"
        )
    )

    # BC Film Incentive BC Tax Credit: 40% of qualified BC labour only
    # (Canadian-controlled corp required; labour % same as federal PSTC)
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'labour', "
            "    qualifying_spend_labour_pct = 35.0, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'British Columbia' "
            "  AND program = 'BC Film Incentive BC Tax Credit (FIBC)'"
        )
    )

    # ── 3. PDV-only credits ───────────────────────────────────────────────────
    # Australia PDV Offset: 30% applies to qualifying PDV expenditure only
    # Estimated PDV portion of a typical drama budget: 15%
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'pdv', "
            "    qualifying_spend_labour_pct = 15.0, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Australia' "
            "  AND program = 'PDV Offset (Post, Digital & VFX)'"
        )
    )

    # ── 4. Local-spend credits ────────────────────────────────────────────────
    # France TRIP: applies to qualifying French expenditure (not total budget)
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'France' "
            "  AND program = 'TRIP (Tax Rebate for International Production)'"
        )
    )

    # Germany DFFF: applies to German production costs, capped at 80% of total budget
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    qualifying_spend_cap_pct = 80.0, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Germany' "
            "  AND program = 'DFFF (German Federal Film Fund)'"
        )
    )

    # Germany GMPF (if present)
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Germany' "
            "  AND program = 'German Motion Picture Fund (GMPF)'"
        )
    )

    # Spain: applies to qualifying Spanish production expenditure only
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    cap_per_person = 100000.0, "
            "    cap_per_person_currency = 'EUR', "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Spain' "
            "  AND program LIKE '%Spain%Tax%'"
        )
    )

    # Canary Islands: applies to Canary Islands expenditure only
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    cap_per_person = 100000.0, "
            "    cap_per_person_currency = 'EUR', "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Canary Islands'"
        )
    )

    # Italy: applies to Italian production expenditure, capped at 75% of total budget
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    qualifying_spend_cap_pct = 75.0, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Italy'"
        )
    )

    # Czech Republic: applies to Czech goods and services only
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Czech Republic'"
        )
    )

    # Hungary: applies to Hungarian production costs
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Hungary'"
        )
    )

    # South Africa: applies to qualifying South African production expenditure
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    spv_eligible = TRUE, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'South Africa'"
        )
    )

    # Malta: applies to qualifying Maltese expenditure
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Malta'"
        )
    )

    # Iceland: applies to qualifying Icelandic production expenditure
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Iceland'"
        )
    )

    # Ireland Section 481: applies to qualifying Irish production expenditure
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_type = 'local_spend', "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Ireland'"
        )
    )

    # ── 5. Rate corrections ───────────────────────────────────────────────────

    # BC FIBC: 36% → 40% (creativebc.com; effective Jan 2025)
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = :rate_gross, "
            "    rate_net = :rate_net, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _BC_NEW_RATE,
            "rate_gross": _BC_NEW_RATE_GROSS,
            "rate_net": _BC_NEW_RATE_NET,
            "territory": _BC_TERRITORY,
            "program": _BC_PROGRAM,
        },
    )

    # Germany DFFF: 20% → 30% (ffa.de; effective Feb 2025)
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = :rate_gross, "
            "    rate_net = :rate_net, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _DE_NEW_RATE,
            "rate_gross": _DE_NEW_RATE_GROSS,
            "rate_net": _DE_NEW_RATE_NET,
            "territory": _DE_TERRITORY,
            "program": _DE_PROGRAM,
        },
    )

    # Czech Republic: 20% → 25% (filmcommission.cz)
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, "
            "    rate_gross = :rate_gross, "
            "    rate_net = :rate_net, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _CZ_NEW_RATE,
            "rate_gross": _CZ_NEW_RATE_GROSS,
            "rate_net": _CZ_NEW_RATE_NET,
            "territory": _CZ_TERRITORY,
            "program": _CZ_PROGRAM,
        },
    )

    # ── 6. Australia Location Offset: min spend AUD 15M → 20M ────────────────
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_min = 20000000.0, "
            "    last_verified_at = '2026-03-19' "
            "WHERE territory = 'Australia' "
            "  AND program = 'Location Offset (Foreign Productions)'"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Restore rate corrections
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, rate_gross = :rate_gross, rate_net = :rate_net "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _BC_OLD_RATE,
            "rate_gross": _BC_OLD_RATE_GROSS,
            "rate_net": _BC_OLD_RATE_NET,
            "territory": _BC_TERRITORY,
            "program": _BC_PROGRAM,
        },
    )
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, rate_gross = :rate_gross, rate_net = :rate_net "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _DE_OLD_RATE,
            "rate_gross": _DE_OLD_RATE_GROSS,
            "rate_net": _DE_OLD_RATE_NET,
            "territory": _DE_TERRITORY,
            "program": _DE_PROGRAM,
        },
    )
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET rate = :rate, rate_gross = :rate_gross, rate_net = :rate_net "
            "WHERE territory = :territory AND program = :program"
        ),
        {
            "rate": _CZ_OLD_RATE,
            "rate_gross": _CZ_OLD_RATE_GROSS,
            "rate_net": _CZ_OLD_RATE_NET,
            "territory": _CZ_TERRITORY,
            "program": _CZ_PROGRAM,
        },
    )

    # Restore Australia Location Offset min spend
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET qualifying_spend_min = 15000000.0 "
            "WHERE territory = 'Australia' "
            "  AND program = 'Location Offset (Foreign Productions)'"
        )
    )

    # Restore South Africa spv_eligible
    conn.execute(
        sa.text(
            "UPDATE incentive_programs "
            "SET spv_eligible = FALSE "
            "WHERE territory = 'South Africa'"
        )
    )

    # Drop new columns
    op.drop_column("incentive_programs", "qualifying_spend_labour_pct")
    op.drop_column("incentive_programs", "qualifying_spend_type")
