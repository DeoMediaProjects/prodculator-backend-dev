"""Unit tests for the pure scoring helpers extracted from builder.py."""
import json

from app.modules.reports.scoring import (
    _compute_bankability_label,
    _incentive_qualification_score,
    _incentive_rate_score,
    _incentive_stability_score,
)


class TestBankabilityLabel:
    def test_low_reliability_is_not_bankable(self):
        assert _compute_bankability_label(0.40, 90) == "NOT BANKABLE"

    def test_unknown_reliability_with_long_timeline_is_not_bankable(self):
        assert _compute_bankability_label(None, 400) == "NOT BANKABLE"

    def test_high_reliability_short_timeline_is_bankable(self):
        assert _compute_bankability_label(0.85, 120) == "BANKABLE"

    def test_high_reliability_long_timeline_verify_first(self):
        assert _compute_bankability_label(0.85, 300) == "VERIFY FIRST"

    def test_mid_reliability_defaults_to_verify_first(self):
        assert _compute_bankability_label(0.65, 90) == "VERIFY FIRST"


class TestIncentiveRateScore:
    def test_non_positive_rate_is_zero(self):
        assert _incentive_rate_score(0) == 0.0
        assert _incentive_rate_score(-5) == 0.0

    def test_breakpoint_value_is_exact(self):
        # 30% is a defined breakpoint -> 65
        assert _incentive_rate_score(30) == 65

    def test_interpolates_between_breakpoints(self):
        # Between 20 (40) and 30 (65): midpoint 25 -> 52.5
        assert _incentive_rate_score(25) == 52.5

    def test_above_top_breakpoint_caps_at_100(self):
        assert _incentive_rate_score(150) == 100.0


class TestQualificationScore:
    def test_clamped_to_bounds(self):
        score = _incentive_qualification_score({})
        assert 20 <= score <= 85

    def test_nationality_requirement_lowers_score(self):
        with_req = _incentive_qualification_score(
            {"nationality_requirements": json.dumps(["UK resident"])}
        )
        without_req = _incentive_qualification_score({})
        assert with_req < without_req


class TestStabilityScore:
    def test_inactive_programme_is_low(self):
        assert _incentive_stability_score({"status": "closed"}) == 20

    def test_frozen_warning_is_low(self):
        row = {"status": "active", "warnings_json": json.dumps(["Programme frozen for 2026"])}
        assert _incentive_stability_score(row) == 20

    def test_active_high_reliability_is_high(self):
        row = {"status": "active", "payment_reliability": 0.9}
        assert _incentive_stability_score(row) == 90
