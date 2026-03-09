from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.core.database_client import DatabaseClient

logger = logging.getLogger(__name__)

_TIMEOUT = 10


def test_anthropic(settings: Settings) -> tuple[bool, str]:
    key = settings.ANTHROPIC_API_KEY
    if not key:
        return False, "ANTHROPIC_API_KEY not configured"
    try:
        resp = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return True, "Connection successful"
        return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


def test_database(db: DatabaseClient) -> tuple[bool, str]:
    try:
        result = db.table("data_sources").select("id").limit(1).execute()
        if result.data is not None:
            return True, "Connection successful"
        return False, "Query returned no result"
    except Exception as e:
        return False, str(e)


def test_tmdb(settings: Settings) -> tuple[bool, str]:
    key = settings.TMDB_API_KEY
    if not key:
        return False, "TMDB_API_KEY not configured"
    try:
        resp = httpx.get(
            "https://api.themoviedb.org/3/configuration",
            params={"api_key": key},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return True, "Connection successful"
        return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


def test_bls(settings: Settings) -> tuple[bool, str]:
    key = settings.BLS_API_KEY
    if not key:
        return False, "BLS_API_KEY not configured"
    try:
        resp = httpx.post(
            "https://api.bls.gov/publicAPI/v2/timeseries/data/",
            json={
                "seriesid": ["OEUM000000027106200"],
                "startyear": "2024",
                "endyear": "2025",
                "registrationkey": key,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "REQUEST_SUCCEEDED":
                return True, "Connection successful"
            return False, data.get("message", ["Unknown error"])[0]
        return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


def test_stripe(settings: Settings) -> tuple[bool, str]:
    key = settings.STRIPE_SECRET_KEY
    if not key:
        return False, "STRIPE_SECRET_KEY not configured"
    try:
        import stripe

        stripe.api_key = key
        stripe.Account.retrieve()
        return True, "Connection successful"
    except Exception as e:
        return False, str(e)


def test_sendgrid(settings: Settings) -> tuple[bool, str]:
    key = settings.SENDGRID_API_KEY
    if not key:
        return False, "SENDGRID_API_KEY not configured"
    try:
        resp = httpx.get(
            "https://api.sendgrid.com/v3/scopes",
            headers={"Authorization": f"Bearer {key}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return True, "Connection successful"
        return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


def test_redis(settings: Settings) -> tuple[bool, str]:
    url = settings.REDIS_URL
    if not url:
        return False, "REDIS_URL not configured"
    try:
        import redis as redis_lib

        client = redis_lib.from_url(url, socket_timeout=_TIMEOUT)
        if client.ping():
            return True, "Connection successful"
        return False, "PING returned False"
    except Exception as e:
        return False, str(e)


def test_not_implemented(slug: str) -> tuple[bool, str]:
    return False, f"Integration '{slug}' is not yet implemented"
