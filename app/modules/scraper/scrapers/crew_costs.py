import logging
from typing import Any

import httpx

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.modules.scraper.differ import diff_and_queue
from app.modules.scraper.extractor import extract
from app.modules.scraper.fetcher import fetch_and_strip, fetch_pdf_links, fetch_pdf_text
from app.modules.scraper.scrapers.api_sources import fetch_api_source

logger = logging.getLogger(__name__)

# BLS series IDs for relevant film/TV occupations (national level)
BLS_SERIES_IDS = [
    "OEUM000000027106200",  # Camera operators, TV/film/video
    "OEUM000000027106100",  # Producers and directors
    "OEUM000000027105900",  # Film/video editors
    "OEUM000000027106900",  # Sound engineering technicians
]

_BLS_ROLE_MAP = {
    "OEUM000000027106200": "Camera Operator",
    "OEUM000000027106100": "Producer/Director",
    "OEUM000000027105900": "Film/Video Editor",
    "OEUM000000027106900": "Sound Engineer",
}


def _fetch_bls(settings: Settings) -> list[dict]:
    """Call BLS public API v2 to fetch annual mean wages for film occupations."""
    if not settings.BLS_API_KEY:
        logger.warning("BLS_API_KEY not set — skipping BLS data fetch")
        return []
    try:
        payload = {
            "seriesid": BLS_SERIES_IDS,
            "startyear": "2023",
            "endyear": "2025",
            "registrationkey": settings.BLS_API_KEY,
        }
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.bls.gov/publicAPI/v2/timeseries/data/",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        records = []
        for series in data.get("Results", {}).get("series", []):
            series_id = series.get("seriesID", "")
            latest = series.get("data", [{}])[0]
            value = latest.get("value")
            if not value:
                continue
            annual = float(value)
            day_rate_cents = round(annual / 260 * 100)
            week_rate_cents = round(annual / 52 * 100)
            records.append({
                "country": "US",
                "role": _BLS_ROLE_MAP.get(series_id, "Unknown Role"),
                "role_category": "BTL-General",
                "department": "day",
                "union_rate_cents": day_rate_cents,
                "non_union_rate_cents": week_rate_cents,
                "rate_currency": "USD",
                "source_name": "BLS OEWS NAICS 5121",
                "source_type": "government_stats",
            })
        return records
    except Exception as exc:
        logger.error("BLS API fetch failed: %s", exc)
        return []


def run(source_row: dict[str, Any], db: DatabaseClient, settings: Settings) -> int:
    # REST API branch — structured data, no AI extraction needed
    if source_row.get("use_rest_api"):
        api_slug = source_row.get("api_slug", "")
        records = fetch_api_source(api_slug, settings)
        if not records:
            return 0
        return diff_and_queue(
            "crew_costs", records, source_row["url"], db, confidence="high",
        )

    # BLS API branch — structured data, no AI extraction needed
    if source_row.get("use_bls_api"):
        records = _fetch_bls(settings)
        if not records:
            return 0
        return diff_and_queue(
            "crew_costs", records, source_row["url"], db, confidence="high",
        )

    url = source_row["url"]
    territory_hint = source_row.get("territory")

    # PDF branch — index pages linking to rate card PDFs
    if source_row.get("is_pdf"):
        return _run_pdf_pipeline(url, territory_hint, db, settings)

    # Standard HTML scrape path
    text = fetch_and_strip(url, settings)
    if not text:
        raise RuntimeError(f"No text returned for crew_costs source: {url}")

    records = extract("crew_costs", text, territory_hint, settings)
    if not records:
        logger.info("No crew cost records extracted from %s", url)
        return 0

    return diff_and_queue("crew_costs", records, url, db, confidence="medium")


def _run_pdf_pipeline(
    index_url: str,
    territory_hint: str | None,
    db: DatabaseClient,
    settings: Settings,
) -> int:
    """Fetch PDF links from an index page, extract text from each, and diff."""
    pdf_links = fetch_pdf_links(index_url, settings)
    if not pdf_links:
        # The URL itself might be a direct PDF link
        text = fetch_pdf_text(index_url, settings)
        if not text:
            logger.warning("No PDF content from crew_costs source: %s", index_url)
            return 0
        records = extract("crew_costs", text, territory_hint, settings)
        if not records:
            return 0
        return diff_and_queue("crew_costs", records, index_url, db, confidence="medium")

    total_changes = 0
    for pdf_url in pdf_links:
        text = fetch_pdf_text(pdf_url, settings)
        if not text:
            continue
        records = extract("crew_costs", text, territory_hint, settings)
        if not records:
            continue
        total_changes += diff_and_queue(
            "crew_costs", records, pdf_url, db, confidence="medium",
        )
    return total_changes
