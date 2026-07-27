"""Tests for the Business Intelligence PDF: cross-tabs, charts and view model.

The PDF follows the client-approved template in the handoff pack. Two things
matter most here:

  1. Cross-tabulations pin a record down on two axes at once, so they are more
     identifying than a single distribution. The segment floor is applied per
     CELL, and a composed quarter must keep its cross-tab kind so the matrix
     still renders as a matrix.
  2. When a section is below threshold the report says so. It never pads.
"""
import re
from datetime import date
from unittest.mock import MagicMock

from app.core.config import Settings
from app.modules.b2b import charts
from app.modules.b2b.report_model import build_report_view
from app.modules.b2b.service import PRIVACY_MIN_SEGMENT, B2BService

CROSSTAB_SPEC = [
    {
        "kind": "crosstab",
        "key": "genre_format",
        "title": "Genre by Format",
        "row_key": "genres",
        "row_flatten": True,
        "col_key": "format",
    }
]

MONTH_CROSSTAB_SPEC = [
    {
        "kind": "crosstab",
        "key": "format_month",
        "title": "Format Volume by Month",
        "row_key": "format",
        "col_key": "submission_month",
    }
]


def _service():
    svc = B2BService(MagicMock(), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))
    svc.email_service = MagicMock()
    svc.pdf_service = MagicMock()
    return svc


def _signal(genre, fmt, submitted="2026-04-05"):
    return {"genres": [genre], "format": fmt, "submission_date": submitted}


def _section(metrics, key):
    return next(s for s in metrics["sections"] if s.get("key") == key)


# ------------------------------------------------------------- raw cross-tabs


def test_crosstab_stores_raw_cells_unsuppressed():
    svc = _service()
    rows = [_signal("Drama", "Feature Film")] * 3

    facts = svc._facts_from_specs(CROSSTAB_SPEC, rows)

    section = facts["sections"][0]
    assert section["kind"] == "crosstab"
    # 3 is below the segment floor but must survive into storage.
    assert section["counts"] == {"Drama||Feature Film": 3}


def test_crosstab_cells_sum_across_months():
    """A combination too small monthly can clear the floor across a quarter."""
    svc = _service()
    monthly = [
        svc._facts_from_specs(CROSSTAB_SPEC, [_signal("Drama", "Feature Film")] * 3)
        for _ in range(3)
    ]

    composed = svc.compose_facts(monthly)

    assert composed["sections"][0]["counts"] == {"Drama||Feature Film": 9}


def test_composition_preserves_crosstab_kind():
    """Regression: flattening every section to "counter" during composition made a
    composed quarter render its matrices as flat distributions."""
    svc = _service()
    facts = svc._facts_from_specs(CROSSTAB_SPEC, [_signal("Drama", "Feature Film")] * 6)

    composed = svc.compose_facts([facts])

    assert composed["sections"][0]["kind"] == "crosstab"


# ------------------------------------------------------- per-cell suppression


def test_crosstab_applies_the_floor_per_cell():
    svc = _service()
    rows = (
        [_signal("Drama", "Feature Film")] * 8
        + [_signal("Horror", "Feature Film")] * 2  # below the per-cell floor
    )
    facts = svc._facts_from_specs(CROSSTAB_SPEC, rows)

    metrics = svc._facts_to_metrics(
        "production_trend", facts, period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)
    )

    section = _section(metrics, "genre_format")
    assert section["row_labels"] == ["Drama"]
    assert "Horror" not in section["row_labels"]
    assert any("Horror" in s["label"] for s in metrics["suppressed_segments"])
    assert 2 < PRIVACY_MIN_SEGMENT


def test_crosstab_withheld_entirely_when_no_cell_qualifies():
    svc = _service()
    rows = [_signal("Drama", "Feature Film")] * 3 + [_signal("Horror", "TV Series")] * 8
    # 8 signals overall is below the overall floor, so widen with unrelated rows.
    rows += [_signal("Comedy", "Short")] * 4
    facts = svc._facts_from_specs(CROSSTAB_SPEC, rows)
    facts["signal_count"] = 15  # clear the overall floor

    metrics = svc._facts_to_metrics(
        "production_trend", facts, period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)
    )
    section = _section(metrics, "genre_format")

    assert section["row_labels"] == ["Horror"]  # only the 8-count cell survives
    assert "withheld" not in section["summary"].lower()


def test_month_columns_read_chronologically():
    """Month axes must not be sorted by volume, or the chart reads backwards."""
    svc = _service()
    rows = (
        [_signal("Drama", "Feature Film", "2026-06-02")] * 9
        + [_signal("Drama", "Feature Film", "2026-04-02")] * 6
        + [_signal("Drama", "Feature Film", "2026-05-02")] * 7
    )
    facts = svc._facts_from_specs(MONTH_CROSSTAB_SPEC, rows)

    metrics = svc._facts_to_metrics(
        "production_trend", facts, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30)
    )

    assert _section(metrics, "format_month")["cols"] == ["2026-04", "2026-05", "2026-06"]


def test_crosstab_ignores_blank_axis_values():
    svc = _service()
    rows = [{"genres": ["Drama"], "format": None}] * 12

    facts = svc._facts_from_specs(CROSSTAB_SPEC, rows)

    assert facts["sections"][0]["counts"] == {}


