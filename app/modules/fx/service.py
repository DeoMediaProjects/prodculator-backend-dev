"""FX Conversion Service.

Provides real-time exchange rates via ExchangeRate-API with:
- Redis cache (24h TTL, key pattern fx_rate:{FROM}:{TO})
- Hardcoded GBP-base fallback rates when API is unavailable
- Batch rate fetching for prompt injection
"""

from __future__ import annotations

import logging
import time
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
    "NZD": 2.16,
    "MAD": 12.8,
    "RSD": 137.0,
    "RON": 5.85,
    "ISK": 176.0,
    "SGD": 1.72,
    "JPY": 191.0,
    "KRW": 1865.0,
}

# Territory → local currency mapping (v3)
TERRITORY_CURRENCY: dict[str, str] = {
    "United Kingdom": "GBP",
    "England": "GBP",
    "Scotland": "GBP",
    "Wales": "GBP",
    "Northern Ireland": "GBP",
    "Ireland": "EUR",
    "Malta": "EUR",
    "Hungary": "HUF",
    "South Africa": "ZAR",
    "Western Cape": "ZAR",
    "Gauteng": "ZAR",
    "KwaZulu-Natal": "ZAR",
    "France": "EUR",
    "Île-de-France": "EUR",
    "Spain": "EUR",
    "Canary Islands": "EUR",
    "Czech Republic": "CZK",
    "Australia": "AUD",
    "New South Wales": "AUD",
    "Victoria": "AUD",
    "Queensland": "AUD",
    "New Zealand": "NZD",
    "Canada": "CAD",
    "British Columbia": "CAD",
    "Ontario": "CAD",
    "Quebec": "CAD",
    "Alberta": "CAD",
    "United States": "USD",
    "Georgia": "USD",
    "California": "USD",
    "New York": "USD",
    "Louisiana": "USD",
    "New Mexico": "USD",
    "Illinois": "USD",
    "Nigeria": "NGN",
    "Italy": "EUR",
    "Portugal": "EUR",
    "Morocco": "MAD",
    "Serbia": "RSD",
    "Romania": "RON",
    "Germany": "EUR",
    "Bavaria": "EUR",
    "Berlin": "EUR",
    "Iceland": "ISK",
    "Belgium": "EUR",
    "Netherlands": "EUR",
    "Japan": "JPY",
    "South Korea": "KRW",
    "Singapore": "SGD",
}

# Currencies with high volatility — require explicit warning (v3 Section 04)
_VOLATILE_CURRENCIES: dict[str, str] = {
    "NGN": "NGN has devalued >60% vs GBP in 2022-2024. Budget with contingency for further devaluation.",
    "ZAR": "ZAR/GBP rate fluctuates 15-20% annually. Lock exchange rates early if possible.",
    "HUF": "HUF moderately volatile vs GBP. Monitor rate quarterly during pre-production.",
}

_FALLBACK_DATE = date(2026, 3, 1)  # Update when refreshing rates

_CACHE_TTL_SECONDS = 86_400  # 24 hours
_CACHE_KEY_PREFIX = "fx_rate"
_WARNING_THROTTLE_SECONDS = 300
_API_RATE_LIMIT_COOLDOWN_SECONDS = 300

_API_BASE = "https://v6.exchangerate-api.com/v6"


