"""restore_dropped_cap_constraints

Revision ID: u1v2w3x4y5z6
Revises: s9t0u1v2w3x4
Create Date: 2026-08-19

Restores cap constraints that the v4 ingest (ab2c3d4e5f61) discarded because the
source strings were not bare numbers.

ROOT CAUSE
----------
``_build_row`` reduced two source fields with parsers that keep the first thing
they recognise and drop the rest, with no warning fallback:

1. ``qsCapPct`` was read with ``_first_number()``. The UK IFTC row states

     "80% of core expenditure, capped at GBP 12,000,000 qualifying expenditure
      regardless of budget size within eligibility"

   and stored 80.0 alone. The GBP 12,000,000 ceiling existed nowhere numeric, so
   ReportValidator scaled qualifying spend with the budget:

     budget GBP 18.7M -> qualifying spend GBP 14.96M   (correct: GBP 12M)
     budget GBP 23.0M -> qualifying spend GBP 18.40M   (correct: GBP 12M)

   The GBP 6.36M rebate_cap_amount masked the error in the gross figure (6.36M
   IS 12M x 53%), but the overstated qualifying spend still fed the waterfall,
   the net-cost model and the ranking.

2. ``rebateCap`` was read with ``_money()``, which anchors at the start of the
   string. Any ceiling stated with a qualification around it parsed to None and
   the programme was modelled as uncapped:

     Belgium      EUR 7,250,000 (~USD 8,000,000) maximum amount that can be...
     Netherlands  EUR 3,000,000 per production company (per some sources) - ...
     Mexico EFICA MXN 40,000,000 per production/beneficiary; MXN 400,000,000...

FIX
---
Two new columns carry the absolute qualifying-expenditure ceiling, applied by
ReportValidator._qualifying_spend_for as MIN(pct x budget, absolute ceiling).
The ingest parsers in ab2c3d4e5f61 are corrected in the same change so a fresh
build produces these values directly; this migration repairs databases already
built from the old loader.

Values are re-stated explicitly rather than re-parsed, so the numbers going into
a live table are reviewable in this file.

SOURCES
-------
UK IFTC   GBP 12,000,000 = 80% of the fixed GBP 15,000,000 reference amount.
          Max credit GBP 6.36M = GBP 15M x 80% x 53%.
          https://www.gov.uk/guidance/audio-visual-expenditure-credit
Belgium   EUR 7,250,000 per-project shelter ceiling (v4 source row).
Netherlands EUR 3,000,000 per production (v4 source row, flagged for
          re-confirmation - the warning text is retained on the row).
Mexico EFICA MXN 40,000,000 per production/beneficiary; the MXN 400,000,000
          programme-wide pool is an annual allocation, not a per-project cap,
          so only the per-project figure becomes a rebate ceiling.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u1v2w3x4y5z6"
down_revision = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


_NEW_COLUMNS = (
    ("qualifying_spend_cap_amount", sa.Float()),
    ("qualifying_spend_cap_currency", sa.String(3)),
)


def upgrade() -> None:
    conn = op.get_bind()

    existing = {c["name"] for c in sa.inspect(conn).get_columns("incentive_programs")}
    for name, coltype in _NEW_COLUMNS:
        if name not in existing:
            op.add_column("incentive_programs", sa.Column(name, coltype, nullable=True))

    # ── UK IFTC — absolute qualifying-expenditure ceiling ────────────────────
    # Matched on a LIKE so both the v4 name ('AVEC (Enhanced/IFTC)') and the
    # pre-v4 name ('UK Independent Film Tax Credit (IFTC)') are covered. An
    # earlier fix (a8b9c0d1e2f3) targeted only the pre-v4 name and matched zero
    # rows on any database built from the v4 loader.
    result = conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_cap_amount   = 12000000.0,
            qualifying_spend_cap_currency = 'GBP',
            last_verified_at              = '2026-08-19'
        WHERE territory = 'United Kingdom'
          AND (program ILIKE '%IFTC%' OR program ILIKE '%Independent Film Tax Credit%')
          AND (status = 'active' OR status IS NULL)
    """))
    print(f"[u1v2w3x4y5z6] UK IFTC qualifying-spend ceiling set on {result.rowcount} row(s)")

    # The same row must keep its gross rebate ceiling. Re-stated here because
    # a8b9c0d1e2f3's name-specific UPDATE does not reach the v4 row.
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_amount   = 6360000.0,
            rebate_cap_currency = 'GBP'
        WHERE territory = 'United Kingdom'
          AND (program ILIKE '%IFTC%' OR program ILIKE '%Independent Film Tax Credit%')
          AND (status = 'active' OR status IS NULL)
          AND (rebate_cap_amount IS NULL OR rebate_cap_amount = 0)
    """))

    # ── Rebate ceilings dropped by the anchored money parser ─────────────────
    for territory, name_like, amount, currency in (
        ("Belgium", "%Tax Shelter%", 7250000.0, "EUR"),
        ("Netherlands", "%Film Production Incentive%", 3000000.0, "EUR"),
        ("Mexico", "%EFICA%", 40000000.0, "MXN"),
    ):
        res = conn.execute(sa.text("""
            UPDATE incentive_programs
            SET rebate_cap_amount   = :amount,
                rebate_cap_currency = :currency
            WHERE territory = :territory
              AND program ILIKE :name_like
              AND (status = 'active' OR status IS NULL)
              AND (rebate_cap_amount IS NULL OR rebate_cap_amount = 0)
        """), {
            "amount": amount, "currency": currency,
            "territory": territory, "name_like": name_like,
        })
        print(f"[u1v2w3x4y5z6] {territory} rebate cap set on {res.rowcount} row(s)")

    # ── Source verification, 2026-08-19 ──────────────────────────────────────
    # UK rows confirmed against HMRC guidance: 53% enhanced rate, relief limited
    # to GBP 15M core costs (x 80% = GBP 12M qualifying, max credit GBP 6.36M),
    # eligibility at core costs under GBP 23.5M as a hard threshold with NO
    # tapering, standard AVEC 34% (39% animation/children's TV), VFX 39% and
    # exempt from the 80% cap. Every stored value matched; only the verification
    # date changes.
    # https://www.gov.uk/guidance/claim-audio-visual-expenditure-credits-for-corporation-tax
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET last_verified_at = '2026-08-19'
        WHERE territory = 'United Kingdom'
          AND (status = 'active' OR status IS NULL)
    """))

    # Netherlands confirmed at 35% with a EUR 3M ceiling, but the ceiling is per
    # production COMPANY PER YEAR, not per production. Enforcing it as a
    # per-project cap cannot overstate a single production's rebate, so it stays
    # as rebate_cap_amount; the display text records what it actually is, and the
    # "verify exact current per-production cap" hedge is now resolved.
    # https://www.filmfonds.nl/en/funding/fund/netherlands-film-production-incentive
    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET rebate_cap_display = 'EUR 3,000,000 per production company per year '
                                 '(applied here as a per-project ceiling, which is '
                                 'the conservative reading for a single production)',
            last_verified_at   = '2026-08-19'
        WHERE territory = 'Netherlands'
          AND program ILIKE '%Film Production Incentive%'
          AND (status = 'active' OR status IS NULL)
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET qualifying_spend_cap_amount   = NULL,
            qualifying_spend_cap_currency = NULL
    """))
    for territory, name_like in (
        ("Belgium", "%Tax Shelter%"),
        ("Netherlands", "%Film Production Incentive%"),
        ("Mexico", "%EFICA%"),
    ):
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET rebate_cap_amount   = NULL,
                rebate_cap_currency = NULL
            WHERE territory = :territory AND program ILIKE :name_like
        """), {"territory": territory, "name_like": name_like})

    for name, _ in _NEW_COLUMNS:
        op.drop_column("incentive_programs", name)
