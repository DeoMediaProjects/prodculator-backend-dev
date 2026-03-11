"""add_new_scrape_sources

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-03-10 14:00:00.000000

1. Add use_rest_api, api_slug, source_authority columns to scrape_sources.
2. Insert new source records for SA, Nigeria, IFTC, BECTU branches,
   REST API sources (Eurostat, OECD, FRED, StatCan, ONS), and supplementary
   incentive sources. Skips rows that already exist by URL.
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "j4k5l6m7n8o9"
down_revision = "i3j4k5l6m7n8"
branch_labels = None
depends_on = None

# New columns to add to scrape_sources
_NEW_COLUMNS = [
    ("use_rest_api", sa.Boolean(), False),
    ("api_slug", sa.Text(), None),
    ("source_authority", sa.Text(), None),
]

# New source records to insert (skip if url already exists)
_NEW_SOURCES = [
    # ── UK — additional incentive sources ────────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.gov.uk/government/publications/uk-independent-film-tax-credit",
        "label": "GOV.UK IFTC Policy Note",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "government_incentive",
    },
    {
        "resource_type": "incentives",
        "url": "https://ep.com/blog/uk-independent-film-tax-credit-approved-key-updates-for-producers/",
        "label": "Entertainment Partners IFTC Guide",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "government_incentive",
    },
    {
        "resource_type": "incentives",
        "url": "https://britishfilmcommission.org.uk/plan-your-production/accessing-uk-tax-reliefs/",
        "label": "British Film Commission Tax Reliefs",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "government_incentive",
    },
    # ── Hungary — supplementary incentive sources ─────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://hungariantaxcredit.com",
        "label": "Hungarian Tax Credit Guide",
        "territory": "Hungary",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "government_incentive",
    },
    {
        "resource_type": "incentives",
        "url": "https://focal.ch/prodvalue",
        "label": "Focal PV Hungary Working Conditions",
        "territory": "Hungary",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "government_incentive",
    },
    # ── Malta — supplementary incentive PDF ───────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://screenmalta.com",
        "label": "Screen Malta Incentives Guidelines 2024",
        "territory": "Malta",
        "is_pdf": True,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "government_incentive",
    },
    # ── South Africa — NEW territory incentives ───────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.thedtic.gov.za",
        "label": "DTIC Foreign Film Incentive (South Africa)",
        "territory": "South Africa",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "government_incentive",
    },
    # ── Nigeria — NEW territory incentives ───────────────────────────────────
    {
        "resource_type": "incentives",
        "url": "https://www.nfc.gov.ng",
        "label": "Nigerian Film Corporation",
        "territory": "Nigeria",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "government_incentive",
    },
    # ── UK — BECTU branch rate cards ──────────────────────────────────────────
    {
        "resource_type": "crew_costs",
        "url": "https://bectuartdepartment.co.uk/Rate-Card",
        "label": "BECTU Art Department Rate Card 2025-26 — Film industry rate card",
        "territory": "United Kingdom",
        "is_pdf": True,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "film_specific",
    },
    {
        "resource_type": "crew_costs",
        "url": "https://camerabranch.org.uk/rates/",
        "label": "BECTU Camera Branch Rate Card 2025 — Film industry rate card",
        "territory": "United Kingdom",
        "is_pdf": True,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "film_specific",
    },
    # ── UK — ONS REST API ─────────────────────────────────────────────────────
    {
        "resource_type": "crew_costs",
        "url": "https://api.ons.gov.uk/v1/datasets/ashe-table-7/timeseries/KAB9/data",
        "label": "ONS Annual Survey of Hours and Earnings — National statistics, indicative, not film-specific",
        "territory": "United Kingdom",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "ons",
        "source_authority": "national_statistics",
    },
    # ── Multi-country REST API sources ────────────────────────────────────────
    {
        "resource_type": "crew_costs",
        "url": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/lc_lci_r2",
        "label": "Eurostat Labour Cost Index — National statistics, indicative, not film-specific",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "eurostat",
        "source_authority": "national_statistics",
    },
    {
        "resource_type": "crew_costs",
        "url": "https://data.oecd.org/api/sdmx-json/data/ULC_EEQ",
        "label": "OECD Unit Labour Costs — National statistics, indicative, not film-specific",
        "territory": None,
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "oecd",
        "source_authority": "national_statistics",
    },
    {
        "resource_type": "crew_costs",
        "url": "https://fred.stlouisfed.org/series/ECIWAG",
        "label": "FRED Employment Cost Index (USA) — National statistics, indicative, not film-specific",
        "territory": "United States",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "fred",
        "source_authority": "national_statistics",
    },
    {
        "resource_type": "crew_costs",
        "url": "https://www150.statcan.gc.ca/n1/en/type/data",
        "label": "Statistics Canada Wages — National statistics, indicative, not film-specific",
        "territory": "Canada",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": True,
        "api_slug": "statcan",
        "source_authority": "national_statistics",
    },
    # ── South Africa — crew cost sources ─────────────────────────────────────
    {
        "resource_type": "crew_costs",
        "url": "https://callacrew.co.za/crew-rates",
        "label": "CallaCrew SA Crew Rates — Film industry rate card",
        "territory": "South Africa",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "film_specific",
    },
    {
        "resource_type": "crew_costs",
        "url": "https://callacrew.co.za/cpa-working-guidelines",
        "label": "CPA Working Guidelines (South Africa) — Film industry rate card",
        "territory": "South Africa",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "film_specific",
    },
    # ── Australia — Fair Work HTML ────────────────────────────────────────────
    {
        "resource_type": "crew_costs",
        "url": "https://www.fairwork.gov.au/pay-and-wages",
        "label": "Fair Work Commission Pay Rates (Australia) — National statistics, indicative, not film-specific",
        "territory": "Australia",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "national_statistics",
    },
    # ── Ireland — crew costs ──────────────────────────────────────────────────
    {
        "resource_type": "crew_costs",
        "url": "https://www.screenireland.ie/funding",
        "label": "Screen Ireland Crew Rates — Film industry benchmark",
        "territory": "Ireland",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "film_specific",
    },
    # ── Malta — crew costs ────────────────────────────────────────────────────
    {
        "resource_type": "crew_costs",
        "url": "https://pcpmalta.com/rebate.html",
        "label": "PCP Malta Production Notes — Film industry benchmark",
        "territory": "Malta",
        "is_pdf": False,
        "use_bls_api": False,
        "use_rest_api": False,
        "api_slug": None,
        "source_authority": "film_specific",
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "scrape_sources" not in inspector.get_table_names():
        return

    # 1. Add new columns (idempotent)
    existing_cols = {col["name"] for col in inspector.get_columns("scrape_sources")}
    for col_name, col_type, default in _NEW_COLUMNS:
        if col_name not in existing_cols:
            op.add_column(
                "scrape_sources",
                sa.Column(col_name, col_type, nullable=True, server_default=str(default) if default is not None else None),
            )

    # 2. Set default False for use_rest_api on existing rows
    if "use_rest_api" not in existing_cols:
        conn.execute(
            sa.text("UPDATE scrape_sources SET use_rest_api = FALSE WHERE use_rest_api IS NULL")
        )

    # 3. Insert new source records (skip if URL already present)
    now = datetime.now(timezone.utc).isoformat()
    for src in _NEW_SOURCES:
        result = conn.execute(
            sa.text("SELECT id FROM scrape_sources WHERE url = :url LIMIT 1"),
            {"url": src["url"]},
        )
        if result.fetchone() is not None:
            continue  # Already exists — skip

        from uuid import uuid4
        conn.execute(
            sa.text(
                "INSERT INTO scrape_sources "
                "(id, resource_type, url, label, territory, is_pdf, use_bls_api, "
                "use_rest_api, api_slug, source_authority, enabled, created_at, updated_at) "
                "VALUES "
                "(:id, :resource_type, :url, :label, :territory, :is_pdf, :use_bls_api, "
                ":use_rest_api, :api_slug, :source_authority, TRUE, :created_at, :updated_at)"
            ),
            {
                "id": str(uuid4()),
                "resource_type": src["resource_type"],
                "url": src["url"],
                "label": src["label"],
                "territory": src.get("territory"),
                "is_pdf": src.get("is_pdf", False),
                "use_bls_api": src.get("use_bls_api", False),
                "use_rest_api": src.get("use_rest_api", False),
                "api_slug": src.get("api_slug"),
                "source_authority": src.get("source_authority"),
                "created_at": now,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "scrape_sources" not in inspector.get_table_names():
        return

    # Remove inserted rows
    for src in _NEW_SOURCES:
        conn.execute(
            sa.text("DELETE FROM scrape_sources WHERE url = :url"),
            {"url": src["url"]},
        )

    # Drop added columns
    existing_cols = {col["name"] for col in inspector.get_columns("scrape_sources")}
    for col_name, _, _ in _NEW_COLUMNS:
        if col_name in existing_cols:
            op.drop_column("scrape_sources", col_name)
