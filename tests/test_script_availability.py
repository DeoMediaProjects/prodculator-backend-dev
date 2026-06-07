"""Tests for the Claude pre-flight probe and outage-vs-content failure handling."""

import pytest

from app.core.config import Settings
from app.modules.scripts.service import ClaudeUnavailableError, ScriptAnalysisService


def _settings(api_key: str = "sk-test-dummy") -> Settings:
    return Settings(
        _env_file=None,
        JWT_SECRET_KEY="x" * 64,
        ANTHROPIC_API_KEY=api_key,
        ANTHROPIC_MODEL="claude-test",
        ANTHROPIC_HEALTHCHECK_TIMEOUT=5,
        SCRIPT_ANALYSIS_CHUNKED_ENABLED=True,
    )


class _FakeMessages:
    def __init__(self, raises=None):
        self._raises = raises

    def create(self, **_kwargs):
        if self._raises:
            raise self._raises
        return object()  # a non-None response is enough for the probe


class _FakeClient:
    def __init__(self, raises=None):
        self.messages = _FakeMessages(raises)


# ---------------------------------------------------------------------------
# check_available()
# ---------------------------------------------------------------------------

class TestCheckAvailable:
    def test_passes_when_api_reachable(self, monkeypatch):
        svc = ScriptAnalysisService(_settings())
        monkeypatch.setattr(svc, "_build_client", lambda _t: _FakeClient())
        # Should not raise.
        assert svc.check_available() is None

    def test_raises_when_no_api_key(self):
        svc = ScriptAnalysisService(_settings(api_key=""))
        with pytest.raises(ClaudeUnavailableError):
            svc.check_available()

    def test_raises_on_connection_error(self, monkeypatch):
        svc = ScriptAnalysisService(_settings())
        monkeypatch.setattr(
            svc, "_build_client", lambda _t: _FakeClient(raises=Exception("Connection error"))
        )
        with pytest.raises(ClaudeUnavailableError):
            svc.check_available()

    def test_raises_on_overloaded(self, monkeypatch):
        svc = ScriptAnalysisService(_settings())
        monkeypatch.setattr(
            svc, "_build_client", lambda _t: _FakeClient(raises=Exception("overloaded_error 529"))
        )
        with pytest.raises(ClaudeUnavailableError):
            svc.check_available()


# ---------------------------------------------------------------------------
# Outage vs content failure during analysis
# ---------------------------------------------------------------------------

_SCRIPT = "INT. HOUSE - DAY\nA talks.\n\nEXT. STREET - NIGHT\nB walks.\n\nINT. CAR - DAY\nC drives."


class TestOutageVsContentFailure:
    def test_total_outage_raises_unavailable_not_fallback(self, monkeypatch):
        """Every chunk fails with an availability error -> ClaudeUnavailableError,
        so the caller fails the report instead of returning a heuristic fallback."""
        svc = ScriptAnalysisService(_settings())

        def all_timeout(*_a, **_kw):
            raise Exception("Request timed out")

        monkeypatch.setattr(svc, "_extract_chunk_analysis", all_timeout)

        with pytest.raises(ClaudeUnavailableError):
            svc.analyze_with_meta(_SCRIPT, "My Script")

    def test_content_failure_falls_back(self, monkeypatch):
        """Chunks fail for a non-availability reason (parse) -> heuristic fallback
        is returned (a real, if degraded, report), not a hard failure."""
        svc = ScriptAnalysisService(_settings())

        def all_parse_error(*_a, **_kw):
            raise ValueError("could not parse JSON payload")

        monkeypatch.setattr(svc, "_extract_chunk_analysis", all_parse_error)

        result, meta = svc.analyze_with_meta(_SCRIPT, "My Script")
        assert result is not None
        assert meta.get("fallbackUsed") is True
