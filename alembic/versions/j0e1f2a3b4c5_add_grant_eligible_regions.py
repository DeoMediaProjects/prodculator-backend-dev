"""add_grant_eligible_regions

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-08-04

PROD-FIX-008 — fund/genre-territory mismatch (Busan Asian Cinema Fund).

The Blacbet Master report, a Nigeria/Benin-set West African Macbeth adaptation,
recommended the Busan International Film Festival Asian Cinema Fund as a
near-term priority "that should be submitted immediately", with no restriction
warning. Tester 1 reported the same class of error on Cloaks (BFI Doc Society
Fund and Fonds Images Afrique surfaced for a project outside their scope).

This is a missing dimension, not a bad record. The dataset's
`nationality_required` flag means "restricted to a single country", and the
Busan entry's own eligibility text reads:

    "Asian filmmakers (broad Asia-Pacific definition). No nationality
     restriction within Asia."

So `nationality_required = false` is literally correct — there was simply no
field in which to record that eligibility is bounded by REGION. An audit of all
114 funds found 16 stating a regional bound in prose that no structured field
captured, so grant matching could only filter on format, deadline, staleness,
single-country nationality, genre and budget.

This migration adds `eligible_regions` (JSON array) and populates it for those
16 from each fund's own eligibility text — no eligibility is invented here, it
is transcribed from the record that already stated it.

Funds whose prose says "worldwide" or expresses a soft preference rather than a
bound are deliberately left NULL:

  * DOC/NYC Fund for Inclusion       — "underrepresented communities worldwide"
  * Tribeca Reframe Grants           — "underrepresented backgrounds worldwide"
  * EWA Network                      — "Europe and beyond ... preferred"

Matching treats NULL as unrestricted, and treats a declared region as a bound
only when the production's own location is known — see app/core/regions.py.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "j0e1f2a3b4c5"
down_revision = "i9d0e1f2a3b4"
branch_labels = None
depends_on = None

_TABLE = "grant_opportunities"

# fund_name -> regions, each transcribed from that fund's own eligibility text.
_FUND_REGIONS: dict[str, list[str]] = {
    # "International co-productions with French or European partner required."
    "ARTE France Cinéma — Co-production Fund": ["Europe"],
    # "African filmmakers and international co-productions with African partners."
    "Durban FilmMart — Development Finance Forum": ["Africa"],
    # "African filmmakers (all nationalities on African continent)."
    "Africa International Film Festival (AFRIFF) — Short Film Fund": ["Africa"],
    # "Documentary filmmakers from Africa, Asia, Latin America, Middle East,
    #  and Eastern Europe (priority regions)."
    "IDFA Bertha Fund": [
        "Africa", "Asia", "Latin America", "Middle East", "Eastern Europe",
    ],
    # "African filmmakers (all nationalities on African continent)."
    "Realness Institute — African Feature Film Development": ["Africa"],
    # "Film and media companies across African Development Bank member countries."
    "African Development Bank — Fund for African Private Sector Assistance "
    "(FAPA) Cultural Industry": ["Africa"],
    # "African filmmakers co-producing with French partners, or French
    #  productions about African subjects." — the Cloaks mismatch.
    "Fonds Images Afrique — Institut Français": ["Africa"],
    # "Filmmakers from underrepresented regions: Africa, Asia, Latin America,
    #  Middle East, Eastern Europe."
    "Hubert Bals Fund — IDFAcademy": [
        "Africa", "Asia", "Latin America", "Middle East", "Eastern Europe",
    ],
    "IFFR — Hubert Bals Fund — Production": [
        "Africa", "Asia", "Latin America", "Middle East", "Eastern Europe",
    ],
    # "African documentary filmmakers at production stage."
    "Hot Docs — Blue Ice Fund (Africa Focus)": ["Africa"],
    # "Asian filmmakers (broad Asia-Pacific definition)." — the reported case.
    "Busan International Film Festival — Asian Cinema Fund": ["Asia"],
    # "Southeast Asian filmmakers."
    "Singapore International Film Festival — Southeast Asian Film Fund": [
        "Southeast Asia",
    ],
    # "Filmmakers from underrepresented regions: Africa, Latin America,
    #  Middle East, Central Asia, Southeast Asia."
    "Berlinale World Cinema Fund": [
        "Africa", "Latin America", "Middle East", "Central Asia",
        "Southeast Asia",
    ],
}


def upgrade() -> None:
    conn = op.get_bind()

    if not any(
        c["name"] == "eligible_regions"
        for c in sa.inspect(conn).get_columns(_TABLE)
    ):
        op.add_column(_TABLE, sa.Column("eligible_regions", sa.Text(), nullable=True))

    # The fund name lives in `title` — `fund_name` is the key used in the
    # source-data blobs, not a column (see b2a3b4c5d6e7, which matches the
    # same way).
    updated, missing = 0, []
    for fund_name, regions in _FUND_REGIONS.items():
        result = conn.execute(
            sa.text(f"""
                UPDATE {_TABLE}
                SET eligible_regions = :regions
                WHERE title = :fund_name
            """),
            {"regions": json.dumps(regions), "fund_name": fund_name},
        )
        if result.rowcount:
            updated += result.rowcount
        else:
            missing.append(fund_name)

    # A rename upstream must not silently leave a fund unrestricted again —
    # that is precisely how the Busan recommendation reached a client.
    assert not missing, (
        "PROD-FIX-008: no grant_opportunities row matched these fund names, so "
        "their regional eligibility was NOT applied:\n  " + "\n  ".join(missing)
    )

    print(f"PROD-FIX-008: regional eligibility applied to {updated} fund(s)")


def downgrade() -> None:
    op.drop_column(_TABLE, "eligible_regions")
