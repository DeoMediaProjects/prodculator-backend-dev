"""Tests for B2B monthly aggregate storage and quarterly/yearly composition.

Implementation Plan section 3: "monthly is the atomic unit"; quarterly composes
from three stored months rather than re-querying; yearly unlocks once twelve
stored months exist.

The load-bearing invariant here is that stored monthly facts are RAW and
UNSUPPRESSED. Privacy floors live in the renderer. If suppression happened at
storage time, a segment appearing 4x in each of three months (12 total, well
above the floor of 5) would be permanently invisible at quarterly level.
"""
from datetime import date, datetime
from unittest.mock import MagicMock

from app.core.config import Settings
from app.modules.b2b.service import (
    PRIVACY_MIN_SEGMENT,
    B2BService,
    month_end,
    month_start,
    months_in_range,
)


def _service():
    svc = B2BService(MagicMock(), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))
    svc.email_service = MagicMock()
    svc.pdf_service = MagicMock()
    return svc


def _signal(territory: str, submission_date: str, **overrides):
    row = {
        "territory": territory,
        "submission_date": submission_date,
        "format": "Feature Film",
        "genres": ["Drama"],
        "budget_range": "1M-5M",
        "camera_equipment": ["ARRI"],
    }
    row.update(overrides)
    return row


def _counts(facts, title):
    section = next(s for s in facts["sections"] if s["title"] == title)
    return section["counts"]


def _section(metrics, title):
    return next(s for s in metrics["sections"] if s["title"] == title)


# ---------------------------------------------------------------- raw storage


def test_raw_facts_keep_sub_threshold_counts():
    """Storage must NOT apply the segment floor."""
    svc = _service()
    rows = [_signal("Ireland", "2026-01-05")] * 4  # 4 < PRIVACY_MIN_SEGMENT

    facts = svc._build_raw_facts("camera_equipment", rows)

    assert facts["signal_count"] == 4
    assert _counts(facts, "Production Volume by Territory") == {"Ireland": 4}


def test_renderer_still_suppresses_sub_threshold_segments():
    """Floors are applied at render time, not storage time."""
    svc = _service()
    rows = [_signal("United Kingdom", "2026-01-05")] * 8 + [_signal("Ireland", "2026-01-06")] * 4

    facts = svc._build_raw_facts("camera_equipment", rows)
    metrics = svc._facts_to_metrics(
        "camera_equipment", facts, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
    )

    territory = _section(metrics, "Production Volume by Territory")
    assert [row["label"] for row in territory["rows"]] == ["United Kingdom"]
    assert any(s["label"] == "Ireland" for s in metrics["suppressed_segments"])


# ------------------------------------------------------------- composition


def test_composition_sums_counters_across_months():
    svc = _service()
    facts = [
        svc._build_raw_facts("camera_equipment", [_signal("Spain", "2026-01-05")] * 3),
        svc._build_raw_facts("camera_equipment", [_signal("Spain", "2026-02-05")] * 2),
    ]

    composed = svc.compose_facts(facts)

    assert composed["signal_count"] == 5
    assert _counts(composed, "Production Volume by Territory") == {"Spain": 5}


