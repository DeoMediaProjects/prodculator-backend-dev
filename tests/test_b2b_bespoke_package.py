"""Tests for bespoke admin-composed packages (SOW 4.5 / Slice C).

The point of routing bespoke composition through B2BService._facts_from_specs and
_facts_to_metrics is that there is no second suppression implementation to get
wrong: an admin-composed report inherits the identical privacy floors as a
standard product, and the sufficiency preview cannot drift from what renders.
"""
from datetime import date
from unittest.mock import MagicMock

from app.core.config import Settings
from app.modules.b2b.package_service import (
    PRIVACY_MIN_SEGMENT,
    DatasetFetcher,
    PackageService,
)
from app.modules.b2b.service import B2BService


def _pkg(rows, datasets=None):
    b2b = B2BService(MagicMock(), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))
    b2b.email_service = MagicMock()
    b2b.pdf_service = MagicMock()
    b2b._load_signals = MagicMock(return_value=rows)
    fetcher = datasets or MagicMock(spec=DatasetFetcher)
    if datasets is None:
        fetcher.count.return_value = 0
        fetcher.fetch.return_value = []
    return PackageService(b2b, fetcher), b2b


def _signal(**overrides):
    row = {
        "home_country": "United Kingdom",
        "genres": ["Drama"],
        "budget_range": "1M-5M",
        "format": "Feature Film",
        "submission_date": "2026-01-15",
    }
    row.update(overrides)
    return row


def _section(metrics, title):
    return next(s for s in metrics["sections"] if s["title"] == title)


# --------------------------------------------------- floors are inherited


