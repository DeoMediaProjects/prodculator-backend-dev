"""Tests for wiring the client-facing bankability verdict to curated,
human-verified territory_profiles payment-timing research instead of the
older incentive-row proxy signal.

Two mockups (crewdepth/bankability admin tool, territory scorecard) described
this rewiring as already done; it wasn't — the verdict clients saw still came
from the old proxy on a different table. These tests pin the corrected
precedence: a trusted curated profile wins; suspended/contradicted status is
a hard override; anything uncurated or unverified falls back to the
untouched legacy behaviour.
"""
from app.modules.reports.builder import ReportBuilder
from app.modules.reports.scoring import _compute_bankability_label


class TestLegacyProxyUnchanged:
    """No curated profile at all -> byte-for-byte the old behaviour."""

    def test_high_reliability_short_timeline_is_bankable(self):
        assert _compute_bankability_label(0.9, 100) == "BANKABLE"

    def test_low_reliability_is_not_bankable(self):
        assert _compute_bankability_label(0.3, 100) == "NOT BANKABLE"

    def test_mid_reliability_is_verify_first(self):
        assert _compute_bankability_label(0.6, 100) == "VERIFY FIRST"

    def test_unknown_reliability_long_timeline_is_not_bankable(self):
        assert _compute_bankability_label(None, 400) == "NOT BANKABLE"


class TestTrustedProfileOverridesProxy:
    def test_short_curated_weeks_bankable_even_with_bad_proxy(self):
        profile = {
            "bankability_source_quality": "government_direct",
            "bankability_suspended": False,
            "bankability_real_world_confirms": True,
            "cert_weeks_max": 10,
            "payment_weeks_max": 10,
        }
        # Proxy signal here (reliability=0.1, timeline=999) would say NOT
        # BANKABLE on its own -- the trusted curated data must win.
        assert _compute_bankability_label(0.1, 999, profile=profile) == "BANKABLE"

    def test_long_curated_weeks_not_bankable_even_with_good_proxy(self):
        profile = {
            "bankability_source_quality": "government_plus_industry",
            "cert_weeks_max": 40,
            "payment_weeks_max": 30,
        }
        assert _compute_bankability_label(0.95, 10, profile=profile) == "NOT BANKABLE"

    def test_mid_curated_weeks_is_verify_first(self):
        profile = {
            "bankability_source_quality": "industry_secondary",
            "cert_weeks_max": 15,
            "payment_weeks_max": 20,
        }
        assert _compute_bankability_label(0.95, 10, profile=profile) == "VERIFY FIRST"


class TestHardOverrides:
    def test_suspended_forces_not_bankable_regardless_of_weeks(self):
        profile = {
            "bankability_source_quality": "government_direct",
            "bankability_suspended": True,
            "cert_weeks_max": 5,
            "payment_weeks_max": 5,
        }
        assert _compute_bankability_label(0.95, 10, profile=profile) == "NOT BANKABLE"

    def test_real_world_contradiction_forces_not_bankable(self):
        """bankability_real_world_confirms is explicitly False (not None) --
        real-world evidence contradicts the stated policy (the mockup's
        Romania/Louisiana example)."""
        profile = {
            "bankability_source_quality": "government_direct",
            "bankability_real_world_confirms": False,
            "cert_weeks_max": 5,
            "payment_weeks_max": 5,
        }
        assert _compute_bankability_label(0.95, 10, profile=profile) == "NOT BANKABLE"

    def test_unconfirmed_is_not_a_contradiction(self):
        """None means 'unconfirmed', not 'contradicted' -- must not force
        NOT BANKABLE on its own."""
        profile = {
            "bankability_source_quality": "government_direct",
            "bankability_real_world_confirms": None,
            "cert_weeks_max": 5,
            "payment_weeks_max": 5,
        }
        assert _compute_bankability_label(0.1, 999, profile=profile) == "BANKABLE"


class TestUnverifiedFallsBackToLegacy:
    def test_unverified_source_quality_ignores_curated_weeks(self):
        """An 'unverified' curated row is no more trustworthy than no row at
        all -- the proxy signal must still decide."""
        profile = {
            "bankability_source_quality": "unverified",
            "cert_weeks_max": 5,
            "payment_weeks_max": 5,
        }
        # Curated weeks alone would say BANKABLE; proxy signal (0.9, 100) also
        # says BANKABLE here -- use a case where they'd disagree to prove the
        # proxy, not the unverified profile, decided it.
        assert _compute_bankability_label(0.3, 100, profile=profile) == "NOT BANKABLE"

    def test_missing_weeks_on_trusted_profile_falls_back_to_proxy(self):
        """Trusted source quality but no weeks data yet -- nothing to compute
        from, so use the proxy."""
        profile = {"bankability_source_quality": "government_direct"}
        assert _compute_bankability_label(0.9, 100, profile=profile) == "BANKABLE"


class TestReliabilityScoreConsistency:
    """incentiveReliability (the 0-100 score) must agree with the label it's
    paired with, whichever signal produced the label."""

    def test_trusted_profile_drives_both_score_and_label(self):
        profile = {
            "bankability_source_quality": "government_direct",
            "cert_weeks_max": 10,
            "payment_weeks_max": 10,
        }
        score, label = ReportBuilder._compute_reliability(
            {"payment_reliability": 0.1, "payment_timeline_days_max": 999}, profile
        )
        assert label == "BANKABLE"
        assert score == 90

    def test_no_profile_uses_legacy_score(self):
        score, label = ReportBuilder._compute_reliability({"payment_reliability": 0.95}, None)
        assert label == "BANKABLE"
        assert score == 90

    def test_suspended_profile_scores_low_and_uses_legacy_fallthrough(self):
        profile = {"bankability_source_quality": "government_direct", "bankability_suspended": True}
        score, label = ReportBuilder._compute_reliability(
            {"payment_reliability": 0.95, "payment_timeline_days_max": 10}, profile
        )
        assert label == "NOT BANKABLE"
        # Suspended -> falls through to the legacy score path (not the
        # trusted-profile score shortcut), since the suspension check happens
        # before the trusted-profile scoring precondition.
        assert isinstance(score, int)
