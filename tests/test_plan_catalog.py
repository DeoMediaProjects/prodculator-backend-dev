"""Tests for the price-id → plan-tier resolver."""

from app.core.config import Settings
from app.modules.payments.plan_catalog import (
    build_price_to_plan_map,
    classify_change,
    resolve_plan_from_subscription,
)


def _settings(**overrides) -> Settings:
    # _env_file=None bypasses .env loading so we test against explicit values only.
    base = {
        "JWT_SECRET_KEY": "x" * 64,
        "STRIPE_PRICE_PROFESSIONAL_GBP": "price_pro_gbp",
        "STRIPE_PRICE_PROFESSIONAL_USD": "price_pro_usd",
        "STRIPE_PRICE_PROFESSIONAL_ANNUAL_GBP": "price_pro_annual",
        "STRIPE_PRICE_PRODUCER_GBP": "price_producer_gbp",
        "STRIPE_PRICE_PRODUCER_USD": "price_producer_usd",
        "STRIPE_PRICE_STUDIO_GBP": "price_studio_gbp",
        "STRIPE_PRICE_STUDIO_USD": "price_studio_usd",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestPriceMap:
    def test_skips_empty_price_ids(self):
        s = Settings(
            _env_file=None,
            JWT_SECRET_KEY="x" * 64,
            STRIPE_PRICE_PRODUCER_GBP="price_p",
        )
        m = build_price_to_plan_map(s)
        assert m == {"price_p": "producer"}

    def test_maps_each_tier(self):
        m = build_price_to_plan_map(_settings())
        assert m["price_pro_gbp"] == "professional"
        assert m["price_pro_usd"] == "professional"
        assert m["price_pro_annual"] == "professional"
        assert m["price_producer_gbp"] == "producer"
        assert m["price_studio_usd"] == "studio"


class TestResolvePlan:
    def test_resolves_from_active_item_price(self):
        sub = {"items": {"data": [{"price": {"id": "price_producer_gbp"}}]}}
        assert resolve_plan_from_subscription(sub, _settings()) == "producer"

    def test_returns_none_for_unknown_price(self):
        sub = {"items": {"data": [{"price": {"id": "price_unknown"}}]}}
        assert resolve_plan_from_subscription(sub, _settings()) is None

    def test_returns_none_when_no_items(self):
        assert resolve_plan_from_subscription({"items": {"data": []}}, _settings()) is None
        assert resolve_plan_from_subscription({}, _settings()) is None

    def test_normalizes_legacy_single(self):
        s = Settings(
            _env_file=None,
            JWT_SECRET_KEY="x" * 64,
            STRIPE_PRICE_SINGLE_GBP="price_single",
        )
        sub = {"items": {"data": [{"price": {"id": "price_single"}}]}}
        # The catalog maps SINGLE → PROFESSIONAL.value, so legacy 'single' surfaces as 'professional'.
        assert resolve_plan_from_subscription(sub, s) == "professional"


class TestClassifyChange:
    def test_upgrade(self):
        assert classify_change("free", "professional") == "upgrade"
        assert classify_change("professional", "producer") == "upgrade"
        assert classify_change("producer", "studio") == "upgrade"
        assert classify_change("free", "studio") == "upgrade"

    def test_downgrade(self):
        assert classify_change("studio", "producer") == "downgrade"
        assert classify_change("producer", "professional") == "downgrade"
        assert classify_change("studio", "free") == "downgrade"

    def test_same(self):
        assert classify_change("professional", "professional") == "same"
        # Legacy 'single' is normalized to 'professional' — same tier.
        assert classify_change("single", "professional") == "same"

    def test_unknown_treated_as_free(self):
        assert classify_change("nonsense", "producer") == "upgrade"
