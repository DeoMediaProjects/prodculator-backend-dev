"""Tests for plan enum helpers and normalization."""

from app.models.enums import PlanType, normalize_plan


def test_normalize_plan_maps_single_to_professional():
    assert normalize_plan("single") == "professional"


def test_normalize_plan_keeps_professional():
    assert normalize_plan("professional") == "professional"


def test_normalize_plan_keeps_free():
    assert normalize_plan("free") == "free"


def test_normalize_plan_keeps_studio():
    assert normalize_plan("studio") == "studio"


def test_normalize_plan_keeps_producer():
    assert normalize_plan("producer") == "producer"


def test_normalize_plan_unknown_passes_through():
    assert normalize_plan("enterprise") == "enterprise"


def test_plan_type_enum_has_professional():
    assert PlanType.PROFESSIONAL == "professional"
    assert PlanType.SINGLE == "single"
    assert PlanType.FREE == "free"
    assert PlanType.STUDIO == "studio"


def test_plan_type_enum_has_producer():
    assert PlanType.PRODUCER == "producer"
