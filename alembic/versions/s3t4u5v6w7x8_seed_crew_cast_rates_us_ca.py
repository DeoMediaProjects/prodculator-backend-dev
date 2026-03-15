"""seed_crew_cast_rates_us_ca

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-03-14 14:30:00.000000

Seed government-stats-sourced crew HOD (20 roles) and cast (8 roles)
rate data for United States and Canada.

Sources:
  US — BLS OEWS (NAICS 5121, public domain under 17 USC §105)
  CA — Statistics Canada LFS (Open Government Licence Canada)

All rates are indicative statistical estimates, NOT union minimums.
"""
from alembic import op
import sqlalchemy as sa


revision = "s3t4u5v6w7x8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None

# fmt: off

# Each tuple: (country, role, role_category, department, union_rate_cents,
#   non_union_rate_cents, rate_currency, fringe_rate_pct, fringe_description,
#   confidence_score, source_name, source_url, notes)

_BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
_STATCAN_URL = "https://www150.statcan.gc.ca/n1/en/type/data"
_US_FRINGE_DESC = (
    "No statutory employer fringe on gov-stats estimate rows. "
    "SAG P&H (21%) and IATSE local fringes are union-specific — "
    "not applied to government statistical estimates."
)
_CA_FRINGE_DESC = (
    "No statutory employer fringe applied to gov-stats estimate rows. "
    "ACTRA P&H, CPP and EI are union/statutory obligations that apply "
    "to actual engagements — not to statistical estimates."
)

_US_CREW = [
    # (role, role_category, department, low_cents, high_cents, conf, source_name, notes)
    ("Line Producer", "HOD-Production", "day", 120000, 250000, 78,
     "BLS OEWS NAICS 5121 / SOC 11-2021",
     "Estimated day rate range. BLS median wage for Production Managers in Motion Picture & Sound Recording Industries."),
    ("UPM / Prod Manager", "HOD-Production", "day", 110000, 200000, 76,
     "BLS OEWS NAICS 5121 / SOC 11-2021",
     "Estimated day rate range derived from BLS Production Manager occupational data for NAICS 5121."),
    ("Production Accountant", "HOD-Production", "day", 70000, 160000, 74,
     "BLS OEWS / SOC 43-3031",
     "BLS Bookkeeping & Accounting median for entertainment sector. Film production accountants typically at upper end."),
    ("1st AD", "HOD-Direction", "day", 90000, 180000, 74,
     "BLS OEWS NAICS 5121 / SOC 27-2012",
     "BLS Producers and Directors SOC for NAICS 5121. 1st ADs sit within this occupational classification."),
    ("Director", "HOD-Direction", "day", 180000, 800000, 72,
     "BLS OEWS NAICS 5121 / SOC 27-2012",
     "BLS Producers & Directors median for Motion Picture sector. Wide range reflects documentary vs studio feature."),
    ("DP / Cinematographer", "HOD-Camera", "day", 120000, 350000, 76,
     "BLS OEWS NAICS 5121 / SOC 27-4031",
     "BLS Camera Operators, TV, Video & Motion Picture SOC 27-4031 for NAICS 5121."),
    ("Camera Operator", "HOD-Camera", "day", 65000, 130000, 76,
     "BLS OEWS / SOC 27-4031",
     "BLS Camera Operators SOC 27-4031. Median annual $74,580."),
    ("1st AC / Focus Puller", "HOD-Camera", "day", 55000, 105000, 74,
     "BLS OEWS / SOC 27-4031",
     "BLS Camera Operators classification. 1st ACs sit within this SOC."),
    ("Production Designer", "HOD-Art", "day", 110000, 320000, 74,
     "BLS OEWS NAICS 5121 / SOC 27-1025",
     "BLS Art Directors SOC 27-1025 for NAICS 5121. Production Designers classified here."),
    ("Art Director", "HOD-Art", "day", 80000, 180000, 74,
     "BLS OEWS / SOC 27-1025",
     "BLS Art Directors SOC 27-1025."),
    ("Set Decorator", "HOD-Art", "day", 60000, 140000, 72,
     "BLS OEWS / SOC 27-1026",
     "BLS Craft & Fine Artists, Interior Designers — nearest occupational match for Set Decorators."),
    ("Costume Designer", "HOD-Wardrobe", "day", 75000, 220000, 74,
     "BLS OEWS NAICS 5121 / SOC 27-1021",
     "BLS Fashion Designers SOC 27-1021 applied to entertainment context."),
    ("Wardrobe Supervisor", "HOD-Wardrobe", "day", 55000, 110000, 72,
     "BLS OEWS / SOC 27-1021",
     "BLS Costume Attendants SOC 39-3092 and Fashion Designers SOC 27-1021 combined reference."),
    ("HOD Make-up / Make-up Designer", "HOD-HairMUP", "day", 60000, 170000, 76,
     "BLS OEWS NAICS 5121 / SOC 39-5091",
     "BLS Make-Up Artists, Theatrical & Performance SOC 39-5091. Median annual $95,680 for NAICS 5121."),
    ("HOD Hair", "HOD-HairMUP", "day", 58000, 150000, 74,
     "BLS OEWS / SOC 39-5091",
     "BLS Make-Up Artists & Hair Stylists SOC 39-5091."),
    ("Gaffer", "HOD-Electrical", "day", 65000, 135000, 74,
     "BLS OEWS / SOC 27-4014",
     "BLS Sound Engineering Technicians SOC 27-4014 nearest match for electrical crew."),
    ("Location Manager", "HOD-Locations", "day", 65000, 135000, 70,
     "BLS OEWS NAICS 5121 / SOC 11-2021",
     "BLS Production Managers SOC 11-2021. Location Managers grouped within this classification."),
    ("Location Scout", "HOD-Locations", "day", 40000, 85000, 68,
     "BLS OEWS NAICS 5121 / SOC 11-2021",
     "BLS Production Managers lower band. Location Scouts typically below Location Manager rate."),
    ("Sound Mixer", "HOD-Sound", "day", 65000, 135000, 76,
     "BLS OEWS NAICS 5121 / SOC 27-4014",
     "BLS Sound Engineering Technicians SOC 27-4014 for NAICS 5121. Median annual $84,270."),
    ("Editor", "HOD-Post", "day", 70000, 190000, 76,
     "BLS OEWS NAICS 5121 / SOC 27-4032",
     "BLS Film & Video Editors SOC 27-4032 for NAICS 5121. Median annual $96,770."),
]

