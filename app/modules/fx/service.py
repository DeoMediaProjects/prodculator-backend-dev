"""FX Conversion Service.

Provides real-time exchange rates via ExchangeRate-API with:
- Redis cache (24h TTL, key pattern fx_rate:{FROM}:{TO})
- Hardcoded GBP-base fallback rates when API is unavailable
- Batch rate fetching for prompt injection
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

# Hardcoded GBP-base fallback rates (refresh monthly)
# Format: {ISO_CODE: rate_vs_GBP}  e.g. 1 GBP = 23.8 ZAR
_FALLBACK_RATES_GBP_BASE: dict[str, float] = {
    "GBP": 1.0,
    "USD": 1.27,
    "EUR": 1.17,
    "CAD": 1.72,
    "AUD": 1.97,
    "ZAR": 23.8,
    "HUF": 460.0,
    "NGN": 2085.0,
    "CZK": 29.0,
    "PLN": 5.05,
}

_FALLBACK_DATE = date(2026, 3, 1)  # Update when refreshing rates

_CACHE_TTL_SECONDS = 86_400  # 24 hours
_CACHE_KEY_PREFIX = "fx_rate"

_API_BASE = "https://v6.exchangerate-api.com/v6"


class FXService:
    """Exchange rate lookup with Redis caching and offline fallback."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._redis = self._build_redis()

    def _build_redis(self):
        """Build a synchronous Redis client (not asyncio — used in sync pipeline)."""
        try:
            import redis as redis_lib
            return redis_lib.from_url(
                self.settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
        except Exception:
            return None

    # ── Public API ──────────────────────────────────────────────────────────

    def get_rate(self, from_currency: str, to_currency: str) -> tuple[float, date]:
        """Return (rate, rate_date). Uses cache → API → fallback in order."""
        from_c = from_currency.upper()
        to_c = to_currency.upper()

        if from_c == to_c:
            return 1.0, date.today()

        # 1. Try Redis cache
        cached = self._cache_get(from_c, to_c)
        if cached is not None:
            return cached

        # 2. Try live API
        live = self._fetch_api_rate(from_c, to_c)
        if live is not None:
            rate, rate_date = live
            self._cache_set(from_c, to_c, rate, rate_date)
            return rate, rate_date

        # 3. Fallback to hardcoded GBP-base rates
        return self._fallback_rate(from_c, to_c)

    def convert(self, amount: float, from_currency: str, to_currency: str) -> tuple[float, date]:
        """Convert amount and return (converted_amount, rate_date)."""
        rate, rate_date = self.get_rate(from_currency, to_currency)
        return round(amount * rate, 2), rate_date

    def get_rates_batch(
        self, base: str, targets: list[str]
    ) -> dict[str, tuple[float, date]]:
        """Fetch multiple rates from a single base currency.

        Returns {ISO_CODE: (rate, rate_date)} for every target requested.
        Uses a single API call where possible, then falls back per-currency.
        """
        base_c = base.upper()
        targets_c = [t.upper() for t in targets if t.upper() != base_c]

        result: dict[str, tuple[float, date]] = {base_c: (1.0, date.today())}

        # Try to load everything from cache first
        missing: list[str] = []
        for t in targets_c:
            cached = self._cache_get(base_c, t)
            if cached is not None:
                result[t] = cached
            else:
                missing.append(t)

        if not missing:
            return result

        # One API call for the base currency — returns all targets at once
        bulk = self._fetch_api_bulk(base_c)
        if bulk:
            bulk_rates, rate_date = bulk
            for t in missing:
                if t in bulk_rates:
                    rate = bulk_rates[t]
                    result[t] = (rate, rate_date)
                    self._cache_set(base_c, t, rate, rate_date)
                    missing = [m for m in missing if m != t]

        # Anything still missing → fallback
        for t in missing:
            result[t] = self._fallback_rate(base_c, t)

        return result

    # ── Cache helpers ────────────────────────────────────────────────────────

    def _cache_key(self, from_c: str, to_c: str) -> str:
        return f"{_CACHE_KEY_PREFIX}:{from_c}:{to_c}"

    def _cache_get(self, from_c: str, to_c: str) -> tuple[float, date] | None:
        if self._redis is None:
            return None
        try:
            key = self._cache_key(from_c, to_c)
            raw: str | None = self._redis.get(key)  # type: ignore[assignment]
            if raw is None:
                return None
            # stored as "rate|YYYY-MM-DD"
            parts = raw.split("|")
            if len(parts) != 2:
                return None
            rate = float(parts[0])
            rate_date = date.fromisoformat(parts[1])
            return rate, rate_date
        except Exception as exc:
            logger.debug("FX cache read failed: %s", exc)
            return None

    def _cache_set(self, from_c: str, to_c: str, rate: float, rate_date: date) -> None:
        if self._redis is None:
            return
        try:
            key = self._cache_key(from_c, to_c)
            value = f"{rate}|{rate_date.isoformat()}"
            self._redis.setex(key, _CACHE_TTL_SECONDS, value)
        except Exception as exc:
            logger.debug("FX cache write failed: %s", exc)

    # ── API helpers ──────────────────────────────────────────────────────────

    def _fetch_api_rate(self, from_c: str, to_c: str) -> tuple[float, date] | None:
        """Fetch a single pair from ExchangeRate-API."""
        api_key = self.settings.EXCHANGE_RATE_API_KEY
        if not api_key:
            return None
        try:
            url = f"{_API_BASE}/{api_key}/pair/{from_c}/{to_c}"
            with httpx.Client(timeout=8) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
            if data.get("result") != "success":
                return None
            rate = float(data["conversion_rate"])
            rate_date = _parse_api_date(data.get("time_last_update_utc"))
            return rate, rate_date
        except Exception as exc:
            logger.warning("FX API single-pair fetch failed %s→%s: %s", from_c, to_c, exc)
            return None

    def _fetch_api_bulk(self, base_c: str) -> tuple[dict[str, float], date] | None:
        """Fetch all rates for a base currency in one call."""
        api_key = self.settings.EXCHANGE_RATE_API_KEY
        if not api_key:
            return None
        try:
            url = f"{_API_BASE}/{api_key}/latest/{base_c}"
            with httpx.Client(timeout=8) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
            if data.get("result") != "success":
                return None
            rates: dict[str, float] = {
                k: float(v) for k, v in data.get("conversion_rates", {}).items()
            }
            rate_date = _parse_api_date(data.get("time_last_update_utc"))
            return rates, rate_date
        except Exception as exc:
            logger.warning("FX API bulk fetch failed base=%s: %s", base_c, exc)
            return None

    # ── Fallback ─────────────────────────────────────────────────────────────

    def _fallback_rate(self, from_c: str, to_c: str) -> tuple[float, date]:
        """Compute rate via GBP-base hardcoded table. Logs a warning."""
        gbp_from = _FALLBACK_RATES_GBP_BASE.get(from_c)
        gbp_to = _FALLBACK_RATES_GBP_BASE.get(to_c)

        if gbp_from is None or gbp_to is None:
            missing = from_c if gbp_from is None else to_c
            logger.warning(
                "FX fallback: no rate for %s — returning 1.0 (identity). "
                "Add to _FALLBACK_RATES_GBP_BASE.",
                missing,
            )
            return 1.0, _FALLBACK_DATE

        # Cross-rate via GBP: from_c → GBP → to_c
        rate = round(gbp_to / gbp_from, 6)
        logger.warning(
            "FX using hardcoded fallback rate %s→%s = %.6f (as of %s). "
            "Set EXCHANGE_RATE_API_KEY for live rates.",
            from_c,
            to_c,
            rate,
            _FALLBACK_DATE,
        )
        return rate, _FALLBACK_DATE


# ── Module-level helpers ─────────────────────────────────────────────────────

def _parse_api_date(raw: str | None) -> date:
    """Parse ExchangeRate-API's time_last_update_utc string, fallback to today."""
    if not raw:
        return date.today()
    try:
        # Format: "Mon, 10 Mar 2026 00:00:01 +0000"
        dt = datetime.strptime(raw, "%a, %d %b %Y %H:%M:%S %z")
        return dt.astimezone(timezone.utc).date()
    except Exception:
        return date.today()