def test_segment_below_floor_monthly_becomes_visible_quarterly():
    """THE key regression test for the storage/render split.

    Malta appears 4x per month -- below the segment floor of 5 in every single
    month -- but 12x across the quarter, so it must appear in the quarterly
    report. Storing rendered (suppressed) monthly metrics would lose it forever.
    """
    svc = _service()
    monthly_facts = []
    for month in ("2026-01", "2026-02", "2026-03"):
        rows = [_signal("Malta", f"{month}-05")] * 4 + [_signal("Spain", f"{month}-06")] * 6
        monthly_facts.append(svc._build_raw_facts("camera_equipment", rows))

    # Malta is suppressed in each individual month...
    for facts in monthly_facts:
        monthly = svc._facts_to_metrics(
            "camera_equipment", facts, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        labels = [r["label"] for r in _section(monthly, "Production Volume by Territory")["rows"]]
        assert "Malta" not in labels

    # ...but visible once the quarter is composed (4 + 4 + 4 = 12 >= 5).
    composed = svc.compose_facts(monthly_facts)
    quarterly = svc._facts_to_metrics(
        "camera_equipment", composed, period_start=date(2026, 1, 1), period_end=date(2026, 3, 31)
    )
    territory = _section(quarterly, "Production Volume by Territory")
    malta = next(r for r in territory["rows"] if r["label"] == "Malta")
    assert malta["count"] == 12
    assert 12 >= PRIVACY_MIN_SEGMENT


def test_headcount_mean_recomputed_from_sum_not_averaged():
    """Averaging monthly averages would weight a 1-signal month like a 9-signal one."""
    svc = _service()
    light = svc._build_raw_facts(
        "production_services", [_signal("Spain", "2026-01-05", crew_size=100)]
    )
    heavy = svc._build_raw_facts(
        "production_services", [_signal("Spain", "2026-02-05", crew_size=10)] * 9
    )

    composed = svc.compose_facts([light, heavy])
    stats = next(s for s in composed["sections"] if s["kind"] == "headcount")["stats"]

    # True mean = (100 + 90) / 10 = 19.0, NOT (100 + 10) / 2 = 55.0
    assert stats["values_count"] == 10
    assert stats["sum"] == 190
    assert stats["max"] == 100
    assert stats["sum"] / stats["values_count"] == 19.0

    metrics = svc._facts_to_metrics(
        "production_services", composed, period_start=date(2026, 1, 1), period_end=date(2026, 2, 28)
    )
    headcount = _section(metrics, "Total Headcount Trend Analysis")
    average = next(r for r in headcount["rows"] if r["label"] == "Average declared headcount")
    assert average["count"] == 19.0


def test_composition_preserves_section_order_and_shape():
    svc = _service()
    rows = [_signal("Spain", "2026-01-05")] * 12
    single = svc._build_raw_facts("camera_equipment", rows)
    composed = svc.compose_facts([single])

    direct = svc._facts_to_metrics(
        "camera_equipment", single, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
    )
    via_composition = svc._facts_to_metrics(
        "camera_equipment", composed, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
    )

    assert [s["title"] for s in direct["sections"]] == [s["title"] for s in via_composition["sections"]]
    assert direct["source_signal_count"] == via_composition["source_signal_count"] == 12


# ------------------------------------------------------- month-on-month


def test_month_on_month_reports_absolute_and_percentage_deltas():
    svc = _service()
    current = svc._build_raw_facts("camera_equipment", [_signal("Spain", "2026-02-05")] * 15)
    previous = svc._build_raw_facts("camera_equipment", [_signal("Spain", "2026-01-05")] * 10)

    comparison = svc.month_on_month(current, previous)

    spain = next(r for r in comparison["rows"] if r["label"].endswith("Spain") and "Territory" in r["label"])
    assert spain["current"] == 15
    assert spain["previous"] == 10
    assert spain["delta"] == 5
    assert spain["percentage_change"] == 50.0


def test_month_on_month_handles_new_segment_with_no_prior():
    svc = _service()
    current = svc._build_raw_facts("camera_equipment", [_signal("Spain", "2026-02-05")] * 8)
    previous = svc._build_raw_facts("camera_equipment", [])

    comparison = svc.month_on_month(current, previous)

    spain = next(r for r in comparison["rows"] if "Spain" in r["label"] and "Territory" in r["label"])
    assert spain["previous"] == 0
    assert spain["delta"] == 8
    # No prior base => percentage is undefined rather than a bogus 0 or infinity.
    assert spain["percentage_change"] is None


def test_month_on_month_does_not_leak_sub_threshold_segments():
    """A label below the floor in BOTH periods must not appear in the comparison."""
    svc = _service()
    current = svc._build_raw_facts("camera_equipment", [_signal("Malta", "2026-02-05")] * 3)
    previous = svc._build_raw_facts("camera_equipment", [_signal("Malta", "2026-01-05")] * 2)

    comparison = svc.month_on_month(current, previous)

    labels = [r["label"] for r in (comparison or {"rows": []})["rows"]]
    assert not any("Malta" in label for label in labels)


def test_month_on_month_returns_none_when_nothing_comparable():
    svc = _service()
    empty = svc._build_raw_facts("camera_equipment", [])
    assert svc.month_on_month(empty, empty) is None


# ------------------------------------------------------------ month helpers


def test_month_helpers():
    assert month_start(date(2026, 3, 17)) == date(2026, 3, 1)
    assert month_end(date(2026, 2, 3)) == date(2026, 2, 28)
    assert month_end(date(2024, 2, 3)) == date(2024, 2, 29)  # leap year
    assert months_in_range(date(2026, 1, 15), date(2026, 3, 2)) == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]
    assert months_in_range(date(2026, 1, 1), date(2026, 1, 31)) == [date(2026, 1, 1)]
    # Spanning a year boundary.
    assert months_in_range(date(2025, 12, 1), date(2026, 2, 1)) == [
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]


