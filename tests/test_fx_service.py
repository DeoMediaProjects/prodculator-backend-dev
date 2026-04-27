from types import SimpleNamespace

import httpx

import app.modules.fx.service as fx_module
from app.modules.fx.service import FXService


def _settings(api_key: str = "test-key"):
    return SimpleNamespace(
        EXCHANGE_RATE_API_KEY=api_key,
        REDIS_URL="redis://localhost:6379/0",
    )


def _fresh_service(monkeypatch, api_key: str = "test-key") -> FXService:
    monkeypatch.setattr(FXService, "_build_redis", lambda self: None)
    FXService._api_blocked_until_monotonic = 0.0
    FXService._warning_last_logged = {}
    return FXService(_settings(api_key=api_key))


def test_api_429_triggers_cooldown_and_skips_repeated_calls(monkeypatch):
    service = _fresh_service(monkeypatch)

    class FakeClient:
        call_count = 0

        def __init__(self, timeout=8):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            FakeClient.call_count += 1
            request = httpx.Request("GET", url)
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    monkeypatch.setattr(fx_module.httpx, "Client", FakeClient)

    # First call hits API and enters cooldown.
    rate_1, _ = service.get_rate("GBP", "EUR")
    # Second call should skip API due to cooldown and use fallback directly.
    rate_2, _ = service.get_rate("GBP", "USD")

    assert rate_1 > 0
    assert rate_2 > 0
    assert FakeClient.call_count == 1


def test_fallback_contains_known_missing_currencies(monkeypatch):
    service = _fresh_service(monkeypatch, api_key="")

    for currency in ("ISK", "SGD", "JPY", "KRW"):
        rate, _ = service.get_rate("GBP", currency)
        assert rate != 1.0


def test_fallback_warning_is_throttled_for_same_pair(monkeypatch):
    service = _fresh_service(monkeypatch, api_key="")

    warning_messages = []

    def fake_warning(message, *args):
        warning_messages.append(message % args if args else message)

    monkeypatch.setattr(fx_module.logger, "warning", fake_warning)

    service._fallback_rate("GBP", "EUR")
    service._fallback_rate("GBP", "EUR")

    assert len(warning_messages) == 1
