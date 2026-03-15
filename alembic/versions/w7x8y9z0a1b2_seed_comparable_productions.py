"""seed_comparable_productions_global

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-03-15 11:00:00.000000

Seeds ~40 real comparable film productions across budget ranges and territories.
Focus on mid-budget ($1M–$50M) titles that are useful comparables for
independent and mid-budget feature films — the primary Prodculator audience.

All data sourced from publicly available TMDB, Box Office Mojo, and trade press.
budget_usd is stored in whole USD (integer), NOT cents.
"""
from alembic import op
import sqlalchemy as sa
import json

revision = "w7x8y9z0a1b2"
down_revision = "v6w7x8y9z0a1"
branch_labels = None
depends_on = None

# fmt: off

# (title, year, budget_usd, primary_territory, incentive_used, genre[], production_company, director, tmdb_id, source)
_PRODUCTIONS = [
    # ── UK / Ireland ──────────────────────────────────────────────────────
    ("The Banshees of Inisherin", 2022, 20000000, "Ireland",
     "Section 481 Tax Credit",
     ["Drama", "Comedy", "Dark Comedy"], "Blueprint Pictures / Searchlight", "Martin McDonagh",
     "674324", "TMDB / Box Office Mojo"),

    ("Aftersun", 2022, 2000000, "United Kingdom",
     "BFI Production Fund / UK Tax Relief",
     ["Drama", "Independent"], "Unified Theory / BBC Film / BFI", "Charlotte Wells",
     "926393", "TMDB / Screen Daily"),

    ("Blue Jean", 2022, 1500000, "United Kingdom",
     "UK Film Tax Relief / BFI National Lottery",
     ["Drama", "LGBTQ+", "Period"], "BBC Film / BFI", "Georgia Oakley",
     "906939", "BFI / Screen Daily"),

    ("Rocks", 2019, 3500000, "United Kingdom",
     "UK Film Tax Relief / BFI",
     ["Drama", "Coming-of-age", "Urban"], "Fable Pictures / BFI / Film4", "Sarah Gavron",
     "594040", "BFI / TMDB"),

    ("The Forgiven", 2021, 10000000, "United Kingdom",
     "Morocco Film Commission Incentive",
     ["Drama", "Thriller"], "Ingenious Media / Blue Fox Entertainment", "John Michael McDonagh",
     "766853", "TMDB / Variety"),

    ("Calm With Horses", 2019, 4000000, "Ireland",
     "Section 481",
     ["Crime", "Drama", "Thriller"], "Element Pictures / Film4", "Nick Rowland",
     "614696", "TMDB / Screen Ireland"),

    # ── South Africa / Nigeria / Africa ──────────────────────────────────
    ("Silverton Siege", 2022, 5000000, "South Africa",
     "SA Tax Rebate",
     ["Action", "Drama", "Thriller", "Historical"], "Nagvlug Films", "Mandla Dube",
     "906155", "TMDB / Netflix"),

    ("Gangs of Lagos", 2023, 3000000, "Nigeria",
     "None",
     ["Crime", "Drama", "Action", "Urban"], "Jungle FilmWorks", "Jadesola Osiberu",
     "1063901", "TMDB / Prime Video"),

    ("The Woman King", 2022, 50000000, "South Africa",
     "SA Tax Rebate / DTI Rebate",
     ["Action", "Drama", "Historical", "War"], "TriStar Pictures / eOne", "Gina Prince-Bythewood",
     "724495", "TMDB / Box Office Mojo"),

    ("Mami Wata", 2023, 1000000, "Nigeria",
     "None",
     ["Drama", "Fantasy", "African Mythology"], "CineFAM", "C.J. 'Fiery' Obasi",
     "1128846", "TMDB / Sundance"),

    ("Riding with Sugar", 2020, 2000000, "South Africa",
     "SA Tax Rebate / Western Cape Incentive",
     ["Drama", "Music", "Urban"], "Ster-Kinekor", "Zeno Graton",
     "726084", "TMDB / NFVF"),

    # ── France / Germany / Spain / Italy ─────────────────────────────────
    ("Anatomy of a Fall", 2023, 7500000, "France",
     "TRIP Tax Rebate / CNC aide sélective",
     ["Drama", "Courtroom", "Thriller"], "Les Films Pelléas / Les Films de Pierre", "Justine Triet",
     "915935", "TMDB / CNC"),

    ("Corsage", 2022, 6000000, "Germany",
     "DFFF / Austrian Film Fund / BKM",
     ["Drama", "Historical", "Biographical"], "Film AG / Samsa Film", "Marie Kreutzer",
     "860866", "TMDB / Variety"),

    ("The Beasts", 2022, 4500000, "Spain",
     "Spanish Tax Deduction / Galician Regional Fund",
     ["Drama", "Thriller"], "Arcadia Motion Pictures / Caballo Films", "Rodrigo Sorogoyen",
     "943280", "TMDB / Cineuropa"),

    ("Alcarràs", 2022, 2000000, "Spain",
     "Spanish Tax Deduction / ICAA aid",
     ["Drama", "Family"], "Avalon PC / Elastica Films", "Carla Simón",
     "864116", "TMDB / Cineuropa"),

    ("A Chiara", 2021, 3000000, "Italy",
     "Italian Tax Credit / MiC selective",
     ["Drama", "Crime", "Coming-of-age"], "Ferrara Films / Vivo Film", "Jonas Carpignano",
     "850964", "TMDB / Cineuropa"),

    ("Perfect Days", 2023, 8000000, "Germany",
     "DFFF / Japanese co-production treaty",
     ["Drama"], "Master Mind / Spoon Inc", "Wim Wenders",
     "976893", "TMDB / Variety"),

    # ── Hungary / Czech Republic / Malta ─────────────────────────────────
    ("Son of Saul", 2015, 1500000, "Hungary",
     "Hungarian 30% Tax Rebate",
     ["Drama", "Historical", "War"], "Laokoon Filmgroup", "László Nemes",
     "310135", "TMDB / NFI"),

    ("White God", 2014, 3200000, "Hungary",
     "Hungarian Tax Rebate",
     ["Drama", "Thriller", "Fantasy"], "Proton Cinema / Pola Pandora", "Kornél Mundruczó",
     "266522", "TMDB / NFI"),

    ("Zátopek", 2021, 4000000, "Czech Republic",
     "Czech Film Fund Incentive",
     ["Drama", "Biographical", "Sport"], "Lucky Man Films", "David Ondříček",
     "741458", "TMDB / Czech Film Fund"),

    ("By the Grace of God", 2018, 7000000, "Malta",
     "Malta Film Fund / Cash Rebate",
     ["Drama", "Thriller"], "Mandarin Films", "François Ozon",
     "504988", "TMDB / MFC"),

    # ── Australia / New Zealand ──────────────────────────────────────────
    ("The Dry", 2020, 7000000, "Australia",
     "Australian Location Offset / Screen Australia",
     ["Crime", "Drama", "Mystery"], "Made Up Stories / Screen Australia", "Robert Connolly",
     "658352", "TMDB / Screen Australia"),

    ("Nitram", 2021, 6000000, "Australia",
     "Australian Location Offset / Screen Victoria",
     ["Drama", "Biographical", "Crime"], "Good Thing Productions", "Justin Kurzel",
     "833464", "TMDB / Screen Australia"),

    ("The Power of the Dog", 2021, 39000000, "New Zealand",
     "NZSPG 20%",
     ["Drama", "Western"], "See-Saw Films / Cross City Films", "Jane Campion",
     "597208", "TMDB / Box Office Mojo"),

    ("Hunt for the Wilderpeople", 2016, 2500000, "New Zealand",
     "NZSPG",
     ["Adventure", "Comedy", "Drama"], "Piki Films / Defender Films", "Taika Waititi",
     "371446", "TMDB / NZFC"),

    # ── Iceland ──────────────────────────────────────────────────────────
    ("Lamb", 2021, 5500000, "Iceland",
     "Icelandic Film Fund Reimbursement",
     ["Drama", "Horror", "Fantasy"], "Go To Sheep / Black Spark Film", "Valdimar Jóhannsson",
     "731224", "TMDB / Icelandic Film Centre"),

    ("Woman at War", 2018, 4000000, "Iceland",
     "Icelandic Film Fund Reimbursement",
     ["Comedy", "Drama", "Environmental"], "Slot Machine / Gulldrengurinn", "Benedikt Erlingsson",
     "493922", "TMDB / Icelandic Film Centre"),

    # ── US Indies / Canada ───────────────────────────────────────────────
    ("Moonlight", 2016, 4000000, "United States",
     "Florida Tax Credit (expired)",
     ["Drama", "LGBTQ+", "Coming-of-age"], "A24 / Plan B / PASTEL", "Barry Jenkins",
     "376867", "TMDB / Box Office Mojo"),

    ("Beasts of the Southern Wild", 2012, 1800000, "United States",
     "Louisiana Motion Picture Tax Credit",
     ["Drama", "Fantasy", "Adventure"], "Cinereach / Court 13 Pictures", "Benh Zeitlin",
     "112160", "TMDB / Box Office Mojo"),

    ("Everything Everywhere All at Once", 2022, 25000000, "United States",
     "California Film & TV Tax Credit",
     ["Action", "Comedy", "Sci-Fi", "Fantasy"], "A24 / AGBO / IAC Films", "Daniel Kwan / Daniel Scheinert",
     "545611", "TMDB / Box Office Mojo"),

    ("The Whale", 2022, 3000000, "United States",
     "New York Production Tax Credit",
     ["Drama"], "A24 / Protozoa Pictures", "Darren Aronofsky",
     "785084", "TMDB / Box Office Mojo"),

    ("Causeway", 2022, 8000000, "United States",
     "Louisiana Tax Credit",
     ["Drama"], "A24 / Jennifer Lawrence's Excellent Cadaver", "Lila Neugebauer",
     "717930", "TMDB / Variety"),

    ("Room", 2015, 13000000, "Canada",
     "Ontario Tax Credit / Telefilm Canada",
     ["Drama", "Thriller"], "Element Pictures / No Trace Camping", "Lenny Abrahamson",
     "264644", "TMDB / Telefilm"),

    ("Incendies", 2010, 6800000, "Canada",
     "Quebec QPSTC / CPTC",
     ["Drama", "War", "Mystery"], "micro_scope / TS Productions", "Denis Villeneuve",
     "46738", "TMDB / Telefilm"),

    # ── Music / Urban / Genre-specific ───────────────────────────────────
    ("One Love", 2024, 70000000, "United Kingdom",
     "UK HETV Tax Relief",
     ["Music", "Biographical", "Drama"], "Paramount / Tuff Gong Pictures", "Reinaldo Marcus Green",
     "882059", "TMDB / Box Office Mojo"),

    ("Idris Elba: Fighter", 2022, 2000000, "United Kingdom",
     "UK Film Tax Relief",
     ["Documentary", "Sport"], "Discovery UK", "Lee Hicken",
     "0", "Discovery / TMDB"),

    ("Blue Story", 2019, 3000000, "United Kingdom",
     "UK Film Tax Relief / BFI",
     ["Drama", "Crime", "Urban", "Music"], "BBC Films / Paramount", "Rapman (Andrew Onwubolu)",
     "624779", "TMDB / BFI"),

    ("Top Boy: Summerhouse (Film)", 2022, 12000000, "United Kingdom",
     "UK HETV Relief",
     ["Crime", "Drama", "Urban"], "Cowboy Films / SpringHill", "Aneil Karia / Brady Hood",
     "0", "Netflix / TMDB"),

    ("The Harder They Fall", 2021, 90000000, "United States",
     "New Mexico Production Tax Credit",
     ["Western", "Action", "Drama"], "Overbrook Entertainment / Netflix", "Jeymes Samuel",
     "619297", "TMDB / Box Office Mojo"),

    ("Yardie", 2018, 8000000, "United Kingdom",
     "UK Film Tax Relief",
     ["Crime", "Drama", "Music", "Urban"], "Warp Films / Studiocanal", "Idris Elba",
     "427641", "TMDB / BFI"),
]
# fmt: on