# --------------------------------------------------------- storage plumbing


def test_build_monthly_aggregate_upserts_raw_facts():
    svc = _service()
    svc._load_signals = MagicMock(return_value=[_signal("Ireland", "2026-01-05")] * 4)

    record = svc.build_monthly_aggregate("camera_equipment", date(2026, 1, 17))

    # Normalised to the first of the month, as a real date (SQLite Date columns
    # reject ISO strings).
    assert record["period_month"] == date(2026, 1, 1)
    assert record["signal_count"] == 4
    # Sub-threshold count survived into storage.
    assert _counts(record["facts"], "Production Volume by Territory") == {"Ireland": 4}

    svc._load_signals.assert_called_once_with(date(2026, 1, 1), date(2026, 1, 31))
    upsert = svc.db.table.return_value.upsert
    assert upsert.call_args.kwargs["on_conflict"] == "product_type,period_month"


def test_compose_from_months_reuses_stored_months_without_requerying():
    """Quarterly must compose from storage, not re-query the signal pool."""
    svc = _service()
    stored = {}
    for month in (date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)):
        rows = [_signal("Spain", f"{month.isoformat()[:7]}-05")] * 6
        stored[month.isoformat()] = {"facts": svc._build_raw_facts("camera_equipment", rows)}
    svc.get_monthly_aggregates = MagicMock(return_value=stored)
    svc._load_signals = MagicMock(side_effect=AssertionError("must not re-query signals"))

    metrics = svc.compose_from_months(
        "camera_equipment", [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    )

    assert metrics["source_signal_count"] == 18
    assert metrics["period_start"] == "2026-01-01"
    assert metrics["period_end"] == "2026-03-31"
    assert metrics["composed_from_months"] == ["2026-01-01", "2026-02-01", "2026-03-01"]


def test_compose_from_months_builds_missing_months():
    svc = _service()
    svc.get_monthly_aggregates = MagicMock(return_value={})
    svc.build_monthly_aggregate = MagicMock(
        side_effect=lambda product_type, month: {
            "facts": svc._build_raw_facts("camera_equipment", [_signal("Spain", "2026-01-05")] * 6)
        }
    )

    metrics = svc.compose_from_months("camera_equipment", [date(2026, 1, 1), date(2026, 2, 1)])

    assert svc.build_monthly_aggregate.call_count == 2
    assert metrics["source_signal_count"] == 12


def test_yearly_requires_twelve_stored_months():
    svc = _service()
    months = months_in_range(date(2026, 1, 1), date(2026, 12, 1))
    assert len(months) == 12

    svc.get_monthly_aggregates = MagicMock(
        return_value={m.isoformat(): {"facts": {}} for m in months[:11]}
    )
    assert svc.yearly_available("camera_equipment", months) is False

    svc.get_monthly_aggregates = MagicMock(
        return_value={m.isoformat(): {"facts": {}} for m in months}
    )
    assert svc.yearly_available("camera_equipment", months) is True


def test_backfill_stores_every_month_in_range():
    svc = _service()
    svc.build_monthly_aggregate = MagicMock(return_value={})

    count = svc.backfill_monthly_aggregates("camera_equipment", date(2026, 1, 10), date(2026, 4, 2))

    assert count == 4
    stored_months = [c.args[1] for c in svc.build_monthly_aggregate.call_args_list]
    assert stored_months == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]


