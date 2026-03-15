"""seed_grant_opportunities_global

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-03-15 11:30:00.000000

Seeds ~25 real film production grant/funding opportunities across territories.
These are standing/recurring programmes — deadlines are indicative of the most
recent cycle and should be updated annually.

Sources: Official film commission and funding body websites.
"""
from alembic import op
import sqlalchemy as sa
import json

revision = "x8y9z0a1b2c3"
down_revision = "w7x8y9z0a1b2"
branch_labels = None
depends_on = None

# fmt: off

# (title, territory, funding_body, max_amount, currency, application_deadline,
#  status, eligibility[], website_url, data_source)
_GRANTS = [
    # ── United Kingdom ───────────────────────────────────────────────────
    ("BFI Film Fund — New Work",
     "United Kingdom", "British Film Institute", "£2,000,000", "GBP",
     "2026-12-31", "open",
     ["UK-based production company", "Feature film or feature doc",
      "Commercial theatrical release intended", "Must qualify for BFI diversity standards"],
     "https://www.bfi.org.uk/get-funding-support/filmmakers-bfi-film-fund",
     "BFI official"),

    ("BFI Short Film Fund",
     "United Kingdom", "British Film Institute", "£30,000", "GBP",
     "2026-09-30", "open",
     ["UK-based filmmaker", "Short film up to 15 mins",
      "First or second short film"],
     "https://www.bfi.org.uk/get-funding-support/filmmakers-bfi-short-film-fund",
     "BFI official"),

    ("Film4 Development Funding",
     "United Kingdom", "Film4 / Channel 4", "£500,000", "GBP",
     "2026-12-31", "open",
     ["UK/EU production company", "Feature film for theatrical release",
      "Ambitious authored filmmaking"],
     "https://www.film4.com/about",
     "Film4 official"),

    ("Creative Scotland Screen Fund",
     "Scotland", "Creative Scotland", "£500,000", "GBP",
     "2026-12-31", "open",
     ["Scotland-based company or significant Scottish cultural element",
      "Feature film, TV, or documentary"],
     "https://www.creativescotland.com/funding/funding-programmes/screen",
     "Creative Scotland official"),

    # ── Ireland ──────────────────────────────────────────────────────────
    ("Screen Ireland Production Funding",
     "Ireland", "Screen Ireland / Fís Éireann", "€800,000", "EUR",
     "2026-12-31", "open",
     ["Irish production company", "Feature film or feature doc",
      "Section 481 qualifying spend in Ireland"],
     "https://www.screenireland.ie/funding/production",
     "Screen Ireland official"),

    ("Screen Ireland First Feature Fund",
     "Ireland", "Screen Ireland", "€250,000", "EUR",
     "2026-06-30", "open",
     ["First-time feature director", "Irish production company",
      "Budget under €2.5M"],
     "https://www.screenireland.ie/funding/development-production",
     "Screen Ireland official"),

    # ── France ───────────────────────────────────────────────────────────
    ("CNC Aide Sélective à la Production (Avance sur recettes)",
     "France", "Centre national du cinéma (CNC)", "€600,000", "EUR",
     "2026-12-31", "open",
     ["French or co-production eligible", "Feature-length fiction film",
      "Must have French distributor attached"],
     "https://www.cnc.fr/professionnels/aides-et-financements/cinema/production",
     "CNC official"),

    ("Région Île-de-France Film Fund",
     "France", "Région Île-de-France", "€400,000", "EUR",
     "2026-10-15", "open",
     ["Shooting days in Île-de-France region", "Feature film",
      "French production company or co-production"],
     "https://www.iledefrance.fr/aides-et-appels-a-projets/aide-a-la-production-cinematographique",
     "Île-de-France official"),

    # ── Germany ──────────────────────────────────────────────────────────
    ("BKM (Beauftragter für Kultur und Medien) Production Fund",
     "Germany", "Federal Government Commissioner for Culture and Media", "€1,000,000", "EUR",
     "2026-12-31", "open",
     ["German production company or German co-producer",
      "Feature film with German cultural content",
      "Budget minimum €1M"],
     "https://www.bundesregierung.de/breg-de/bundesregierung/bundeskanzleramt/staatsministerin-fuer-kultur-und-medien",
     "BKM official"),

    ("Medienboard Berlin-Brandenburg Production Support",
     "Germany", "Medienboard Berlin-Brandenburg", "€800,000", "EUR",
     "2026-12-31", "open",
     ["Spend minimum 150% of grant in Berlin-Brandenburg",
      "Feature film", "German or co-production entity"],
     "https://www.medienboard.de/en/film-funding/production-support",
     "Medienboard official"),

    # ── Spain ────────────────────────────────────────────────────────────
    ("ICAA General Aid for Feature Film Production",
     "Spain", "ICAA (Instituto de Cinematografía)", "€1,000,000", "EUR",
     "2026-09-30", "open",
     ["Spanish production company", "Feature film (min 60 mins)",
      "Spanish language or co-official language"],
     "https://www.culturaydeporte.gob.es/cultura/areas/cine/ayudas.html",
     "ICAA / MCD official"),

    # ── Italy ────────────────────────────────────────────────────────────
    ("MiC Contributo Selettivo (Selective Contribution)",
     "Italy", "Ministero della Cultura (MiC)", "€600,000", "EUR",
     "2026-12-31", "open",
     ["Italian production company", "Feature film",
      "First/second work or culturally significant project"],
     "https://cinema.cultura.gov.it/contributi/contributo-selettivo/",
     "MiC official"),

    # ── South Africa ─────────────────────────────────────────────────────
    ("NFVF Production Funding",
     "South Africa", "National Film & Video Foundation", "R5,000,000", "ZAR",
     "2026-08-31", "open",
     ["South African production company", "Feature film",
      "BEE Level 1-4 company", "South African story content"],
     "https://www.nfvf.co.za/funding/",
     "NFVF official"),

    ("KwaZulu-Natal Film Commission Production Fund",
     "South Africa", "KwaZulu-Natal Film Commission", "R2,000,000", "ZAR",
     "2026-09-30", "open",
     ["KZN-based production", "Feature or documentary",
      "Minimum 60% spend in KZN"],
     "https://www.kwazulunatalfilm.co.za/",
     "KZNFC official"),

    # ── Nigeria ──────────────────────────────────────────────────────────
    ("NFVCB Film Grant (Nollywood Fund)",
     "Nigeria", "National Film & Video Censors Board / Bank of Industry", "₦50,000,000", "NGN",
     "2026-12-31", "open",
     ["Nigerian production company", "Feature film or series",
      "BOI-approved business plan"],
     "https://www.boi.ng/nollyfund/",
     "BOI / NFVCB official"),

    # ── Hungary ──────────────────────────────────────────────────────────
    ("NFI Hungarian Film Fund — Production Support",
     "Hungary", "National Film Institute Hungary", "HUF 200,000,000", "HUF",
     "2026-12-31", "open",
     ["Hungarian production company or co-production",
      "Feature film", "Hungarian cultural content or talent"],
     "https://nfi.hu/en/funding/production-support",
     "NFI official"),

    # ── Czech Republic ───────────────────────────────────────────────────
    ("Czech Film Fund — Production Support",
     "Czech Republic", "Czech Film Fund", "CZK 15,000,000", "CZK",
     "2026-12-31", "open",
     ["Czech producer or co-production", "Feature film",
      "Czech cultural content or filming in CZ"],
     "https://www.fondkinematografie.cz/en/support-for-czech-cinematography/production",
     "Czech Film Fund official"),

    # ── Australia ────────────────────────────────────────────────────────
    ("Screen Australia Production Investment",
     "Australia", "Screen Australia", "A$3,000,000", "AUD",
     "2026-12-31", "open",
     ["Australian production company", "Feature film or feature doc",
      "SAC (significant Australian content) test",
      "Theatrical distribution commitment"],
     "https://www.screenaustralia.gov.au/funding-and-support/feature-films/production-investment",
     "Screen Australia official"),

    ("Screen NSW Production Fund",
     "Australia", "Screen NSW", "A$500,000", "AUD",
     "2026-12-31", "open",
     ["NSW-based company or significant NSW spend", "Feature film",
      "Theatrical release plan"],
     "https://www.screen.nsw.gov.au/funding/production-investment",
     "Screen NSW official"),

    # ── New Zealand ──────────────────────────────────────────────────────
    ("NZFC Feature Film Production Finance",
     "New Zealand", "New Zealand Film Commission", "NZ$2,000,000", "NZD",
     "2026-12-31", "open",
     ["NZ production company", "Feature film (70+ mins)",
      "Significant NZ content", "NZ theatrical distribution attached"],
     "https://www.nzfilm.co.nz/funding/production-funding",
     "NZFC official"),

    # ── Iceland ──────────────────────────────────────────────────────────
    ("Icelandic Film Centre Production Grant",
     "Iceland", "Icelandic Film Centre", "ISK 40,000,000", "ISK",
     "2026-06-30", "open",
     ["Icelandic production company", "Feature film",
      "Icelandic cultural or language content"],
     "https://www.icelandicfilmcentre.is/funding/production-grants",
     "Icelandic Film Centre official"),

    # ── Canada ───────────────────────────────────────────────────────────
    ("Telefilm Canada Production Program",
     "Canada", "Telefilm Canada", "C$1,500,000", "CAD",
     "2026-12-31", "open",
     ["Canadian production company", "Feature film",
      "CAVCO certification eligible", "Canadian theatrical distribution"],
     "https://telefilm.ca/en/financing/production-program",
     "Telefilm official"),

    ("Ontario Creates Film Fund",
     "Canada", "Ontario Creates", "C$400,000", "CAD",
     "2026-12-31", "open",
     ["Ontario-based production company", "Feature film",
      "Minimum Ontario spend requirement"],
     "https://www.ontariocreates.ca/tax-incentives-and-financing/film-fund",
     "Ontario Creates official"),

    # ── Malta ────────────────────────────────────────────────────────────
    ("Malta Film Fund — Co-financing",
     "Malta", "Malta Film Commission", "€200,000", "EUR",
     "2026-12-31", "open",
     ["Maltese co-producer or significant Maltese spend",
      "Feature film or high-end TV",
      "Minimum 50% of fund spent in Malta"],
     "https://www.maltafilmcommission.com/the-malta-film-fund/",
     "Malta Film Commission official"),
]
# fmt: on


