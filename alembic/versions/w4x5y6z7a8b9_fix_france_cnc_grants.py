"""fix_france_cnc_grants

Revision ID: w4x5y6z7a8b9
Revises: v3w4x5y6z7a8
Create Date: 2026-03-23

Fixes two issues with the France grant data identified by cross-checking the
report output against official CNC documentation.

ISSUE 1 — CNC Aide Sélective à la Production: eligibility too broad
--------------------------------------------------------------------
The grant was seeded with eligibility "French or co-production eligible" and
"Must have French distributor attached", which implies it is accessible to a
US-initiated production engaging a French production services company (PSC).

This is incorrect. The Aide sélective à la production (avance sur recettes
avant réalisation / après réalisation) is available ONLY to the FRENCH
DELEGATE PRODUCER (société de production déléguée). A foreign production
using a French PSC to access the TRIP rebate is NOT the delegate producer
and therefore CANNOT apply for the avance sur recettes.

The eligibility text has been corrected to accurately reflect this restriction
and to prevent the grant from appearing on reports for foreign service
productions.

Source: CNC — https://www.cnc.fr/professionnels/aides-et-financements/cinema/production/aide-selective-a-la-production_191475
"L'aide sélective à la production est accordée aux producteurs délégués..."

ISSUE 2 — Missing grant: Aide aux cinémas du monde (ACM)
---------------------------------------------------------
The ACM is the correct CNC international funding programme for projects that
have genuine French creative involvement but are not French-initiated. It
supports international co-productions between a French production company
(acting as co-producer, NOT a service company) and a foreign production
company. The director must be a foreign national.

This is the programme that should appear in reports where the production has
a bona fide French co-producer rather than a service-only arrangement.

The Aide sélective (avance sur recettes) is not available to these productions
but the ACM may be, and it was entirely absent from the DB.

Source: CNC — https://www.cnc.fr/professionnels/aides-et-financements/cinema/production/aide-aux-cinemas-du-monde_191476
"""
import json as _json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "w4x5y6z7a8b9"
down_revision = "v3w4x5y6z7a8"
branch_labels = None
depends_on = None

# ── Issue 1 — correct Aide Sélective eligibility ─────────────────────────────

_AIDE_SELECTIVE_TITLE = "CNC Aide Sélective à la Production (Avance sur recettes)"
_AIDE_SELECTIVE_TERRITORY = "France"

_OLD_ELIGIBILITY = _json.dumps([
    "French or co-production eligible",
    "Feature-length fiction film",
    "Must have French distributor attached",
])

_NEW_ELIGIBILITY = _json.dumps([
    "FRENCH DELEGATE PRODUCER (société de production déléguée) required — "
    "this programme is NOT accessible to foreign productions using a French "
    "production services company (PSC) to access the TRIP rebate",
    "Feature-length fiction film with French majority production",
    "French theatrical distributor attached",
    "Application assessed by a CNC selection committee (not automatic)",
])

# ── Issue 2 — insert Aide aux cinémas du monde (ACM) ─────────────────────────

_ACM_ROW = {
    "id": str(uuid4()),
    "title": "CNC Aide aux cinémas du monde (ACM)",
    "territory": "France",
    "funding_body": "Centre national du cinéma (CNC)",
    "max_amount": "\u20ac200,000",
    "currency": "EUR",
    "application_deadline": "2026-12-31",
    "status": "open",
    "eligibility": _json.dumps([
        "Genuine international CO-PRODUCTION required between a French production "
        "company (co-producer, NOT a service company) and a foreign production company",
        "Director must be a foreign national (non-French)",
        "French co-producer must hold a meaningful creative and financial share",
        "Feature-length fiction film or documentary",
        "French theatrical distribution commitment",
        "NOT available to foreign productions using only a French PSC for TRIP — "
        "a genuine co-production agreement with shared creative control is required",
    ]),
    "website_url": "https://www.cnc.fr/professionnels/aides-et-financements/cinema/production/aide-aux-cinemas-du-monde_191476",
    "data_source": "CNC official",
    "verified": True,
    "is_new": False,
}

_ACM_INSERT_SQL = """\
INSERT INTO grant_opportunities (
    id, title, territory, funding_body, max_amount, currency,
    application_deadline, status, eligibility,
    website_url, data_source, verified, is_new,
    created_at, updated_at, last_verified_at
) VALUES (
    :id, :title, :territory, :funding_body, :max_amount, :currency,
    :application_deadline, :status, :eligibility,
    :website_url, :data_source, :verified, :is_new,
    NOW(), NOW(), '2026-03-23'
)
"""

_ACM_DELETE_SQL = """\
DELETE FROM grant_opportunities
WHERE title = :title AND territory = :territory
"""


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Correct Aide Sélective eligibility ─────────────────────────────────
    conn.execute(
        sa.text(
            "UPDATE grant_opportunities "
            "SET eligibility  = :eligibility, "
            "    updated_at   = NOW(), "
            "    last_verified_at = '2026-03-23' "
            "WHERE title      = :title "
            "  AND territory  = :territory"
        ),
        {
            "eligibility": _NEW_ELIGIBILITY,
            "title": _AIDE_SELECTIVE_TITLE,
            "territory": _AIDE_SELECTIVE_TERRITORY,
        },
    )

    # ── 2. Insert Aide aux cinémas du monde ───────────────────────────────────
    conn.execute(sa.text(_ACM_INSERT_SQL), _ACM_ROW)


def downgrade() -> None:
    conn = op.get_bind()

    # Remove ACM row
    conn.execute(
        sa.text(_ACM_DELETE_SQL),
        {"title": _ACM_ROW["title"], "territory": _ACM_ROW["territory"]},
    )

    # Restore original Aide Sélective eligibility
    conn.execute(
        sa.text(
            "UPDATE grant_opportunities "
            "SET eligibility = :eligibility "
            "WHERE title     = :title "
            "  AND territory = :territory"
        ),
        {
            "eligibility": _OLD_ELIGIBILITY,
            "title": _AIDE_SELECTIVE_TITLE,
            "territory": _AIDE_SELECTIVE_TERRITORY,
        },
    )
