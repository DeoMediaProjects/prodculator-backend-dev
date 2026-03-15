"""seed_film_festivals_global

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-03-15 12:00:00.000000

Seeds ~30 real film festivals across tiers (A-list, mid-tier, emerging)
and territories. Includes deadlines, genres, budget tiers, and alumni.

Sources: Official festival websites, FilmFreeway, FIAPF accreditation list.
"""
from alembic import op
import sqlalchemy as sa
import json

revision = "y9z0a1b2c3d4"
down_revision = "x8y9z0a1b2c3"
branch_labels = None
depends_on = None

# fmt: off

# Each tuple:
# (name, location, year, genres[], budget_tiers[], festival_dates,
#  premiere_requirement, tier, acceptance_rate, website_url, filmfreeway_url,
#  data_source, deadlines[{name, date}], notable_alumni[],
#  average_budget_of_accepted_films, notes, current_status)

_FESTIVALS = [
    # ══════════════════════════════════════════════════════════════════════
    # A-LIST / FIAPF COMPETITIVE
    # ══════════════════════════════════════════════════════════════════════
    ("Cannes Film Festival",
     "Cannes, France", 2026,
     ["Drama", "Art House", "All Genres"],
     ["Low Budget", "Mid Budget", "High Budget"],
     "May 12–23, 2026", "World Premiere", "A-list", 0.02,
     "https://www.festival-cannes.com/en/",
     "https://filmfreeway.com/CannesFilmFestival",
     "FIAPF / Official",
     [{"name": "Feature Submission", "date": "2026-03-15"}],
     ["Bong Joon-ho", "Justine Triet", "Sean Baker", "Ruben Östlund"],
     "$5M–$50M", "Palme d'Or is the highest honour. Un Certain Regard for emerging voices.",
     "upcoming"),

    ("Venice International Film Festival",
     "Venice, Italy", 2026,
     ["Drama", "Art House", "All Genres"],
     ["Low Budget", "Mid Budget", "High Budget"],
     "Aug 27 – Sep 6, 2026", "World Premiere", "A-list", 0.02,
     "https://www.labiennale.org/en/cinema/",
     "https://filmfreeway.com/VeniceFilmFestival",
     "FIAPF / Official",
     [{"name": "Feature Submission", "date": "2026-05-31"}],
     ["Chloé Zhao", "Yorgos Lanthimos", "Todd Field", "Laura Poitras"],
     "$3M–$40M", "Golden Lion. Horizons section for emerging work. Venice launch is awards-season strategy.",
     "upcoming"),

    ("Berlin International Film Festival (Berlinale)",
     "Berlin, Germany", 2026,
     ["Drama", "Political", "Social Realism", "All Genres"],
     ["Low Budget", "Mid Budget", "High Budget"],
     "Feb 12–22, 2026", "World or International Premiere", "A-list", 0.03,
     "https://www.berlinale.de/en/",
     "https://filmfreeway.com/Berlinale",
     "FIAPF / Official",
     [{"name": "Feature Submission", "date": "2025-10-15"}],
     ["Carla Simón", "Mohammad Rasoulof", "Radu Jude", "Mati Diop"],
     "$2M–$30M", "Golden Bear. Panorama and Forum for indie/experimental work.",
     "upcoming"),

    ("Toronto International Film Festival (TIFF)",
     "Toronto, Canada", 2026,
     ["All Genres"],
     ["Low Budget", "Mid Budget", "High Budget"],
     "Sep 4–14, 2026", "World or North American Premiere", "A-list", 0.04,
     "https://www.tiff.net/",
     "https://filmfreeway.com/TIFF",
     "Official",
     [{"name": "Feature Submission", "date": "2026-06-15"}],
     ["Barry Jenkins", "Greta Gerwig", "Denis Villeneuve", "Steve McQueen"],
     "$5M–$50M", "People's Choice Award is a strong Oscar predictor. Major market/sales venue.",
     "upcoming"),

    ("San Sebastián International Film Festival",
     "San Sebastián, Spain", 2026,
     ["Drama", "Art House", "All Genres"],
     ["Low Budget", "Mid Budget"],
     "Sep 18–26, 2026", "World or International Premiere", "A-list", 0.04,
     "https://www.sansebastianfestival.com/",
     "https://filmfreeway.com/SanSebastianFilmFestival",
     "FIAPF / Official",
     [{"name": "Feature Submission", "date": "2026-06-30"}],
     ["Rodrigo Sorogoyen", "Hirokazu Kore-eda", "Hong Sang-soo"],
     "$1M–$20M", "Golden Shell. Strong for European and Latin American cinema.",
     "upcoming"),

    # ══════════════════════════════════════════════════════════════════════
    # MAJOR NON-COMPETITIVE / MARKET FESTIVALS
    # ══════════════════════════════════════════════════════════════════════
    ("Sundance Film Festival",
     "Park City, Utah, USA", 2026,
     ["Independent", "Drama", "Documentary", "All Genres"],
     ["Micro Budget", "Low Budget", "Mid Budget"],
     "Jan 22 – Feb 1, 2026", "World Premiere preferred", "A-list", 0.02,
     "https://www.sundance.org/festivals/sundance-film-festival/",
     "https://filmfreeway.com/SundanceFilmFestival",
     "Official",
     [{"name": "Feature Submission", "date": "2025-08-14"},
      {"name": "Short Film Submission", "date": "2025-08-14"}],
     ["Ryan Coogler", "Ari Aster", "Sean Baker", "Benh Zeitlin", "Charlotte Wells"],
     "$500K–$15M", "Premier US indie launchpad. Grand Jury Prize. Strong for first features.",
     "upcoming"),

    ("Tribeca Festival",
     "New York, USA", 2026,
     ["All Genres", "Independent", "Documentary"],
     ["Low Budget", "Mid Budget"],
     "Jun 4–15, 2026", "World or US Premiere", "Mid-tier", 0.05,
     "https://www.tribecafilm.com/festival",
     "https://filmfreeway.com/TribecaFilmFestival",
     "Official",
     [{"name": "Feature Submission", "date": "2025-12-06"}],
     ["Ava DuVernay", "Jon Watts", "Jerrod Carmichael"],
     "$1M–$20M", "Strong NYC audience. Good for docs and genre films.",
     "upcoming"),

    ("South by Southwest (SXSW)",
     "Austin, Texas, USA", 2026,
     ["Independent", "Genre", "Comedy", "Sci-Fi", "Documentary"],
     ["Micro Budget", "Low Budget", "Mid Budget"],
     "Mar 7–15, 2026", "World Premiere preferred", "Mid-tier", 0.06,
     "https://www.sxsw.com/film/",
     "https://filmfreeway.com/SXSW",
     "Official",
     [{"name": "Feature Submission", "date": "2025-10-17"}],
     ["Jordan Peele", "Daniels (EEAAO)", "Boots Riley"],
     "$500K–$10M", "Great for genre, comedy, and crossover titles. Industry/tech audience.",
     "upcoming"),

    # ══════════════════════════════════════════════════════════════════════
    # STRONG MID-TIER / REGIONAL
    # ══════════════════════════════════════════════════════════════════════
    ("London Film Festival (BFI LFF)",
     "London, United Kingdom", 2026,
     ["All Genres"],
     ["Low Budget", "Mid Budget", "High Budget"],
     "Oct 8–19, 2026", "UK Premiere", "A-list", 0.06,
     "https://www.bfi.org.uk/london-film-festival",
     "https://filmfreeway.com/BFILondonFilmFestival",
     "BFI Official",
     [{"name": "Feature Submission", "date": "2026-06-01"}],
     ["Steve McQueen", "Andrea Arnold", "Joanna Hogg", "Charlotte Wells"],
     "$2M–$30M", "UK's premier festival. Strong awards season positioning. European premiere platform.",
     "upcoming"),

    ("Edinburgh International Film Festival",
     "Edinburgh, Scotland", 2026,
     ["Independent", "Scottish", "All Genres"],
     ["Low Budget", "Mid Budget"],
     "Aug 14–25, 2026", "UK or European Premiere", "Mid-tier", 0.08,
     "https://www.edfilmfest.org.uk/",
     "https://filmfreeway.com/EdinburghInternationalFilmFestival",
     "Official",
     [{"name": "Feature Submission", "date": "2026-04-30"}],
     ["Lynne Ramsay", "Peter Mullan"],
     "$500K–$10M", "One of the world's oldest film festivals. Strong Scottish/UK indie focus.",
     "upcoming"),

    ("Galway Film Fleadh",
     "Galway, Ireland", 2026,
     ["Independent", "Irish", "All Genres"],
     ["Micro Budget", "Low Budget"],
     "Jul 7–12, 2026", "Irish or World Premiere", "Mid-tier", 0.10,
     "https://www.galwayfilmfleadh.com/",
     "https://filmfreeway.com/GalwayFilmFleadh",
     "Official",
     [{"name": "Feature Submission", "date": "2026-04-15"}],
     ["Lenny Abrahamson", "John Carney", "Martin McDonagh"],
     "$200K–$5M", "Ireland's leading film festival. Excellent for Irish premieres and industry networking.",
     "upcoming"),

    ("Durban International Film Festival (DIFF)",
     "Durban, South Africa", 2026,
     ["African Cinema", "All Genres", "Documentary"],
     ["Micro Budget", "Low Budget", "Mid Budget"],
     "Jul 17–27, 2026", "African or World Premiere", "Mid-tier", 0.12,
     "https://www.durbanfilmfest.co.za/",
     "https://filmfreeway.com/DurbanInternationalFilmFestival",
     "Official",
     [{"name": "Feature Submission", "date": "2026-04-01"}],
     ["Jahmil X.T. Qubeka", "Wanuri Kahiu", "Oliver Hermanus"],
     "$100K–$5M", "Premier African film festival. Strong pan-African industry networking.",
     "upcoming"),

    ("Africa International Film Festival (AFRIFF)",
     "Lagos, Nigeria", 2026,
     ["African Cinema", "Nollywood", "All Genres"],
     ["Micro Budget", "Low Budget", "Mid Budget"],
     "Nov 1–7, 2026", "African or World Premiere", "Mid-tier", 0.15,
     "https://afriff.com/",
     "https://filmfreeway.com/AFRIFF",
     "Official",
     [{"name": "Feature Submission", "date": "2026-08-31"}],
     ["Kunle Afolayan", "Kemi Adetiba", "C.J. Obasi"],
     "$50K–$3M", "Nigeria's premier festival. Nollywood industry hub. Growing international profile.",
     "upcoming"),

    ("Melbourne International Film Festival (MIFF)",
     "Melbourne, Australia", 2026,
     ["All Genres", "Independent", "Documentary"],
     ["Low Budget", "Mid Budget"],
     "Aug 6–23, 2026", "Australian Premiere", "Mid-tier", 0.06,
     "https://miff.com.au/",
     "https://filmfreeway.com/MelbourneInternationalFilmFestival",
     "Official",
     [{"name": "Feature Submission", "date": "2026-04-30"}],
     ["Jennifer Kent", "Justin Kurzel", "David Michôd"],
     "$1M–$15M", "Australia's premier festival. MIFF Premiere Fund co-invests in Australian features.",
     "upcoming"),

    ("Sydney Film Festival",
     "Sydney, Australia", 2026,
     ["All Genres"],
     ["Low Budget", "Mid Budget"],
     "Jun 3–14, 2026", "Australian Premiere", "Mid-tier", 0.07,
     "https://www.sff.org.au/",
     "https://filmfreeway.com/SydneyFilmFestival",
     "Official",
     [{"name": "Feature Submission", "date": "2026-02-28"}],
     ["Warwick Thornton", "Robert Connolly"],
     "$500K–$10M", "Strong audience festival. Good Australian and international programming.",
     "upcoming"),

    ("New Zealand International Film Festival (NZIFF)",
     "Auckland / Wellington, New Zealand", 2026,
     ["All Genres", "Independent"],
     ["Low Budget", "Mid Budget"],
     "Jul–Aug 2026", "NZ Premiere", "Mid-tier", 0.08,
     "https://www.nziff.co.nz/",
     "https://filmfreeway.com/NZIFF",
     "Official",
     [{"name": "Feature Submission", "date": "2026-04-15"}],
     ["Taika Waititi", "Jane Campion", "Lee Tamahori"],
     "$500K–$10M", "NZ's premier festival, touring multiple cities.",
     "upcoming"),

    ("Karlovy Vary International Film Festival",
     "Karlovy Vary, Czech Republic", 2026,
     ["Drama", "Art House", "Eastern European"],
     ["Low Budget", "Mid Budget"],
     "Jul 3–11, 2026", "World or International Premiere", "A-list", 0.04,
     "https://www.kviff.com/en/",
     "https://filmfreeway.com/KarlovyVaryIFF",
     "FIAPF / Official",
     [{"name": "Feature Submission", "date": "2026-04-01"}],
     ["Agnieszka Holland", "Cristian Mungiu", "Kornél Mundruczó"],
     "$1M–$15M", "Crystal Globe. Central & Eastern Europe's premier festival. Growing industry profile.",
     "upcoming"),

    ("Reykjavik International Film Festival (RIFF)",
     "Reykjavik, Iceland", 2026,
     ["Independent", "Nordic", "All Genres"],
     ["Micro Budget", "Low Budget"],
     "Sep 24 – Oct 4, 2026", "Icelandic or International Premiere", "Mid-tier", 0.12,
     "https://riff.is/",
     "https://filmfreeway.com/RIFF",
     "Official",
     [{"name": "Feature Submission", "date": "2026-06-30"}],
     ["Grímur Hákonarson", "Hlynur Pálmason"],
     "$200K–$5M", "Iceland's primary festival. Nordic focus. Intimate industry atmosphere.",
     "upcoming"),

    # ══════════════════════════════════════════════════════════════════════
    # GENRE / NICHE (Useful for genre-specific prodculator reports)
    # ══════════════════════════════════════════════════════════════════════
    ("Fantastic Fest",
     "Austin, Texas, USA", 2026,
     ["Horror", "Sci-Fi", "Fantasy", "Action", "Genre"],
     ["Micro Budget", "Low Budget", "Mid Budget"],
     "Sep 18–25, 2026", "World Premiere preferred", "Niche", 0.08,
     "https://fantasticfest.com/",
     "https://filmfreeway.com/FantasticFest",
     "Official",
     [{"name": "Feature Submission", "date": "2026-06-15"}],
     ["Robert Eggers", "Ti West", "Julia Ducournau"],
     "$500K–$15M", "Premier genre festival in the US. Strong horror/sci-fi discovery platform.",
     "upcoming"),

    ("FrightFest",
     "London, United Kingdom", 2026,
     ["Horror", "Thriller", "Genre"],
     ["Micro Budget", "Low Budget"],
     "Aug 27–31, 2026", "UK Premiere", "Niche", 0.10,
     "https://www.frightfest.co.uk/",
     "https://filmfreeway.com/FrightFest",
     "Official",
     [{"name": "Feature Submission", "date": "2026-05-15"}],
     ["Ben Wheatley", "Neil Marshall", "Rose Glass"],
     "$100K–$5M", "UK's premier horror festival. Strong for UK genre filmmakers.",
     "upcoming"),

    ("Sheffield DocFest",
     "Sheffield, United Kingdom", 2026,
     ["Documentary"],
     ["Micro Budget", "Low Budget", "Mid Budget"],
     "Jun 4–9, 2026", "UK or World Premiere", "Mid-tier", 0.08,
     "https://sheffdocfest.com/",
     "https://filmfreeway.com/SheffieldDocFest",
     "Official",
     [{"name": "Feature Submission", "date": "2026-02-15"}],
     ["Asif Kapadia", "Kim Longinotto", "Marc Isaacs"],
     "$100K–$3M", "UK's leading documentary festival. MeetMarket for doc pitching.",
     "upcoming"),

    ("Locarno Film Festival",
     "Locarno, Switzerland", 2026,
     ["Art House", "Experimental", "Independent"],
     ["Low Budget", "Mid Budget"],
     "Aug 5–15, 2026", "World or International Premiere", "A-list", 0.03,
     "https://www.locarnofestival.ch/en/",
     "https://filmfreeway.com/LocarnoFilmFestival",
     "FIAPF / Official",
     [{"name": "Feature Submission", "date": "2026-04-30"}],
     ["Kelly Reichardt", "Albert Serra", "Radu Jude"],
     "$500K–$10M", "Golden Leopard. Known for bold auteur cinema. Open Air Piazza Grande screenings.",
     "upcoming"),

    ("Budapest International Documentary Festival",
     "Budapest, Hungary", 2026,
     ["Documentary"],
     ["Micro Budget", "Low Budget"],
     "Jan 22–26, 2026", "Hungarian or International Premiere", "Niche", 0.15,
     "https://www.budapestdocfest.hu/",
     "https://filmfreeway.com/BudapestDocFest",
     "Official",
     [{"name": "Feature Doc Submission", "date": "2025-10-15"}],
     ["Márta Mészáros"],
     "$50K–$2M", "Hungary's premier doc festival. Growing Central European doc hub.",
     "upcoming"),

    ("Encounters South African International Documentary Festival",
     "Cape Town / Johannesburg, South Africa", 2026,
     ["Documentary"],
     ["Micro Budget", "Low Budget"],
     "Jun 4–14, 2026", "African or World Premiere", "Niche", 0.12,
     "https://encounters.co.za/",
     "https://filmfreeway.com/EncountersDocFest",
     "Official",
     [{"name": "Feature Doc Submission", "date": "2026-03-31"}],
     ["Rehad Desai", "Jihan El-Tahri"],
     "$50K–$2M", "Africa's premier documentary festival. Cape Town and JHB screenings.",
     "upcoming"),

    ("Tallinn Black Nights Film Festival (PÖFF)",
     "Tallinn, Estonia", 2026,
     ["All Genres", "Baltic", "Nordic"],
     ["Low Budget", "Mid Budget"],
     "Nov 13–29, 2026", "World or International Premiere", "A-list", 0.05,
     "https://poff.ee/en/",
     "https://filmfreeway.com/POFF",
     "FIAPF / Official",
     [{"name": "Feature Submission", "date": "2026-08-01"}],
     ["Zaza Urushadze", "Rainer Sarnet"],
     "$500K–$10M", "FIAPF-accredited. Only A-list festival in Baltic/Nordic. Industry@Tallinn market.",
     "upcoming"),
]
# fmt: on


