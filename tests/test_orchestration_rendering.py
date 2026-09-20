"""The v2 payload on its way out: API filtering, and its own renderer.

Two separate concerns. The API must not let the canonical payload carry figures
past a filter that was written to remove them from the legacy shape. The
renderer must show what the payload actually contains, including the parts that
are absent, because a document that hid them would make an incomplete run look
finished.
"""
from __future__ import annotations

import pytest

from app.modules.reports.pdf_service import PDFService
from app.modules.reports.router import _build_free_tier_report_data
from app.modules.reports.sample_orchestration import (
    as_payload,
    build_sample_orchestration,
)


@pytest.fixture(scope="module")
def payload():
    return as_payload(build_sample_orchestration())


@pytest.fixture(scope="module")
def html(payload):
    return PDFService().render_orchestration_html(payload, script_title="A Film")


# ── The free tier never receives the v2 payload ──────────────────────────────


def test_the_free_tier_strips_the_orchestration_payload():
    """This filter removes rather than allowlists, so anything new survives it.

    The v2 payload carries the same figures the rest of that function spends its
    body stripping out of the legacy shape, and a second copy of a redacted
    number is not less sensitive for being nested.
    """
    data = {
        "locationRankings": [],
        "executiveSummary": {"headlineNetBudget": "£24,262,500"},
        "orchestrationV2": {"sections": [{"blocks": [{"data": {"x": 1}}]}]},
    }
    assert "orchestrationV2" not in _build_free_tier_report_data(data)


def test_stripping_is_safe_when_there_is_no_payload():
    data = {"locationRankings": [], "executiveSummary": {}}
    assert "orchestrationV2" not in _build_free_tier_report_data(data)


def test_the_original_report_data_is_not_mutated():
    data = {
        "locationRankings": [],
        "executiveSummary": {},
        "orchestrationV2": {"sections": []},
    }
    _build_free_tier_report_data(data)
    assert "orchestrationV2" in data


def test_no_figure_from_the_payload_survives_into_a_free_response():
    """Checked on the serialised result, not just the top-level key."""
    import json

    data = {
        "locationRankings": [],
        "executiveSummary": {},
        "orchestrationV2": {
            "sections": [
                {"blocks": [{"data": {"recommendations": [{"amount": "£5,740,000"}]}}]}
            ]
        },
    }
    assert "5,740,000" not in json.dumps(_build_free_tier_report_data(data))


# ── The renderer shows the payload, absences included ────────────────────────


def test_every_section_reaches_the_document(html):
    for title in (
        "Executive Summary",
        "Script Intelligence",
        "Tax Incentive Analysis",
        "Grant &amp; Funding Opportunities",
        "Industry Development &amp; Market Strategy",
        "Comparable Productions",
        "Festival Strategy",
        "Sales &amp; Distribution Strategy",
        "Next Steps",
    ):
        assert title in html, title


def test_an_absent_figure_is_printed_as_absent(html):
    """A programme with no calculable amount says so rather than showing blank."""
    assert "not calculated" in html


def test_an_empty_section_says_it_is_empty(html):
    assert "No engine output for this section in this run." in html


def test_the_downstream_sections_declare_they_own_no_calculation(html):
    assert "owns no calculation" in html


def test_both_counts_are_rendered_so_shown_is_not_read_as_searched(html):
    assert "shown of" in html
    assert "eligible" in html


def test_the_routing_conflict_and_its_rule_are_shown(html):
    assert "Film London Production Finance Market" in html
    assert "Routing wins over presence" in html


def test_an_empty_finance_bucket_is_labelled_rather_than_omitted(html):
    """Committed finance being empty is the fixture's correct state."""
    assert "documented committed finance" in html
    assert "empty" in html


def test_the_provenance_travels_into_the_document(html, payload):
    assert payload["projectfacts_snapshot_id"] in html
    assert payload["report_run_id"] in html


def test_the_document_says_it_is_not_the_paid_report(html):
    assert "not the paid report" in html


def test_no_acquisition_language_reaches_the_document(html):
    lowered = html.lower()
    for phrase in ("will buy", "likely to acquire", "interested buyer", "actively scouting"):
        assert phrase not in lowered, phrase


# ── Malformed input ──────────────────────────────────────────────────────────


def test_a_non_payload_is_refused_rather_than_rendered_empty():
    """An empty document would look like a payload with nothing in it."""
    service = PDFService()
    for bad in (None, {}, {"sections_but_misspelled": []}, "not a payload"):
        with pytest.raises(ValueError, match="Not a v2 orchestration payload"):
            service.render_orchestration_html(bad)


def test_a_payload_with_no_engine_output_still_renders_thirteen_sections():
    from app.modules.reports.orchestration import assemble

    empty = as_payload(
        assemble(
            report_run_id="run-1",
            projectfacts_snapshot_id="snap-a",
            projectfacts_version="1",
            engine_results=[],
            package="free",
        )
    )
    rendered = PDFService().render_orchestration_html(empty, script_title="Nothing")
    assert rendered.count("No engine output for this section in this run.") == 13


def test_the_legacy_report_template_is_untouched_by_any_of_this():
    """The paid report must not be one edit away from a v2 regression."""
    from pathlib import Path

    base = (
        Path(__file__).resolve().parents[1]
        / "app/templates/pdf/report_base.html"
    ).read_text(encoding="utf-8")
    assert "orchestrationV2" not in base
    assert "payload.sections" not in base
