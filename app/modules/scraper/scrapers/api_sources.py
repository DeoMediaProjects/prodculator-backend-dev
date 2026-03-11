"""REST API fetchers for structured crew cost data sources.

Each function returns a list of normalised crew_costs dicts ready for
diff_and_queue(). StatCan and ONS produce actual wage figures usable as
indicative floor references. Eurostat LCI, OECD ULC, and FRED ECIWAG
have been removed — they returned index numbers with day_rate=None and
no role breakdown, making them useless for crew cost benchmarking.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT = 20

# Statistics Canada — Table 14-10-0064-01, wages by occupation
_STATCAN_TABLE = "14-10-0064-01"


def fetch_api_source(api_slug: str, settings: Settings) -> list[dict[str, Any]]:
    """Dispatch to the correct API fetcher based on slug."""
    fetchers = {
        "statcan": _fetch_statcan,
        "ons": _fetch_ons,
    }
    fn = fetchers.get(api_slug)
    if fn is None:
        logger.warning("Unknown api_slug '%s' — skipping REST API fetch", api_slug)
        return []
    try:
        return fn(settings)
    except Exception as exc:
        logger.error("REST API fetch failed for slug=%s: %s", api_slug, exc)
        return []


# ── Statistics Canada ─────────────────────────────────────────────────────────

def _fetch_statcan(settings: Settings) -> list[dict[str, Any]]:
    """Fetch Statistics Canada wage data via WDS REST API.

    Returns indicative wage figures for Canada. Not film-specific.
    """
    try:
        url = f"https://www150.statcan.gc.ca/t1/tbl1/en/dtbl!download/{_STATCAN_TABLE}/downloadTable"
        # StatCan requires a GET to retrieve latest data URL, then download CSV
        # As a lightweight alternative, use the getSeriesData endpoint for a known vector
        # Vector 2062809: average weekly earnings, all industries, Canada
        vec_url = "https://www150.statcan.gc.ca/t1/tbl1/en/dtbl!download/v2062809/downloadTable"
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                "https://www150.statcan.gc.ca/t1/tbl1/en/liblookup/getSeriesData",
                params={"vectorId": "v2062809", "latestN": "4"},
            )
            if resp.status_code != 200:
                logger.warning("StatCan: unexpected status %s", resp.status_code)
                return []
            data = resp.json()

        obs = data.get("object", {}).get("vectorDataPoint", [])
        if not obs:
            return []

        latest = max(obs, key=lambda o: o.get("refPer", ""))
        value = latest.get("value")
        period = latest.get("refPer", "latest")

        if value is None:
            return []

        weekly = float(value)
        daily = round(weekly / 5, 2)

        return [{
            "territory": "Canada",
            "role": "Average Weekly Earnings (all industries)",
            "category": "Below-the-Line",
            "day_rate": daily,
            "week_rate": weekly,
            "union": None,
            "currency": "CAD",
            "source_url": "https://www150.statcan.gc.ca/t1/tbl1/en/table/dtbl!14100063",
            "budget_band": None,
            "rate_notes": (
                f"Statistics Canada avg weekly earnings {period}: CAD {weekly:,.2f}/week. "
                "National statistics — indicative, not film-specific."
            ),
        }]
    except Exception as exc:
        logger.error("StatCan fetch failed: %s", exc)
        return []


# ── ONS (UK) ─────────────────────────────────────────────────────────────────

def _fetch_ons(settings: Settings) -> list[dict[str, Any]]:
    """Fetch ONS Annual Survey of Hours and Earnings (UK).

    Uses ONS beta API. Returns indicative weekly earnings. Not film-specific.
    """
    try:
        # ASHE Table 7 — full-time employees, weekly earnings, all industries
        url = "https://api.ons.gov.uk/v1/datasets/ashe-table-7/timeseries/KAB9/data"
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning("ONS API: unexpected status %s", resp.status_code)
                return []
            data = resp.json()

        annual = data.get("years", [])
        if not annual:
            return []

        latest = max(annual, key=lambda o: o.get("year", "0"))
        value_str = latest.get("value", "")
        if not value_str or value_str == "..":
            return []

        weekly = float(value_str)
        daily = round(weekly / 5, 2)
        period = latest.get("year", "latest")

        return [{
            "territory": "United Kingdom",
            "role": "Median Weekly Earnings (full-time, all industries)",
            "category": "Below-the-Line",
            "day_rate": daily,
            "week_rate": weekly,
            "union": None,
            "currency": "GBP",
            "source_url": "https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/earningsandworkinghours/datasets/ashetable7",
            "budget_band": None,
            "rate_notes": (
                f"ONS ASHE Table 7 {period}: £{weekly:,.2f}/week median (full-time). "
                "National statistics — indicative, not film-specific."
            ),
        }]
    except Exception as exc:
        logger.error("ONS fetch failed: %s", exc)
        return []