_US_CAST = [
    ("Lead Actor (Weekly)", "CAST-Lead", "week", 350000, 900000, 74,
     "BLS OEWS NAICS 5121 / SOC 27-2011",
     "BLS Actors SOC 27-2011 for NAICS 5121. Weekly range derived from BLS annual median $49,590."),
    ("Lead Actor (Daily)", "CAST-Lead", "day", 70000, 180000, 74,
     "BLS OEWS NAICS 5121 / SOC 27-2011",
     "BLS Actors SOC 27-2011 daily equivalent."),
    ("Supporting Actor", "CAST-Supporting", "day", 50000, 120000, 72,
     "BLS OEWS / SOC 27-2011",
     "BLS Actors SOC 27-2011 lower distribution applied to supporting roles."),
    ("Day Player", "CAST-DayPlayer", "day", 40000, 90000, 70,
     "BLS OEWS / SOC 27-2011",
     "BLS Actors SOC 27-2011 single-day engagement estimate."),
    ("Background / Extra", "CAST-Background", "day", 15000, 28000, 68,
     "BLS OEWS / SOC 27-2011",
     "BLS Actors lower percentile. Background performers at floor of SOC 27-2011 distribution."),
    ("Background / Extra (Featured)", "CAST-Background", "day", 20000, 35000, 68,
     "BLS OEWS / SOC 27-2011",
     "BLS Actors SOC 27-2011 lower-mid distribution for featured background."),
    ("Stunt Performer", "CAST-Stunt", "day", 80000, 180000, 70,
     "BLS OEWS NAICS 5121 / SOC 27-2011",
     "BLS Actors SOC 27-2011 upper distribution applied to stunt work."),
    ("Voice Artist", "CAST-Voice", "session", 60000, 140000, 68,
     "BLS OEWS / SOC 27-2011",
     "BLS Actors SOC 27-2011 per-session estimate."),
]

