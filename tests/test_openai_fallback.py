"""Tests for the OpenAI fallback used when Anthropic is unreachable or out of
credits. The safety property: the fallback fires ONLY for provider-unavailable
failures (rate-limit, timeout, connection, quota/auth) using the IDENTICAL
prompt/temperature/stage as the Anthropic call — never for a genuine content
or parsing bug, and never when OpenAI isn't configured.
"""
import pytest

from app.core.config import Settings
from app.modules.scripts.service import ScriptAnalysisService, _OpenAIResponseShim


def _settings(*, openai_key: str = "sk-openai-test") -> Settings:
    return Settings(
        _env_file=None,
        JWT_SECRET_KEY="x" * 64,
        ANTHROPIC_API_KEY="sk-test-dummy",
        ANTHROPIC_MODEL="claude-test",
        OPENAI_API_KEY=openai_key,
        OPENAI_MODEL="gpt-4o",
    )


class TestQuotaAuthClassifier:
    def test_detects_out_of_credits(self):
        svc = ScriptAnalysisService(_settings())
        assert svc._is_quota_or_auth_error(
            Exception("Your credit balance is too low to access the Anthropic API")
        )

    def test_detects_insufficient_quota(self):
        svc = ScriptAnalysisService(_settings())
        assert svc._is_quota_or_auth_error(Exception("Error code: 429 - insufficient_quota"))

    def test_ignores_content_errors(self):
        svc = ScriptAnalysisService(_settings())
        assert not svc._is_quota_or_auth_error(Exception("could not parse JSON payload"))

    def test_provider_unavailable_covers_quota(self):
        svc = ScriptAnalysisService(_settings())
        assert svc._is_provider_unavailable_error(Exception("credit balance is too low"))


class TestLLMFallbackOrchestration:
    def test_falls_back_to_openai_on_anthropic_outage(self, monkeypatch):
        svc = ScriptAnalysisService(_settings())

        def fake_anthropic(self, **kwargs):
            raise Exception("Your credit balance is too low to access the Anthropic API")

        def fake_openai(self, **kwargs):
            return _OpenAIResponseShim(text='{"ok":true}', stop_reason="end_turn", input_tokens=5, output_tokens=5)

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_openai_with_retry", fake_openai)

        response = svc._call_llm_with_retry(
            system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk"
        )

        assert svc._extract_text_response(response) == '{"ok":true}'
        assert svc._last_llm_provider == "openai"

    def test_non_availability_error_never_triggers_fallback(self, monkeypatch):
        """A real content/logic bug must propagate untouched — never masked by
        a fallback attempt that could hide the actual failure."""
        svc = ScriptAnalysisService(_settings())

        def fake_anthropic(self, **kwargs):
            raise ValueError("some unrelated bug")

        def explode(self, **kwargs):
            raise AssertionError("OpenAI must not be called for a non-availability error")

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_openai_with_retry", explode)

        with pytest.raises(ValueError, match="some unrelated bug"):
            svc._call_llm_with_retry(system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk")

    def test_both_providers_down_surfaces_original_anthropic_error(self, monkeypatch):
        svc = ScriptAnalysisService(_settings())

        def fake_anthropic(self, **kwargs):
            raise Exception("credit balance is too low")

        def fake_openai_down(self, **kwargs):
            raise Exception("OpenAI is also down")

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_openai_with_retry", fake_openai_down)

        with pytest.raises(Exception, match="credit balance is too low"):
            svc._call_llm_with_retry(system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk")

    def test_no_fallback_attempted_when_openai_not_configured(self, monkeypatch):
        svc = ScriptAnalysisService(_settings(openai_key=""))

        def fake_anthropic(self, **kwargs):
            raise Exception("credit balance is too low")

        def explode(self, **kwargs):
            raise AssertionError("OpenAI must not be called when not configured")

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_openai_with_retry", explode)

        with pytest.raises(Exception, match="credit balance is too low"):
            svc._call_llm_with_retry(system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk")


class TestResponseShimCompatibility:
    """The OpenAI response must be indistinguishable, to downstream consumers,
    from an Anthropic Message — same .content/.stop_reason/.usage surface."""

    def test_extract_text_response_reads_shim(self):
        svc = ScriptAnalysisService(_settings())
        shim = _OpenAIResponseShim(text="hello world", stop_reason="end_turn", input_tokens=1, output_tokens=2)
        assert svc._extract_text_response(shim) == "hello world"

    def test_max_tokens_stop_reason_maps_to_truncated(self):
        shim = _OpenAIResponseShim(text="partial", stop_reason="max_tokens", input_tokens=1, output_tokens=2)
        assert shim.stop_reason == "max_tokens"


class TestCheckAvailableWithOpenAIFallback:
    def test_openai_reachable_when_anthropic_key_missing(self, monkeypatch):
        """No Anthropic key, but OpenAI is configured and reachable -> available."""
        svc = ScriptAnalysisService(_settings())
        monkeypatch.setattr(svc.settings, "ANTHROPIC_API_KEY", "")

        class _FakeOpenAIClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**_kwargs):
                        return object()

        monkeypatch.setattr(svc, "_build_openai_client", lambda _t: _FakeOpenAIClient())

        assert svc.check_available() is None

    def test_raises_when_both_unavailable(self, monkeypatch):
        svc = ScriptAnalysisService(_settings())
        monkeypatch.setattr(svc.settings, "ANTHROPIC_API_KEY", "")

        class _FakeOpenAIClientDown:
            class chat:
                class completions:
                    @staticmethod
                    def create(**_kwargs):
                        raise Exception("OpenAI down too")

        monkeypatch.setattr(svc, "_build_openai_client", lambda _t: _FakeOpenAIClientDown())

        from app.modules.scripts.service import ClaudeUnavailableError
        with pytest.raises(ClaudeUnavailableError):
            svc.check_available()