def test_bespoke_composition_applies_the_same_segment_floor():
    rows = [_signal(home_country="United Kingdom")] * 8 + [_signal(home_country="Ireland")] * 4
    pkg, _ = _pkg(rows)

    metrics = pkg.compose(
        section_keys=["sig_territory_home"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        title="Grey Consortium Bespoke Q1",
    )

    section = _section(metrics, "Production Volume by Home Country")
    assert [r["label"] for r in section["rows"]] == ["United Kingdom"]
    assert any(s["label"] == "Ireland" for s in metrics["suppressed_segments"])
    assert 4 < PRIVACY_MIN_SEGMENT


def test_bespoke_composition_holds_below_the_overall_floor():
    pkg, _ = _pkg([_signal()] * 4)

    metrics = pkg.compose(
        section_keys=["sig_territory_home"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        title="Too Thin",
    )

    assert metrics["insufficient_data"] is True
    assert [s["title"] for s in metrics["sections"]] == ["Privacy Threshold"]


def test_bespoke_title_and_client_name_carry_through():
    pkg, _ = _pkg([_signal()] * 12)

    metrics = pkg.compose(
        section_keys=["sig_territory_home"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        title="Grey Consortium — Strategic Trend Q1",
        client_name="Grey Consortium UK",
    )

    assert metrics["title"] == "Grey Consortium — Strategic Trend Q1"
    assert metrics["client_name"] == "Grey Consortium UK"
    assert metrics["composed_section_keys"] == ["sig_territory_home"]


def test_unknown_section_keys_are_reported_not_silently_dropped():
    pkg, _ = _pkg([_signal()] * 12)

    metrics = pkg.compose(
        section_keys=["sig_territory_home", "sig_does_not_exist"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        title="X",
    )

    assert metrics["unknown_section_keys"] == ["sig_does_not_exist"]


# ------------------------------------------------- context-only packages


def test_context_only_package_is_not_held_on_signal_volume():
    """Privacy floors govern signal-derived output only.

    A package of purely curated market context has no personal data to protect,
    so a quiet period must not hold it.
    """
    fetcher = MagicMock(spec=DatasetFetcher)
    fetcher.fetch.return_value = [
        {"territory": "United Kingdom", "program": "AVEC", "rate": "25.5%", "cap": "None"},
    ]
    fetcher.display_columns.return_value = ["territory", "program", "rate", "cap"]
    pkg, b2b = _pkg([], datasets=fetcher)

    metrics = pkg.compose(
        section_keys=["ctx_incentives"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        title="Market Context Only",
    )

    assert metrics["insufficient_data"] is False
    section = _section(metrics, "Incentive Programme Landscape")
    assert section["kind"] == "dataset"
    assert section["records"][0]["program"] == "AVEC"
    # No signal sections requested => the signal pool is never even queried.
    b2b._load_signals.assert_not_called()


def test_context_sections_are_prepended_as_part_a():
    fetcher = MagicMock(spec=DatasetFetcher)
    fetcher.fetch.return_value = [{"territory": "UK", "program": "AVEC"}]
    fetcher.display_columns.return_value = ["territory", "program"]
    pkg, _ = _pkg([_signal()] * 12, datasets=fetcher)

    metrics = pkg.compose(
        section_keys=["sig_territory_home", "ctx_incentives"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        title="Two Part",
    )

    titles = [s["title"] for s in metrics["sections"]]
    assert titles[0] == "Incentive Programme Landscape"  # Part A leads
    assert "Production Volume by Home Country" in titles


def test_empty_dataset_renders_an_explicit_note_not_a_blank_table():
    fetcher = MagicMock(spec=DatasetFetcher)
    fetcher.fetch.return_value = []
    pkg, _ = _pkg([_signal()] * 12, datasets=fetcher)

    metrics = pkg.compose(
        section_keys=["ctx_festivals"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        title="Ctx",
    )

    section = _section(metrics, "Festival Calendar & Deadlines")
    assert section["records"] == []
    assert "no festivals records" in section["summary"].lower()


# ------------------------------------------- preview / render consistency


def test_preview_agrees_with_render_on_what_is_renderable():
    """The whole point of the shared counting path."""
    rows = [_signal(home_country="United Kingdom")] * 8 + [_signal(home_country="Ireland")] * 4
    pkg, _ = _pkg(rows)

    preview = pkg.preview(
        section_keys=["sig_territory_home"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    metrics = pkg.compose(
        section_keys=["sig_territory_home"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        title="X",
    )

    entry = preview["sections"][0]
    rendered = _section(metrics, "Production Volume by Home Country")
    assert entry["renderable"] is True
    assert entry["qualifying_segments"] == len(rendered["rows"]) == 1
    assert entry["suppressed_segments"] == 1


def test_section_with_no_declared_data_is_not_renderable():
    """A Crew Size Distribution built from signals that declared no crew size
    must not clear the threshold on an 'Unspecified' bucket."""
    pkg, _ = _pkg([_signal()] * 12)  # no crew_size field at all

    preview = pkg.preview(
        section_keys=["sig_crew"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    metrics = pkg.compose(
        section_keys=["sig_crew"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        title="X",
    )

    assert preview["sections"][0]["renderable"] is False
    assert _section(metrics, "Crew Size Distribution")["rows"] == []


def test_preview_flags_sections_blocked_by_exclusivity():
    pkg, _ = _pkg([_signal()] * 12)

    preview = pkg.preview(
        section_keys=["sig_territory_home", "sig_audience"],
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        blocked_keys={
            "sig_audience": {"module_label": "AI Usage Module", "reverts_at": "2028-06-30"}
        },
    )

    blocked = next(s for s in preview["sections"] if s["key"] == "sig_audience")
    assert blocked["status"] == "blocked_exclusive"
    assert blocked["renderable"] is False
    assert blocked["exclusivity"]["module_label"] == "AI Usage Module"
    assert preview["renderable_sections"] == 1


# ------------------------------------------------------- dataset plumbing


def test_festivals_dataset_points_at_the_real_table():
    """Regression: this mapped to a non-existent "festivals" table, and because
    count()/fetch() swallow exceptions the section silently reported empty."""
    assert DatasetFetcher._TABLES["festivals"] == "film_festivals"


def test_display_columns_intersect_with_present_columns():
    fetcher = DatasetFetcher(MagicMock())
    records = [{"territory": "UK", "program": "AVEC", "id": "1"}]

    assert fetcher.display_columns("incentives", records) == ["territory", "program"]


def test_display_columns_fall_back_when_schema_is_unrecognised():
    fetcher = DatasetFetcher(MagicMock())
    records = [{"id": "1", "created_at": "x", "alpha": 1, "beta": 2}]

    assert fetcher.display_columns("incentives", records) == ["alpha", "beta"]


def test_display_columns_empty_for_no_records():
    assert DatasetFetcher(MagicMock()).display_columns("incentives", []) == []


def test_cell_formats_lists_and_nulls_for_the_pdf():
    assert PackageService._cell(None) == "-"
    assert PackageService._cell(["Drama", "Thriller"]) == "Drama, Thriller"
    assert PackageService._cell([]) == "-"
    assert PackageService._cell(2026) == "2026"


# --------------------------------------------------------- generate path


def test_generate_bespoke_report_completes_and_stores_pdf():
    b2b = B2BService(MagicMock(), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))
    b2b.email_service = MagicMock()
    b2b.pdf_service = MagicMock()
    b2b.pdf_service.generate_pdf_bytes.return_value = b"%PDF-1.4 fake"
    b2b.render_pdf_html = MagicMock(return_value="<html></html>")
    b2b.db.table.return_value.insert.return_value.execute.return_value.data = None
    b2b.deliver_request_pdf = MagicMock()

    row = b2b.generate_bespoke_report(
        metrics={"insufficient_data": False, "title": "Bespoke"},
        user_id="user-1",
        recipient_email="admin@prodculator.com",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        subscription_id="sub_grey",
    )

    assert row["status"] == "completed"
    assert row["product_type"] == "enterprise"
    assert row["request_type"] == "admin"
    b2b.pdf_service.generate_pdf_bytes.assert_called_once()
    # Delivery is opt-in: composing must not email the client on every generate.
    b2b.deliver_request_pdf.assert_not_called()


def test_generate_bespoke_report_delivers_only_when_asked():
    b2b = B2BService(MagicMock(), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))
    b2b.email_service = MagicMock()
    b2b.pdf_service = MagicMock()
    b2b.pdf_service.generate_pdf_bytes.return_value = b"%PDF"
    b2b.render_pdf_html = MagicMock(return_value="<html></html>")
    b2b.db.table.return_value.insert.return_value.execute.return_value.data = None
    b2b.deliver_request_pdf = MagicMock()

    b2b.generate_bespoke_report(
        metrics={"insufficient_data": False, "title": "Bespoke"},
        user_id="user-1",
        recipient_email="admin@prodculator.com",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        deliver=True,
    )

    b2b.deliver_request_pdf.assert_called_once()


def test_generate_bespoke_report_refuses_insufficient_metrics():
    b2b = B2BService(MagicMock(), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))
    b2b.pdf_service = MagicMock()

    try:
        b2b.generate_bespoke_report(
            metrics={"insufficient_data": True},
            user_id="user-1",
            recipient_email="admin@prodculator.com",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 3, 31),
        )
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "privacy floor" in str(exc)

    # Nothing was persisted or rendered.
    b2b.pdf_service.generate_pdf_bytes.assert_not_called()


def test_generate_bespoke_report_marks_failed_and_reraises():
    b2b = B2BService(MagicMock(), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))
    b2b.email_service = MagicMock()
    b2b.pdf_service = MagicMock()
    b2b.pdf_service.generate_pdf_bytes.return_value = None  # PDF engine unavailable
    b2b.render_pdf_html = MagicMock(return_value="<html></html>")
    b2b.db.table.return_value.insert.return_value.execute.return_value.data = None

    try:
        b2b.generate_bespoke_report(
            metrics={"insufficient_data": False, "title": "Bespoke"},
            user_id="user-1",
            recipient_email="admin@prodculator.com",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 3, 31),
        )
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass

    statuses = [
        c.args[0].get("status")
        for c in b2b.db.table.return_value.update.call_args_list
    ]
    assert "failed" in statuses
