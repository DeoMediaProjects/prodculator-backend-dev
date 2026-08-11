"""A discount must be applied by Stripe, not merely written on the pricing page.

The charged amount comes from the Stripe price object. A percentage hardcoded in the
frontend would show one number and bill another, and would keep advertising a
discount after the coupon expired. So the site advertises a promotion only when a
coupon ID is configured, and the same setting is what the checkout applies.
"""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.modules.payments.service import StripeService


def service(**overrides):
    settings = Settings(STRIPE_SECRET_KEY="sk_test", **overrides)
    return StripeService(settings)


class TestCouponDrivesTheDiscount:
    def test_no_coupon_configured_means_no_discount_argument(self):
        assert service()._promo_discounts() is None

    def test_a_configured_coupon_is_passed_to_stripe(self):
        s = service(STRIPE_PROMO_COUPON_ID="promo_45", STRIPE_PROMO_PERCENT_OFF=45)
        assert s._promo_discounts() == [{"coupon": "promo_45"}]

    def test_a_blank_coupon_is_not_a_coupon(self):
        assert service(STRIPE_PROMO_COUPON_ID="   ")._promo_discounts() is None

    def test_a_percentage_without_a_coupon_discounts_nothing(self):
        """The failure this guards: someone sets the advertised percentage and
        forgets the coupon, so the page promises 45% off and Stripe charges full
        price."""
        assert service(STRIPE_PROMO_PERCENT_OFF=45)._promo_discounts() is None


class TestWhatTheSiteIsAllowedToAdvertise:
    def _promotion(self, **overrides):
        from app.modules.payments.router import active_promotion
        import asyncio

        return asyncio.get_event_loop().run_until_complete(
            active_promotion(Settings(**overrides))
        )

    def test_nothing_is_advertised_without_a_coupon(self):
        assert self._promotion(STRIPE_PROMO_PERCENT_OFF=45)["active"] is False

    def test_nothing_is_advertised_without_a_percentage(self):
        assert self._promotion(STRIPE_PROMO_COUPON_ID="promo_45")["active"] is False

    def test_a_fully_configured_promotion_is_advertised(self):
        result = self._promotion(
            STRIPE_PROMO_COUPON_ID="promo_45", STRIPE_PROMO_PERCENT_OFF=45,
        )
        assert result == {
            "active": True,
            "percentOff": 45,
            "label": "45% off all subscription plans",
        }

    def test_a_custom_label_is_used_when_set(self):
        result = self._promotion(
            STRIPE_PROMO_COUPON_ID="promo_45",
            STRIPE_PROMO_PERCENT_OFF=45,
            STRIPE_PROMO_LABEL="Launch offer: 45% off every plan",
        )
        assert result["label"] == "Launch offer: 45% off every plan"

    @pytest.mark.parametrize("percent", [0, -10, 100, 150])
    def test_a_nonsensical_percentage_advertises_nothing(self, percent):
        result = self._promotion(
            STRIPE_PROMO_COUPON_ID="promo", STRIPE_PROMO_PERCENT_OFF=percent,
        )
        assert result["active"] is False