class FXService:
    """Exchange rate lookup with Redis caching and offline fallback."""

    _api_blocked_until_monotonic: float = 0.0
    _warning_last_logged: dict[str, float] = {}

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

    def convert_budget(
        self, amount: float, from_currency: str, to_currency: str = "GBP"
    ) -> dict:
        """Convert a budget amount and return a dict with full context.

        Returns: {converted, rate, rate_date, from_currency, to_currency, display}
        """
        rate, rate_date = self.get_rate(from_currency, to_currency)
        converted = round(amount * rate, 2)
        return {
            "converted": converted,
            "rate": rate,
            "rate_date": rate_date.isoformat(),
            "from_currency": from_currency.upper(),
            "to_currency": to_currency.upper(),
            "display": (
                f"Budget: {from_currency.upper()} {amount:,.0f} = "
                f"{to_currency.upper()} {converted:,.0f} "
                f"at rate {rate:.4f} (fetched {rate_date.isoformat()})"
            ),
        }

    def compute_currency_advantage_score(
        self, budget_currency: str, territory_currency: str
    ) -> tuple[int, str | None]:
        """Compute currency advantage score (0-100) per v3 spec Section 04.

        Returns (score, volatility_warning_or_None).
        - Same currency → 50 (neutral)
        - Budget currency stronger → score > 50
        - Budget currency weaker → score < 50
        """
        bc = budget_currency.upper()
        tc = territory_currency.upper()

        if bc == tc:
            return 50, None

        # Get how many units of territory currency 1 unit of budget currency buys
        rate, _ = self.get_rate(bc, tc)

        # Compute advantage based on the rate magnitude.
        # We use log-scale normalisation to handle extreme differentials
        # (e.g. GBP → NGN at 2085:1) without saturating the score.
        import math
        log_rate = math.log10(max(rate, 0.001))
        # log_rate > 0 means budget_currency buys >1 unit of territory_currency (advantage)
        # log_rate < 0 means budget_currency buys <1 unit (disadvantage)
        # Scale: log10(2085) ≈ 3.32 → should map to ~95
        # log10(23.8) ≈ 1.38 → should map to ~82
        # log10(1.17) ≈ 0.07 → should map to ~53
        # log10(0.85) ≈ -0.07 → should map to ~47
        advantage = log_rate * 14  # scale factor: ±3.5 log units → ±49 points
        score = int(50 + max(-48, min(48, advantage)))
        score = max(0, min(100, score))

        # Volatility warning
        warning = _VOLATILE_CURRENCIES.get(tc) or _VOLATILE_CURRENCIES.get(bc)

        return score, warning

    def compute_currency_advantage_batch(
        self,
        budget_currency: str,
        territories: list[str],
    ) -> dict[str, dict]:
        """Compute currency advantage for a list of territories.

        Returns {territory_name: {"score": int, "warning": str|None, "currency": str}}
        """
        results: dict[str, dict] = {}
        for territory in territories:
            tc = TERRITORY_CURRENCY.get(territory)
            if tc is None:
                results[territory] = {"score": 50, "warning": None, "currency": "UNKNOWN"}
                continue
            score, warning = self.compute_currency_advantage_score(budget_currency, tc)
            results[territory] = {"score": score, "warning": warning, "currency": tc}
        return results

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

    @classmethod
    def _is_api_temporarily_blocked(cls) -> bool:
        return time.monotonic() < cls._api_blocked_until_monotonic

    @classmethod
    def _activate_api_cooldown(cls) -> None:
        cls._api_blocked_until_monotonic = max(
            cls._api_blocked_until_monotonic,
            time.monotonic() + _API_RATE_LIMIT_COOLDOWN_SECONDS,
        )
        cls._warning_once(
            "fx_api_429_cooldown",
            "FX API returned 429; suspending live FX calls for %ss and using cache/fallback rates.",
            _API_RATE_LIMIT_COOLDOWN_SECONDS,
        )

    @classmethod
    def _warning_once(cls, key: str, message: str, *args) -> None:
        now = time.monotonic()
        last = cls._warning_last_logged.get(key)
        if last is not None and now - last < _WARNING_THROTTLE_SECONDS:
            return
        cls._warning_last_logged[key] = now
        logger.warning(message, *args)

    def _fetch_api_rate(self, from_c: str, to_c: str) -> tuple[float, date] | None:
        """Fetch a single pair from ExchangeRate-API."""
        api_key = self.settings.EXCHANGE_RATE_API_KEY
        if not api_key or self._is_api_temporarily_blocked():
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
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 429:
                self._activate_api_cooldown()
                return None
            self._warning_once(
                f"fx_single_api_error:{from_c}:{to_c}:{status_code}",
                "FX API single-pair fetch failed %s→%s (status=%s): %s",
                from_c,
                to_c,
                status_code,
                exc,
            )
            return None
        except Exception as exc:
            self._warning_once(
                f"fx_single_api_error:{from_c}:{to_c}:unknown",
                "FX API single-pair fetch failed %s→%s: %s",
                from_c,
                to_c,
                exc,
            )
            return None

    def _fetch_api_bulk(self, base_c: str) -> tuple[dict[str, float], date] | None:
        """Fetch all rates for a base currency in one call."""
        api_key = self.settings.EXCHANGE_RATE_API_KEY
        if not api_key or self._is_api_temporarily_blocked():
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
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 429:
                self._activate_api_cooldown()
                return None
            self._warning_once(
                f"fx_bulk_api_error:{base_c}:{status_code}",
                "FX API bulk fetch failed base=%s (status=%s): %s",
                base_c,
                status_code,
                exc,
            )
            return None
        except Exception as exc:
            self._warning_once(
                f"fx_bulk_api_error:{base_c}:unknown",
                "FX API bulk fetch failed base=%s: %s",
                base_c,
                exc,
            )
            return None

    # ── Fallback ─────────────────────────────────────────────────────────────

    def _fallback_rate(self, from_c: str, to_c: str) -> tuple[float, date]:
        """Compute rate via GBP-base hardcoded table. Logs a warning."""
        gbp_from = _FALLBACK_RATES_GBP_BASE.get(from_c)
        gbp_to = _FALLBACK_RATES_GBP_BASE.get(to_c)

        if gbp_from is None or gbp_to is None:
            missing = from_c if gbp_from is None else to_c
            self._warning_once(
                f"fx_missing_fallback:{missing}",
                "FX fallback: no rate for %s — returning 1.0 (identity). "
                "Add to _FALLBACK_RATES_GBP_BASE.",
                missing,
            )
            return 1.0, _FALLBACK_DATE

        # Cross-rate via GBP: from_c → GBP → to_c
        rate = round(gbp_to / gbp_from, 6)
        self._warning_once(
            f"fx_hardcoded_fallback:{from_c}:{to_c}",
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
