import logging
from typing import Any

from app.core.config import Settings
from app.core.database_client import DatabaseClient
from app.modules.scraper.differ import diff_and_queue
from app.modules.scraper.extractor import extract
from app.modules.scraper.fetcher import fetch_and_strip

logger = logging.getLogger(__name__)


def run(source_row: dict[str, Any], db: DatabaseClient, settings: Settings) -> int:
    url = source_row["url"]
    territory_hint = source_row.get("territory")

    text = fetch_and_strip(url, settings)
    if not text:
        raise RuntimeError(f"No text returned for grants source: {url}")

    records = extract("grants", text, territory_hint, settings)
    if not records:
        logger.info("No grant records extracted from %s", url)
        return 0

    return diff_and_queue("grants", records, url, db, confidence="medium")