# ------------------------------------------------------- delivery routing


def test_whole_month_period_composes_and_compares():
    svc = _service()
    svc.compose_from_months = MagicMock(return_value={"insufficient_data": False})

    svc.build_period_metrics(
        product_type="camera_equipment",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
    )

    months = svc.compose_from_months.call_args.args[1]
    assert months == [date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]
    # Quarterly compares against the immediately preceding quarter.
    assert svc.compose_from_months.call_args.kwargs["compare_to"] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]


def test_scheduled_delivery_periods_are_whole_completed_months():
    """Scheduled periods must align to completed calendar months.

    The previous [prev-month-day-N .. this-month-day-N] range both bypassed
    stored-month composition and counted a boundary-day signal twice.
    """
    from app.modules.b2b.service import add_months, interval_months

    due_at = datetime(2026, 7, 1, 4, 30)
    for frequency, expected_start in (
        ("monthly", date(2026, 6, 1)),
        ("quarterly", date(2026, 4, 1)),
    ):
        months = interval_months(frequency)
        period_start = month_start(add_months(due_at, -months))
        period_end = month_end(add_months(due_at, -1))
        assert period_start == expected_start
        assert period_end == date(2026, 6, 30)
        assert len(months_in_range(period_start, period_end)) == months


def test_monthly_close_stores_closed_month_for_every_product(monkeypatch):
    from app.modules.b2b import service as service_module

    svc = _service()
    monkeypatch.setattr(service_module, "create_client", lambda: MagicMock())
    monkeypatch.setattr(service_module, "B2BService", lambda db, settings: svc)
    svc.build_monthly_aggregate = MagicMock(return_value={})

    stored = service_module.run_b2b_monthly_aggregate_close(
        Settings(_env_file=None, JWT_SECRET_KEY="x" * 64), today=date(2026, 7, 15)
    )

    assert stored == len(service_module.B2B_PRODUCTS)
    # Always the previous (closed) month, never the in-flight one.
    assert {c.args[1] for c in svc.build_monthly_aggregate.call_args_list} == {date(2026, 6, 1)}


def test_monthly_close_continues_when_one_product_fails(monkeypatch):
    from app.modules.b2b import service as service_module

    svc = _service()
    monkeypatch.setattr(service_module, "create_client", lambda: MagicMock())
    monkeypatch.setattr(service_module, "B2BService", lambda db, settings: svc)
    calls = []

    def flaky(product_type, month):
        calls.append(product_type)
        if len(calls) == 1:
            raise RuntimeError("signal pool unavailable")
        return {}

    svc.build_monthly_aggregate = flaky

    stored = service_module.run_b2b_monthly_aggregate_close(
        Settings(_env_file=None, JWT_SECRET_KEY="x" * 64), today=date(2026, 7, 15)
    )

    assert len(calls) == len(service_module.B2B_PRODUCTS)
    assert stored == len(service_module.B2B_PRODUCTS) - 1


def test_ad_hoc_range_falls_back_to_direct_query():
    """A range that does not align to month boundaries cannot use stored months."""
    svc = _service()
    svc.build_metrics = MagicMock(return_value={})
    svc.compose_from_months = MagicMock(side_effect=AssertionError("should not compose"))

    svc.build_period_metrics(
        product_type="camera_equipment",
        period_start=date(2026, 4, 10),
        period_end=date(2026, 5, 20),
    )

    svc.build_metrics.assert_called_once()