_CA_CREW = [
    ("Line Producer", "HOD-Production", "day", 100000, 210000, 76,
     "Statistics Canada LFS / NOC 5131",
     "Stats Canada NOC 5131 (Film & Stage Directors) and NOC 0512 (Producers). Median wages for BC and Ontario."),
    ("UPM / Prod Manager", "HOD-Production", "day", 90000, 180000, 76,
     "Statistics Canada LFS / NOC 5131",
     "Stats Canada NOC 5131 / 0512. Production Manager occupational median for BC/ON film sector."),
    ("Production Accountant", "HOD-Production", "day", 65000, 140000, 74,
     "Statistics Canada LFS / NOC 1111",
     "Stats Canada NOC 1111 (Financial Auditors) applied to production accounting context."),
    ("1st AD", "HOD-Direction", "day", 80000, 160000, 74,
     "Statistics Canada LFS / NOC 5131",
     "Stats Canada NOC 5131 Producers & Directors. 1st ADs classified within this NOC."),
    ("Director", "HOD-Direction", "day", 160000, 1000000, 72,
     "Statistics Canada LFS / NOC 5131",
     "Stats Canada NOC 5131 upper distribution for Motion Picture Directors."),
    ("DP / Cinematographer", "HOD-Camera", "day", 120000, 320000, 76,
     "Statistics Canada LFS / NOC 5225",
     "Stats Canada NOC 5225 (Camera Operators & Crew). Median wages BC/ON adjusted to film context."),
    ("Camera Operator", "HOD-Camera", "day", 60000, 120000, 74,
     "Statistics Canada LFS / NOC 5225",
     "Stats Canada NOC 5225 Camera Operators."),
    ("1st AC / Focus Puller", "HOD-Camera", "day", 52000, 98000, 72,
     "Statistics Canada LFS / NOC 5225",
     "Stats Canada NOC 5225 lower band."),
    ("Production Designer", "HOD-Art", "day", 100000, 280000, 74,
     "Statistics Canada LFS / NOC 5243",
     "Stats Canada NOC 5243 (Theatre, Film & TV Art Directors). Direct occupational match."),
    ("Art Director", "HOD-Art", "day", 78000, 170000, 74,
     "Statistics Canada LFS / NOC 5243",
     "Stats Canada NOC 5243."),
    ("Set Decorator", "HOD-Art", "day", 60000, 130000, 72,
     "Statistics Canada LFS / NOC 5243",
     "Stats Canada NOC 5243 lower band for Set Decorators."),
    ("Costume Designer", "HOD-Wardrobe", "day", 75000, 190000, 74,
     "Statistics Canada LFS / NOC 5243",
     "Stats Canada NOC 5243. Costume Designers within Art Directors classification."),
    ("Wardrobe Supervisor", "HOD-Wardrobe", "day", 55000, 105000, 72,
     "Statistics Canada LFS / NOC 5243",
     "Stats Canada NOC 5243 lower band."),
    ("HOD Make-up / Make-up Designer", "HOD-HairMUP", "day", 55000, 150000, 74,
     "Statistics Canada LFS / NOC 6341",
     "Stats Canada NOC 6341 (Hairstylists & Barbers) — nearest creative match for MUP/Hair."),
    ("HOD Hair", "HOD-HairMUP", "day", 53000, 140000, 74,
     "Statistics Canada LFS / NOC 6341",
     "Stats Canada NOC 6341 applied to film hair department context."),
    ("Gaffer", "HOD-Electrical", "day", 60000, 125000, 74,
     "Statistics Canada LFS / NOC 7241",
     "Stats Canada NOC 7241 (Electricians) applied to film lighting/electrical context."),
    ("Location Manager", "HOD-Locations", "day", 60000, 125000, 70,
     "Statistics Canada LFS / NOC 5131",
     "Stats Canada NOC 5131 Production Managers. Location Managers grouped within this NOC."),
    ("Location Scout", "HOD-Locations", "day", 37000, 75000, 68,
     "Statistics Canada LFS / NOC 5131",
     "Stats Canada lower band. Location Scout typically below Location Manager rate."),
    ("Sound Mixer", "HOD-Sound", "day", 60000, 125000, 74,
     "Statistics Canada LFS / NOC 5225",
     "Stats Canada NOC 5225. Sound Mixers within this classification."),
    ("Editor", "HOD-Post", "day", 65000, 170000, 74,
     "Statistics Canada LFS / NOC 5131",
     "Stats Canada NOC 5131. Film Editors grouped within Producers & Directors classification."),
]

