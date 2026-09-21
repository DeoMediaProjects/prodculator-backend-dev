"""The v2 orchestration payload, wired into the builder behind a flag.

The flag exists for one reason: the old-versus-v2 comparison has to run against
real report runs before any cutover, and that is the one part of this sequence
fixtures cannot rehearse. So what these tests guard is that turning it on
changes exactly one thing — a shadow payload appears — and that turning it off,
or having it fail, changes nothing a producer sees.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.modules.reports.engine_envelope import assert_consistent
from app.modules.reports.orchestration import as_payload, assemble
from app.modules.reports.project_facts_v1 import build_project_facts_snapshot
from app.modules.reports.project_dna import build_project_dna


def _snapshot(**metadata):
    base = {"script_title": "A Film", "budget_amount": 1_000_000}
    base.update(metadata)
    return build_project_facts_snapshot(base, build_project_dna(base, {}))


def test_the_flag_is_off_by_default():
    """A half-wired orchestration reaching a paid report is the failure mode."""
    assert get_settings().REPORT_ORCHESTRATION_V2_ENABLED is False


def test_the_flag_is_readable_from_settings():
    assert isinstance(get_settings().REPORT_ORCHESTRATION_V2_ENABLED, bool)


def test_the_builder_attaches_nothing_while_the_flag_is_off(monkeypatch):
    from app.modules.reports.builder import ReportBuilder

    builder = ReportBuilder.__new__(ReportBuilder)
    report: dict = {}
    builder._attach_orchestration_v2(report)
    assert report == {}


def test_the_attached_payload_carries_this_run_s_snapshot():
    """Whatever the builder attaches must pass the report's own provenance check."""
    snapshot = _snapshot()
    payload = as_payload(
        assemble(
            report_run_id="run-1",
            projectfacts_snapshot_id=snapshot.snapshot_id,
            projectfacts_version=snapshot.version,
            engine_results=[],
            package="producer",
        )
    )
    report = {
        "projectFactsSnapshotId": snapshot.snapshot_id,
        "projectFactsVersion": snapshot.version,
        "orchestrationV2": payload,
    }
    assert_consistent(report)


def test_an_orchestration_payload_from_another_run_is_caught():
    """The shadow payload is checked like any other engine result."""
    import pytest

    from app.modules.reports.engine_envelope import InconsistentInputVersion

    payload = as_payload(
        assemble(
            report_run_id="run-1",
            projectfacts_snapshot_id="somewhere-else",
            projectfacts_version="1",
            engine_results=[],
            package="producer",
        )
    )
    report = {
        "projectFactsSnapshotId": "this-run",
        "projectFactsVersion": "1",
        "orchestrationV2": payload,
    }
    with pytest.raises(InconsistentInputVersion):
        assert_consistent(report)


def test_the_payload_has_thirteen_sections_even_with_no_engines():
    """A run where nothing matched still has a shape; it just has no contents."""
    snapshot = _snapshot()
    result = assemble(
        report_run_id="run-1",
        projectfacts_snapshot_id=snapshot.snapshot_id,
        projectfacts_version=snapshot.version,
        engine_results=[],
        package="free",
    )
    assert len(result.sections) == 13
    assert all(section["blocks"] == [] for section in result.sections)
    assert result.qa["status"] == "PASS"


def test_an_assembly_failure_does_not_break_the_report(monkeypatch):
    """A producer's report must not fail for a shadow payload nobody reads.

    The failure still has to be visible, or the comparison would silently be
    comparing against nothing.
    """
    from app.modules.reports import builder as builder_module
    from app.modules.reports.builder import ReportBuilder

    class _Boom:
        REPORT_ORCHESTRATION_V2_ENABLED = True

    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: _Boom(), raising=True
    )
    monkeypatch.setattr(
        builder_module,
        "GRANTS_ENGINE_VERSION",
        "2.0",
        raising=False,
    )

    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = []
    instance.grants_payload = None
    instance.request_metadata = {}
    instance.project_facts_snapshot = _snapshot()

    def _explode(*args, **kwargs):
        raise RuntimeError("assembly failed")

    monkeypatch.setattr(
        "app.modules.reports.orchestration.assemble", _explode, raising=True
    )

    report: dict = {}
    instance._attach_orchestration_v2(report)

    assert "orchestrationV2" not in report
    assert any("orchestration-v2" in warning for warning in instance.warnings)