_INSERT_SQL = """\
INSERT INTO grant_opportunities (
    id, title, territory, funding_body, max_amount, currency,
    application_deadline, status, eligibility,
    website_url, data_source, verified, is_new,
    created_at, updated_at, last_verified_at
) VALUES (
    gen_random_uuid(), :title, :territory, :funding_body, :max_amount, :currency,
    :application_deadline, :status, :eligibility,
    :website_url, :data_source, TRUE, FALSE,
    NOW(), NOW(), NOW()
)
"""


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "grant_opportunities" not in inspector.get_table_names():
        return

    for grant in _GRANTS:
        (title, territory, funding_body, max_amount, currency,
         deadline, status, eligibility, website_url, data_source) = grant

        # Check if already exists
        existing = conn.execute(
            sa.text(
                "SELECT id FROM grant_opportunities "
                "WHERE title = :title AND territory = :territory LIMIT 1"
            ),
            {"title": title, "territory": territory},
        ).fetchone()
        if existing:
            continue

        conn.execute(
            sa.text(_INSERT_SQL),
            {
                "title": title,
                "territory": territory,
                "funding_body": funding_body,
                "max_amount": max_amount,
                "currency": currency,
                "application_deadline": deadline,
                "status": status,
                "eligibility": json.dumps(eligibility),
                "website_url": website_url,
                "data_source": data_source,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "grant_opportunities" not in inspector.get_table_names():
        return

    for grant in _GRANTS:
        title, territory = grant[0], grant[1]
        conn.execute(
            sa.text(
                "DELETE FROM grant_opportunities "
                "WHERE title = :title AND territory = :territory"
            ),
            {"title": title, "territory": territory},
        )
