"""A discount must be applied by Stripe, not merely written on the pricing page.

The charged amount comes from the Stripe price object. A percentage hardcoded in the
frontend would show one number and bill another, and would keep advertising a
discount after the coupon expired. So the site advertises a promotion only when a
coupon ID is configured, and the same setting is what the checkout applies.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.modules.payments.service import StripeService


# Settings reads the real .env, which now carries a live coupon. Tests that mean
# "no promotion configured" have to say so explicitly, or they pass or fail
# depending on the machine they run on.
NO_PROMO = dict(
    STRIPE_PROMO_COUPON_ID="",
    STRIPE_PROMO_ONEOFF_COUPON_ID="",
    STRIPE_PROMO_PERCENT_OFF=0,
    STRIPE_PROMO_LABEL="",
)


def settings_for(**overrides):
    return Settings(STRIPE_SECRET_KEY="sk_test", **{**NO_PROMO, **overrides})


def service(**overrides):
    return StripeService(settings_for(**overrides))


LIVE = dict(
    # Two coupons for one offer. The subscription one runs for a customer's first
    # three months, which is duration=repeating, and Stripe will not apply a
    # repeating coupon to a one-time payment — so the one-off report has its own at
    # the same percentage with duration=once.
    STRIPE_PROMO_COUPON_ID="promo_49",
    STRIPE_PROMO_ONEOFF_COUPON_ID="promo_49_once",
    STRIPE_PROMO_PERCENT_OFF=49,
    # The launch offer: the individual side. "single" is the one-off report, which
    # is not a subscription plan but is inside the offer.
    STRIPE_PROMO_PLANS="professional,producer,single",
)


class TestCouponDrivesTheDiscount:
    def test_no_coupon_configured_means_no_discount_argument(self):
        assert service()._promo_discounts("professional") is None

    @pytest.mark.parametrize("plan", ["professional", "producer"])
    def test_a_covered_subscription_carries_the_subscription_coupon(self, plan):
        assert service(**LIVE)._promo_discounts(plan) == [{"coupon": "promo_49"}]

    def test_the_one_off_report_carries_its_own_coupon(self):
        """Not the subscription one. That coupon is repeating, and Stripe rejects a
        repeating coupon on a one-time payment — the customer could not buy at all."""
        assert service(**LIVE)._promo_discounts("single") == [{"coupon": "promo_49_once"}]

    def test_the_one_off_report_carries_nothing_until_its_coupon_exists(self):
        assert service(**{**LIVE, "STRIPE_PROMO_ONEOFF_COUPON_ID": ""})._promo_discounts("single") is None

    @pytest.mark.parametrize("plan", ["studio", "credit", "", None])
    def test_a_plan_outside_the_coupon_scope_carries_nothing(self, plan):
        """Not merely undiscounted. Stripe rejects a session carrying a coupon for
        a product it does not cover, so sending it anyway would stop the customer
        buying at all."""
        assert service(**LIVE)._promo_discounts(plan) is None

    def test_the_scope_is_case_insensitive(self):
        assert service(**LIVE)._promo_discounts("Professional") == [{"coupon": "promo_49"}]

    def test_a_blank_coupon_is_not_a_coupon(self):
        assert service(STRIPE_PROMO_COUPON_ID="   ")._promo_discounts("professional") is None

    def test_a_percentage_without_a_coupon_discounts_nothing(self):
        """The failure this guards: someone sets the advertised percentage and
        forgets the coupon, so the page promises 49% off and Stripe charges full
        price."""
        assert service(STRIPE_PROMO_PERCENT_OFF=49)._promo_discounts("professional") is None


class TestTheOneOffReportIsChargedWhatItIsShown:
    """The Single Report card is struck through when the offer covers it, so the
    session that sells it has to carry the coupon.

    It did not, and nothing caught it: the card had no plan key at all, so it fell
    through the frontend's scope check and advertised the subscription discount
    while ``create_credit_checkout_session`` sent no coupon. The page said $22 and
    Stripe charged $40.
    """

    def _captured_session(self, monkeypatch, **overrides):
        import stripe

        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="cs_test", url="https://checkout.test")

        monkeypatch.setattr(stripe.checkout.Session, "create", fake_create)
        service(**overrides).create_credit_checkout_session(
            price_id="price_single", user_email="a@b.test", user_id="u1",
        )
        return captured

    def test_the_credit_checkout_carries_the_coupon_when_the_offer_covers_it(self, monkeypatch):
        captured = self._captured_session(monkeypatch, **LIVE)
        assert captured["discounts"] == [{"coupon": "promo_49_once"}]

    def test_it_carries_nothing_when_the_offer_does_not_cover_it(self, monkeypatch):
        """Dropping "single" from the scope has to stop the coupon being sent, not
        just stop it being displayed — Stripe rejects a session carrying a coupon
        for a product outside its scope, which would break the purchase."""
        captured = self._captured_session(
            monkeypatch, **{**LIVE, "STRIPE_PROMO_PLANS": "professional,producer"}
        )
        assert "discounts" not in captured

    def test_it_carries_nothing_when_no_promotion_is_configured(self, monkeypatch):
        captured = self._captured_session(monkeypatch)
        assert "discounts" not in captured


class TestWhatTheSiteIsAllowedToAdvertise:
    def _promotion(self, **overrides):
        from app.modules.payments.router import active_promotion
        import asyncio

        # asyncio.run, not get_event_loop().run_until_complete. On Python 3.14 the
        # latter raises "There is no current event loop in thread 'MainThread'"
        # outside a running loop, which failed every test in this class regardless
        # of what it was asserting.
        return asyncio.run(active_promotion(settings_for(**overrides)))

    def test_nothing_is_advertised_without_a_coupon(self):
        assert self._promotion(STRIPE_PROMO_PERCENT_OFF=45)["active"] is False

    def test_nothing_is_advertised_without_a_percentage(self):
        assert self._promotion(STRIPE_PROMO_COUPON_ID="promo_45")["active"] is False

    def test_a_fully_configured_promotion_is_advertised(self):
        result = self._promotion(**LIVE)
        assert result == {
            "active": True,
            "percentOff": 49,
            "label": "49% off all subscription plans",
            # The site discounts exactly these and nothing else.
            "plans": ["producer", "professional", "single"],
        }

    def test_a_plan_with_no_coupon_behind_it_is_not_advertised(self):
        """The one-off report is configured as covered but has no coupon of its own
        yet. Advertising it would strike its price through at a saving the credit
        checkout could not give — the $22-shown-$40-charged bug, reintroduced by
        configuration instead of by code."""
        result = self._promotion(**{**LIVE, "STRIPE_PROMO_ONEOFF_COUPON_ID": ""})
        assert result["plans"] == ["producer", "professional"]

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