def test_the_flag_on_attaches_a_payload(monkeypatch):
    from app.modules.reports.builder import ReportBuilder

    class _On:
        REPORT_ORCHESTRATION_V2_ENABLED = True

    monkeypatch.setattr("app.core.config.get_settings", lambda: _On(), raising=True)

    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = []
    instance.grants_payload = None
    instance.request_metadata = {"report_id": "r-1", "_package": "producer"}
    instance.project_facts_snapshot = _snapshot()

    report: dict = {}
    instance._attach_orchestration_v2(report)

    payload = report["orchestrationV2"]
    assert len(payload["sections"]) == 13
    assert payload["projectfacts_snapshot_id"] == instance.project_facts_snapshot.snapshot_id
    assert payload["report_run_id"] == "r-1"


# ── Telling "flag off" from "flag on and nothing came back" ──────────────────


def test_the_flag_off_leaves_no_trace(monkeypatch):
    from app.modules.reports.builder import ReportBuilder

    class _Off:
        REPORT_ORCHESTRATION_V2_ENABLED = False

    monkeypatch.setattr("app.core.config.get_settings", lambda: _Off(), raising=True)
    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = []

    report: dict = {}
    instance._attach_orchestration_v2(report)
    assert report == {}


def test_the_flag_on_marks_the_report_even_when_it_worked(monkeypatch):
    """The key's presence is what says the flag was on.

    Without it a report generated with the flag on and every engine failing is
    indistinguishable from one generated with the flag off — no payload, no
    trace — and the only way to answer "did the variable take?" is to read the
    deploy logs.
    """
    from app.modules.reports.builder import ReportBuilder

    class _On:
        REPORT_ORCHESTRATION_V2_ENABLED = True

    monkeypatch.setattr("app.core.config.get_settings", lambda: _On(), raising=True)
    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = []
    instance.grants_payload = None
    instance.request_metadata = {"report_id": "r-1", "_package": "producer"}
    instance.project_facts_snapshot = _snapshot()

    report: dict = {}
    instance._attach_orchestration_v2(report)
    # The key exists whatever happened. Its contents depend on what the engines
    # could reach — with no staged database they report that, which is the
    # information the key is for.
    assert "orchestrationV2Warnings" in report
    assert "orchestrationV2" in report
    # Every warning says which step produced it. Not all of them say
    # "[orchestration-v2]": the comparables and sales strategies are built by a
    # helper section 12 also calls, so a catalogue it cannot read is reported
    # as "[commercial]" wherever it is noticed first. Attributability is the
    # invariant — a bare string in this list would leave a reader unable to
    # tell which engine failed.
    assert all(
        warning.startswith(("[orchestration-v2]", "[commercial]"))
        for warning in report["orchestrationV2Warnings"]
    )


def test_a_failure_reaches_the_report_rather_than_a_list_nobody_reads(monkeypatch):
    from app.modules.reports import builder as builder_module
    from app.modules.reports.builder import ReportBuilder

    class _Boom:
        REPORT_ORCHESTRATION_V2_ENABLED = True

    monkeypatch.setattr("app.core.config.get_settings", lambda: _Boom(), raising=True)
    monkeypatch.setattr(
        builder_module, "GRANTS_ENGINE_VERSION", "2.0", raising=False
    )

    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = []
    instance.grants_payload = None
    instance.request_metadata = {}
    instance.project_facts_snapshot = _snapshot()

    def _explode(*args, **kwargs):
        raise RuntimeError("assembly failed")

    monkeypatch.setattr(
        "app.modules.reports.orchestration.assemble", _explode, raising=True
    )

    report: dict = {}
    instance._attach_orchestration_v2(report)

    assert "orchestrationV2" not in report
    recorded = report["orchestrationV2Warnings"]
    assert any("not assembled" in warning for warning in recorded)


def test_only_this_run_s_warnings_are_recorded(monkeypatch):
    """A warning from earlier in the build is not a v2 failure."""
    from app.modules.reports.builder import ReportBuilder

    class _On:
        REPORT_ORCHESTRATION_V2_ENABLED = True

    monkeypatch.setattr("app.core.config.get_settings", lambda: _On(), raising=True)
    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = ["[something-else] unrelated"]
    instance.grants_payload = None
    instance.request_metadata = {"report_id": "r-1", "_package": "producer"}
    instance.project_facts_snapshot = _snapshot()

    report: dict = {}
    instance._attach_orchestration_v2(report)
    assert "[something-else] unrelated" not in report["orchestrationV2Warnings"]
    assert instance.warnings[0] == "[something-else] unrelated"
