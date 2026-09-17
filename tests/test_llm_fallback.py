"""Tests for the LLM fallback chain used when Anthropic is unreachable or out of
credits: Anthropic -> OpenAI -> Gemini, in whatever order LLM_FALLBACK_PROVIDERS
gives. The safety property: a fallback fires ONLY for provider-unavailable
failures (rate-limit, timeout, connection, quota/auth) using the IDENTICAL
prompt/temperature/stage as the Anthropic call — never for a genuine content or
parsing bug, and never for a provider that isn't configured.
"""
import pytest

from app.core.config import Settings
from app.modules.scripts.service import (
    ScriptAnalysisService,
    _OpenAIResponseShim,
    _ProviderResponseShim,
)


def _settings(
    *,
    openai_key: str = "sk-openai-test",
    gemini_key: str = "",
    fallbacks: str = "openai,gemini",
) -> Settings:
    return Settings(
        _env_file=None,
        JWT_SECRET_KEY="x" * 64,
        ANTHROPIC_API_KEY="sk-test-dummy",
        ANTHROPIC_MODEL="claude-test",
        OPENAI_API_KEY=openai_key,
        OPENAI_MODEL="gpt-4o",
        GEMINI_API_KEY=gemini_key,
        GEMINI_MODEL="gemini-test",
        LLM_FALLBACK_PROVIDERS=fallbacks,
    )


def _shim(text: str = '{"ok":true}') -> _ProviderResponseShim:
    return _ProviderResponseShim(text=text, stop_reason="end_turn", input_tokens=5, output_tokens=5)


class TestQuotaAuthClassifier:
    def test_detects_out_of_credits(self):
        svc = ScriptAnalysisService(_settings())
        assert svc._is_quota_or_auth_error(
            Exception("Your credit balance is too low to access the Anthropic API")
        )

    def test_detects_insufficient_quota(self):
        svc = ScriptAnalysisService(_settings())
        assert svc._is_quota_or_auth_error(Exception("Error code: 429 - insufficient_quota"))

    def test_detects_anthropic_spend_cap(self):
        """The org spend cap comes back as a 400 invalid_request_error, not a
        429 — the classifier must still call it a provider outage or the chain
        refuses to fall back for the exact case it exists for."""
        svc = ScriptAnalysisService(_settings())
        assert svc._is_provider_unavailable_error(
            Exception(
                "Error code: 400 - {'type': 'error', 'error': {'type': "
                "'invalid_request_error', 'message': 'You have reached your "
                "specified API usage limits. You will regain access on "
                "2026-10-01 at 00:00 UTC.'}}"
            )
        )

    def test_detects_gemini_bad_key(self):
        svc = ScriptAnalysisService(_settings())
        assert svc._is_quota_or_auth_error(
            Exception("400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key.")
        )

    def test_detects_gemini_resource_exhausted(self):
        """Gemini's wording for a rate limit must reach the same retry path as
        Anthropic's, or a quota blip becomes a hard report failure."""
        svc = ScriptAnalysisService(_settings())
        assert svc._is_rate_limit_error(Exception("429 RESOURCE_EXHAUSTED. Quota exceeded."))

    def test_ignores_content_errors(self):
        svc = ScriptAnalysisService(_settings())
        assert not svc._is_quota_or_auth_error(Exception("could not parse JSON payload"))

    def test_provider_unavailable_covers_quota(self):
        svc = ScriptAnalysisService(_settings())
        assert svc._is_provider_unavailable_error(Exception("credit balance is too low"))


class TestFallbackChainResolution:
    def test_skips_providers_without_a_key(self):
        svc = ScriptAnalysisService(_settings(gemini_key=""))
        assert svc._fallback_chain() == ["openai"]

    def test_honours_configured_order(self, monkeypatch):
        monkeypatch.setattr(
            "app.modules.scripts.service._gemini_sdk_available", lambda: True
        )
        svc = ScriptAnalysisService(
            _settings(gemini_key="g-key", fallbacks="gemini,openai")
        )
        assert svc._fallback_chain() == ["gemini", "openai"]

    def test_narrowing_the_list_excludes_a_configured_provider(self, monkeypatch):
        """The whole point of LLM_FALLBACK_PROVIDERS: a key that is present but
        known-dead must be skippable without deleting it from the env."""
        monkeypatch.setattr(
            "app.modules.scripts.service._gemini_sdk_available", lambda: True
        )
        svc = ScriptAnalysisService(_settings(gemini_key="g-key", fallbacks="gemini"))
        assert svc._fallback_chain() == ["gemini"]

    def test_unknown_names_are_dropped(self, monkeypatch):
        monkeypatch.setattr(
            "app.modules.scripts.service._gemini_sdk_available", lambda: True
        )
        svc = ScriptAnalysisService(
            _settings(gemini_key="g-key", fallbacks="gemini,llama,gemini")
        )
        assert svc._fallback_chain() == ["gemini"]

    def test_gemini_skipped_when_sdk_missing(self, monkeypatch):
        """A key set on an image built before google-genai was added must not
        wedge the chain — it should behave as if Gemini weren't configured."""
        monkeypatch.setattr(
            "app.modules.scripts.service._gemini_sdk_available", lambda: False
        )
        svc = ScriptAnalysisService(_settings(openai_key="", gemini_key="g-key"))
        assert svc._fallback_chain() == []