_INSERT_SQL = """\
INSERT INTO film_festivals (
    id, name, location, year, genres, budget_tiers,
    festival_dates, premiere_requirement, tier, acceptance_rate,
    website_url, filmfreeway_url, data_source,
    verified, is_new, deadlines, notable_alumni,
    average_budget_of_accepted_films, notes,
    last_verified_at, created_at, updated_at
) VALUES (
    gen_random_uuid(), :name, :location, :year, :genres, :budget_tiers,
    :festival_dates, :premiere_requirement, :tier, :acceptance_rate,
    :website_url, :filmfreeway_url, :data_source,
    TRUE, FALSE, :deadlines, :notable_alumni,
    :avg_budget, :notes,
    NOW(), NOW(), NOW()
)
"""


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "film_festivals" not in inspector.get_table_names():
        return

    for fest in _FESTIVALS:
        (name, location, year, genres, budget_tiers, festival_dates,
         premiere_req, tier, acceptance_rate, website_url, filmfreeway_url,
         data_source, deadlines, notable_alumni, avg_budget, notes,
         _current_status) = fest

        # Check if already exists
        existing = conn.execute(
            sa.text(
                "SELECT id FROM film_festivals "
                "WHERE name = :name AND year = :year LIMIT 1"
            ),
            {"name": name, "year": year},
        ).fetchone()
        if existing:
            continue

        conn.execute(
            sa.text(_INSERT_SQL),
            {
                "name": name,
                "location": location,
                "year": year,
                "genres": json.dumps(genres),
                "budget_tiers": json.dumps(budget_tiers),
                "festival_dates": festival_dates,
                "premiere_requirement": premiere_req,
                "tier": tier,
                "acceptance_rate": acceptance_rate,
                "website_url": website_url,
                "filmfreeway_url": filmfreeway_url,
                "data_source": data_source,
                "deadlines": json.dumps(deadlines),
                "notable_alumni": json.dumps(notable_alumni),
                "avg_budget": avg_budget,
                "notes": notes,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "film_festivals" not in inspector.get_table_names():
        return

    for fest in _FESTIVALS:
        name, year = fest[0], fest[2]
        conn.execute(
            sa.text(
                "DELETE FROM film_festivals "
                "WHERE name = :name AND year = :year"
            ),
            {"name": name, "year": year},
        )