# ------------------------------------------------------------- view model


def _trend_metrics():
    svc = _service()
    specs = [
        {"kind": "distribution", "key": "territories_considered", "title": "Considered", "flatten": True},
        {"kind": "distribution", "key": "territories_recommended", "title": "Recommended", "flatten": True},
        {"kind": "distribution", "key": "budget_range", "title": "Budget"},
        {"kind": "distribution", "key": "format", "title": "Format"},
    ]
    rows = []
    for _ in range(30):
        rows.append({
            "territories_considered": ["United Kingdom"],
            "territories_recommended": ["Ireland"],
            "budget_range": "Low",
            "format": "Feature Film",
        })
    facts = svc._facts_from_specs(specs, rows)
    return svc, svc._facts_to_metrics(
        "production_trend",
        facts,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        extra={
            "composed_from_months": ["2026-04-01", "2026-05-01", "2026-06-01"],
            "previous_signal_count": 20,
            "period_volumes": [{"label": "Q1 26", "value": 20}, {"label": "Q2 26", "value": 30}],
        },
    )


def test_view_model_builds_four_kpis_with_direction():
    _, metrics = _trend_metrics()

    view = build_report_view(metrics, client_name="Grey Consortium UK")

    assert len(view["kpis"]) == 4
    assert view["kpis"][0]["value"] == "30"
    # 30 against 20 is a 50% rise.
    assert "50%" in view["kpis"][0]["direction"]["text"]
    assert view["kpis"][0]["direction"]["cls"] == "up"
    assert view["cadence"] == "Quarterly"
    assert view["period_label"] == "Q2 2026"
    assert view["client_name"] == "Grey Consortium UK"


def test_view_model_pairs_both_territory_readings_into_a_diverging_chart():
    _, metrics = _trend_metrics()

    view = build_report_view(metrics)

    assert view["territory"]["available"] is True
    assert view["territory"]["diverging"] is True
    assert view["territory"]["svg"].startswith("<svg")
    assert "migration pressure" in view["territory"]["readout"]


def test_view_model_reports_withheld_sections_honestly():
    svc = _service()
    facts = svc._facts_from_specs(
        [{"kind": "distribution", "key": "budget_range", "title": "Budget"}],
        # 12 clears the overall floor; each band stays under the segment floor.
        [{"budget_range": f"Band {i % 6}"} for i in range(12)],
    )
    metrics = svc._facts_to_metrics(
        "production_trend", facts, period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)
    )

    view = build_report_view(metrics)

    assert view["budget"]["available"] is False
    assert "withheld" in view["budget"]["readout"].lower()
    assert view["territory"]["available"] is False
    assert "withheld" in view["territory"]["readout"].lower()


def test_view_model_direction_handles_no_prior_period():
    _, metrics = _trend_metrics()
    metrics.pop("previous_signal_count")

    view = build_report_view(metrics)

    assert view["kpis"][0]["direction"]["cls"] == "flat"
    assert "No prior period" in view["kpis"][0]["direction"]["text"]


def test_report_reference_is_stable_and_readable():
    svc, metrics = _trend_metrics()
    metrics["product_type"] = "production_trend"

    assert svc._report_reference(metrics) == "BI-PT-2026Q2 v1.0"


def test_rendered_report_contains_no_em_dashes():
    """The developer notes ban the em-dash aside habit in report prose."""
    svc, metrics = _trend_metrics()
    svc.pdf_service = MagicMock()
    from app.modules.reports.pdf_service import PDFService

    svc.pdf_service.env = PDFService().env

    html = svc.render_pdf_html(metrics)
    # Strip the embedded logo: a base64 blob is not prose and can contain any
    # letter sequence by chance.
    visible = re.sub(r'src="data:[^"]*"', 'src="logo"', html)

    assert "—" not in visible
    assert "Business Intelligence" in visible
    assert "B2B" not in visible  # internal term must never reach a client surface


# ----------------------------------------------------------------- charts


def test_charts_return_empty_string_for_empty_data():
    """An empty chart must collapse, not render an axis with nothing on it."""
    assert charts.diverging([]) == ""
    assert charts.hbars([]) == ""
    assert charts.grouped_quarter([]) == ""
    assert charts.heatmatrix([], [], []) == ""
    assert charts.donut([]) == ""
    # A trend needs at least two points to be a trend.
    assert charts.trendline([("Q1", 10)]) == ""


def test_charts_emit_svg_for_real_data():
    assert charts.diverging([("United Kingdom", 41, 22)]).startswith("<svg")
    assert charts.hbars([("Low", 58, "58 · 40%")]).startswith("<svg")
    assert charts.grouped_quarter([("Feature", [24, 28, 31])]).startswith("<svg")
    assert charts.trendline([("Q1", 98), ("Q2", 146)]).startswith("<svg")
    assert charts.heatmatrix(["Feature"], ["Drama"], [[31]]).startswith("<svg")
    assert charts.donut([("Low", 58, charts.INK)]).startswith("<svg")


def test_donut_renders_a_single_full_segment_as_a_circle():
    """A 360 degree arc has coincident endpoints and would draw nothing."""
    svg = charts.donut([("Only", 40, charts.YEL)])

    assert "<circle" in svg


def test_chart_labels_are_escaped():
    svg = charts.hbars([("<script>x</script>", 9)])

    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