class TestLLMFallbackOrchestration:
    def test_falls_back_to_openai_on_anthropic_outage(self, monkeypatch):
        svc = ScriptAnalysisService(_settings())

        def fake_anthropic(self, **kwargs):
            raise Exception("Your credit balance is too low to access the Anthropic API")

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_openai_with_retry", lambda self, **k: _shim())

        response = svc._call_llm_with_retry(
            system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk"
        )

        assert svc._extract_text_response(response) == '{"ok":true}'
        assert svc._last_llm_provider == "openai"

    def test_falls_through_openai_to_gemini(self, monkeypatch):
        """The case this chain exists for: Anthropic out of credits AND the
        OpenAI key unusable. Gemini must still serve the stage."""
        monkeypatch.setattr("app.modules.scripts.service._gemini_sdk_available", lambda: True)
        svc = ScriptAnalysisService(_settings(gemini_key="g-key"))

        def fake_anthropic(self, **kwargs):
            raise Exception("credit balance is too low")

        def fake_openai(self, **kwargs):
            raise Exception("Error code: 429 - insufficient_quota")

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_openai_with_retry", fake_openai)
        monkeypatch.setattr(
            ScriptAnalysisService, "_call_gemini_with_retry", lambda self, **k: _shim('{"from":"gemini"}')
        )

        response = svc._call_llm_with_retry(
            system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk"
        )

        assert svc._extract_text_response(response) == '{"from":"gemini"}'
        assert svc._last_llm_provider == "gemini"

    def test_gemini_receives_the_identical_prompt(self, monkeypatch):
        """Not a degraded path: same system prompt, user content, temperature
        and stage as the Anthropic call that failed."""
        monkeypatch.setattr("app.modules.scripts.service._gemini_sdk_available", lambda: True)
        svc = ScriptAnalysisService(_settings(openai_key="", gemini_key="g-key"))
        seen = {}

        def fake_anthropic(self, **kwargs):
            raise Exception("credit balance is too low")

        def fake_gemini(self, **kwargs):
            seen.update(kwargs)
            return _shim()

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_gemini_with_retry", fake_gemini)

        svc._call_llm_with_retry(
            system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk"
        )

        assert seen == {
            "system_prompt": "sys",
            "user_content": "usr",
            "temperature": 0.1,
            "stage": "script_chunk",
        }

    def test_non_availability_error_never_triggers_fallback(self, monkeypatch):
        """A real content/logic bug must propagate untouched — never masked by
        a fallback attempt that could hide the actual failure."""
        monkeypatch.setattr("app.modules.scripts.service._gemini_sdk_available", lambda: True)
        svc = ScriptAnalysisService(_settings(gemini_key="g-key"))

        def fake_anthropic(self, **kwargs):
            raise ValueError("some unrelated bug")

        def explode(self, **kwargs):
            raise AssertionError("no fallback may be called for a non-availability error")

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_openai_with_retry", explode)
        monkeypatch.setattr(ScriptAnalysisService, "_call_gemini_with_retry", explode)

        with pytest.raises(ValueError, match="some unrelated bug"):
            svc._call_llm_with_retry(system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk")

    def test_content_error_on_a_fallback_stops_the_chain(self, monkeypatch):
        """If OpenAI fails on something that isn't an availability problem, the
        same input would fail identically on Gemini — trying it would only burn
        another provider's retry schedule and bury the real cause."""
        monkeypatch.setattr("app.modules.scripts.service._gemini_sdk_available", lambda: True)
        svc = ScriptAnalysisService(_settings(gemini_key="g-key"))

        def fake_anthropic(self, **kwargs):
            raise Exception("credit balance is too low")

        def fake_openai(self, **kwargs):
            raise ValueError("prompt is malformed")

        def explode(self, **kwargs):
            raise AssertionError("Gemini must not be tried after a content error")

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_openai_with_retry", fake_openai)
        monkeypatch.setattr(ScriptAnalysisService, "_call_gemini_with_retry", explode)

        with pytest.raises(Exception, match="credit balance is too low"):
            svc._call_llm_with_retry(system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk")

    def test_all_providers_down_surfaces_original_anthropic_error(self, monkeypatch):
        monkeypatch.setattr("app.modules.scripts.service._gemini_sdk_available", lambda: True)
        svc = ScriptAnalysisService(_settings(gemini_key="g-key"))

        def fake_anthropic(self, **kwargs):
            raise Exception("credit balance is too low")

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(
            ScriptAnalysisService,
            "_call_openai_with_retry",
            lambda self, **k: (_ for _ in ()).throw(Exception("OpenAI is also down: 429 insufficient_quota")),
        )
        monkeypatch.setattr(
            ScriptAnalysisService,
            "_call_gemini_with_retry",
            lambda self, **k: (_ for _ in ()).throw(Exception("429 RESOURCE_EXHAUSTED")),
        )

        with pytest.raises(Exception, match="credit balance is too low"):
            svc._call_llm_with_retry(system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk")

    def test_no_fallback_attempted_when_none_configured(self, monkeypatch):
        svc = ScriptAnalysisService(_settings(openai_key="", gemini_key=""))

        def fake_anthropic(self, **kwargs):
            raise Exception("credit balance is too low")

        def explode(self, **kwargs):
            raise AssertionError("no fallback is configured, so none may be called")

        monkeypatch.setattr(ScriptAnalysisService, "_call_anthropic_with_retry", fake_anthropic)
        monkeypatch.setattr(ScriptAnalysisService, "_call_openai_with_retry", explode)
        monkeypatch.setattr(ScriptAnalysisService, "_call_gemini_with_retry", explode)

        with pytest.raises(Exception, match="credit balance is too low"):
            svc._call_llm_with_retry(system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk")


class TestGeminiTokenBudget:
    def test_budget_gets_headroom_for_thought_tokens(self):
        """Gemini bills thinking against max_output_tokens, so handing it the raw
        answer-sized budget can return a truncated body."""
        svc = ScriptAnalysisService(_settings(gemini_key="g-key"))
        base = svc._stage_max_tokens("script_chunk")
        assert svc._gemini_max_tokens("script_chunk") == base * 2

    def test_multiplier_never_shrinks_the_budget(self, monkeypatch):
        svc = ScriptAnalysisService(_settings(gemini_key="g-key"))
        monkeypatch.setattr(svc.settings, "GEMINI_MAX_TOKENS_MULTIPLIER", 0.5)
        base = svc._stage_max_tokens("script_chunk")
        assert svc._gemini_max_tokens("script_chunk") == base


class _FakePart:
    def __init__(self, text, thought=False):
        self.text = text
        self.thought = thought


class _FakeCandidate:
    def __init__(self, parts, finish_reason=None):
        self.content = type("_C", (), {"parts": parts})()
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, parts, finish_reason=None, usage=None):
        self.candidates = [_FakeCandidate(parts, finish_reason)]
        self.usage_metadata = usage


class _FakeUsage:
    prompt_token_count = 120
    candidates_token_count = 40
    thoughts_token_count = 300


class _FakeGeminiClient:
    """Stands in for google-genai's client, recording what the call asked for."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.calls: list[dict] = []
        self.models = self

    def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._chunks)


class TestGeminiStreamParsing:
    """Covers the real _call_gemini_with_retry body — the response shape is the
    part most likely to drift with the SDK, and a silent mis-parse here would
    ship an empty report rather than an error."""

    def _service(self, monkeypatch, chunks):
        monkeypatch.setattr("app.modules.scripts.service._gemini_sdk_available", lambda: True)
        svc = ScriptAnalysisService(_settings(gemini_key="g-key"))
        client = _FakeGeminiClient(chunks)
        monkeypatch.setattr(svc, "_build_gemini_client", lambda _t: client)
        return svc, client

    def test_joins_text_parts_and_reports_usage(self, monkeypatch):
        chunks = [
            _FakeChunk([_FakePart('{"ok"')]),
            _FakeChunk([_FakePart(":true}")], finish_reason="STOP", usage=_FakeUsage()),
        ]
        svc, _ = self._service(monkeypatch, chunks)

        response = svc._call_gemini_with_retry(
            system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk"
        )

        assert svc._extract_text_response(response) == '{"ok":true}'
        assert response.stop_reason == "end_turn"
        assert response.usage.input_tokens == 120
        assert response.usage.output_tokens == 40

    def test_thought_parts_are_excluded(self, monkeypatch):
        """Thought summaries arrive as ordinary parts flagged thought=True. Let
        them through and every caller's json.loads sees reasoning prose."""
        chunks = [
            _FakeChunk([_FakePart("Let me think about the budget...", thought=True)]),
            _FakeChunk([_FakePart('{"ok":true}')], finish_reason="STOP"),
        ]
        svc, _ = self._service(monkeypatch, chunks)

        response = svc._call_gemini_with_retry(
            system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk"
        )

        assert svc._extract_text_response(response) == '{"ok":true}'

    def test_max_tokens_finish_maps_to_anthropic_stop_reason(self, monkeypatch):
        """Downstream code recovers truncated JSON off stop_reason == max_tokens,
        so Gemini's MAX_TOKENS must translate, not pass through verbatim."""
        chunks = [_FakeChunk([_FakePart('{"partial"')], finish_reason="MAX_TOKENS")]
        svc, _ = self._service(monkeypatch, chunks)

        response = svc._call_gemini_with_retry(
            system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk"
        )

        assert response.stop_reason == "max_tokens"

    def test_request_carries_system_prompt_and_thinking_level(self, monkeypatch):
        svc, client = self._service(
            monkeypatch, [_FakeChunk([_FakePart("hi")], finish_reason="STOP")]
        )

        svc._call_gemini_with_retry(
            system_prompt="sys", user_content="usr", temperature=0.3, stage="script_chunk"
        )

        config = client.calls[0]["config"]
        assert client.calls[0]["model"] == "gemini-test"
        assert client.calls[0]["contents"] == "usr"
        assert config.system_instruction == "sys"
        assert config.temperature == 0.3
        assert config.max_output_tokens == svc._gemini_max_tokens("script_chunk")
        assert config.thinking_config.thinking_level.value == "LOW"

    def test_blank_thinking_level_sends_no_thinking_config(self, monkeypatch):
        svc, client = self._service(
            monkeypatch, [_FakeChunk([_FakePart("hi")], finish_reason="STOP")]
        )
        monkeypatch.setattr(svc.settings, "GEMINI_THINKING_LEVEL", "")

        svc._call_gemini_with_retry(
            system_prompt="sys", user_content="usr", temperature=0.1, stage="script_chunk"
        )

        assert client.calls[0]["config"].thinking_config is None


class TestResponseShimCompatibility:
    """A fallback response must be indistinguishable, to downstream consumers,
    from an Anthropic Message — same .content/.stop_reason/.usage surface."""

    def test_extract_text_response_reads_shim(self):
        svc = ScriptAnalysisService(_settings())
        shim = _ProviderResponseShim(text="hello world", stop_reason="end_turn", input_tokens=1, output_tokens=2)
        assert svc._extract_text_response(shim) == "hello world"

    def test_max_tokens_stop_reason_maps_to_truncated(self):
        shim = _ProviderResponseShim(text="partial", stop_reason="max_tokens", input_tokens=1, output_tokens=2)
        assert shim.stop_reason == "max_tokens"

    def test_legacy_alias_is_the_same_class(self):
        assert _OpenAIResponseShim is _ProviderResponseShim


class TestCheckAvailableWithFallbacks:
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

    def test_gemini_reachable_is_enough(self, monkeypatch):
        """Anthropic dead and OpenAI dead, Gemini up -> the report is allowed to
        proceed and the user is charged for work that can actually be done."""
        monkeypatch.setattr("app.modules.scripts.service._gemini_sdk_available", lambda: True)
        svc = ScriptAnalysisService(_settings(gemini_key="g-key"))
        monkeypatch.setattr(svc.settings, "ANTHROPIC_API_KEY", "")

        probed: list[str] = []

        def fake_probe(provider, _timeout):
            probed.append(provider)
            if provider == "openai":
                raise Exception("insufficient_quota")

        monkeypatch.setattr(svc, "_probe_fallback_provider", fake_probe)

        assert svc.check_available() is None
        assert probed == ["openai", "gemini"]

    def test_raises_when_everything_is_unavailable(self, monkeypatch):
        monkeypatch.setattr("app.modules.scripts.service._gemini_sdk_available", lambda: True)
        svc = ScriptAnalysisService(_settings(gemini_key="g-key"))
        monkeypatch.setattr(svc.settings, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(
            svc,
            "_probe_fallback_provider",
            lambda provider, _timeout: (_ for _ in ()).throw(Exception(f"{provider} down")),
        )

        from app.modules.scripts.service import ClaudeUnavailableError
        with pytest.raises(ClaudeUnavailableError, match="every configured fallback"):
            svc.check_available()