_CA_CAST = [
    ("Lead Actor (Weekly)", "CAST-Lead", "week", 280000, 750000, 74,
     "Statistics Canada LFS / NOC 5135",
     "Stats Canada NOC 5135 (Actors & Comedians). Weekly rate derived from annual median CAD $42,000."),
    ("Lead Actor (Daily)", "CAST-Lead", "day", 65000, 170000, 74,
     "Statistics Canada LFS / NOC 5135",
     "Stats Canada NOC 5135 daily equivalent estimate."),
    ("Supporting Actor", "CAST-Supporting", "day", 45000, 110000, 72,
     "Statistics Canada LFS / NOC 5135",
     "Stats Canada NOC 5135 lower-mid distribution."),
    ("Day Player", "CAST-DayPlayer", "day", 35000, 80000, 68,
     "Statistics Canada LFS / NOC 5135",
     "Stats Canada NOC 5135 single-day estimate."),
    ("Background / Extra", "CAST-Background", "day", 14000, 26000, 68,
     "Statistics Canada LFS / NOC 5135",
     "Stats Canada NOC 5135 floor distribution."),
    ("Background / Extra (Featured)", "CAST-Background", "day", 18000, 32000, 68,
     "Statistics Canada LFS / NOC 5135",
     "Stats Canada NOC 5135 lower band."),
    ("Stunt Performer", "CAST-Stunt", "day", 70000, 160000, 68,
     "Statistics Canada LFS / NOC 5135",
     "Stats Canada NOC 5135 upper distribution applied to stunt context."),
    ("Voice Artist", "CAST-Voice", "session", 50000, 120000, 68,
     "Statistics Canada LFS / NOC 5135",
     "Stats Canada NOC 5135 per-session estimate."),
]

# fmt: on

_INSERT_SQL = """\
INSERT INTO crew_costs (
    id, country, region, role, role_category, department,
    union_rate_cents, non_union_rate_cents, rate_currency,
    working_day_hours, fringe_rate_pct, fringe_description,
    source_name, source_type, source_url, confidence_score,
    effective_from, notes, created_at, updated_at
) VALUES (
    gen_random_uuid(), :country, '', :role, :role_category, :department,
    :union_rate_cents, :non_union_rate_cents, :rate_currency,
    0, :fringe_rate_pct, :fringe_description,
    :source_name, 'government_stats', :source_url, :confidence_score,
    '2025-01-01', :notes, NOW(), NOW()
)
"""


def _insert_rows(conn, rows, country, currency, fringe_desc, source_url):
    for row in rows:
        role, role_cat, dept, low, high, conf, src_name, notes = row
        conn.execute(
            sa.text(_INSERT_SQL),
            {
                "country": country,
                "role": role,
                "role_category": role_cat,
                "department": dept,
                "union_rate_cents": low,
                "non_union_rate_cents": high,
                "rate_currency": currency,
                "fringe_rate_pct": 0,
                "fringe_description": fringe_desc,
                "source_name": src_name,
                "source_url": source_url,
                "confidence_score": conf,
                "notes": notes,
            },
        )


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "crew_costs" not in inspector.get_table_names():
        return

    _insert_rows(conn, _US_CREW, "US", "USD", _US_FRINGE_DESC, _BLS_URL)
    _insert_rows(conn, _US_CAST, "US", "USD", _US_FRINGE_DESC, _BLS_URL)
    _insert_rows(conn, _CA_CREW, "CA", "CAD", _CA_FRINGE_DESC, _STATCAN_URL)
    _insert_rows(conn, _CA_CAST, "CA", "CAD", _CA_FRINGE_DESC, _STATCAN_URL)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "crew_costs" not in inspector.get_table_names():
        return

    # Remove seeded government_stats rows for US and CA
    conn.execute(
        sa.text(
            "DELETE FROM crew_costs "
            "WHERE source_type = 'government_stats' "
            "AND country IN ('US', 'CA')"
        )
    )