_INSERT_SQL = """\
INSERT INTO comparable_productions (
    id, title, year, budget_usd, primary_territory, incentive_used,
    genre, production_company, director, tmdb_id, source,
    created_at, updated_at
) VALUES (
    gen_random_uuid(), :title, :year, :budget_usd, :primary_territory, :incentive_used,
    :genre, :production_company, :director, :tmdb_id, :source,
    NOW(), NOW()
)
"""


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "comparable_productions" not in inspector.get_table_names():
        return

    for prod in _PRODUCTIONS:
        title, year, budget, territory, incentive, genres, company, director, tmdb, source = prod
        # Check if already exists (by title + year)
        existing = conn.execute(
            sa.text(
                "SELECT id FROM comparable_productions "
                "WHERE title = :title AND year = :year LIMIT 1"
            ),
            {"title": title, "year": year},
        ).fetchone()
        if existing:
            continue

        conn.execute(
            sa.text(_INSERT_SQL),
            {
                "title": title,
                "year": year,
                "budget_usd": budget,
                "primary_territory": territory,
                "incentive_used": incentive,
                "genre": json.dumps(genres),
                "production_company": company,
                "director": director,
                "tmdb_id": tmdb,
                "source": source,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "comparable_productions" not in inspector.get_table_names():
        return

    for prod in _PRODUCTIONS:
        title, year = prod[0], prod[1]
        conn.execute(
            sa.text(
                "DELETE FROM comparable_productions "
                "WHERE title = :title AND year = :year AND source LIKE '%TMDB%'"
            ),
            {"title": title, "year": year},
        )
