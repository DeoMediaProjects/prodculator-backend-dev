import json
from types import SimpleNamespace
from typing import cast

from app.core.config import Settings
from app.modules.scripts.service import ScriptAnalysisService


def _as_settings(fake: SimpleNamespace) -> Settings:
    """Cast a test double to Settings to satisfy the type checker."""
    return cast(Settings, fake)


def test_parse_json_payload_from_fenced_json():
    raw = """```json
{"locations":[{"name":"Lagos"}],"budgetEstimate":{"range":"low"}}
```"""
    data = ScriptAnalysisService._parse_json_payload(raw)
    assert data["locations"][0]["name"] == "Lagos"


def test_parse_json_payload_from_mixed_text():
    raw = """
Here is the analysis:

{
  "locations": [{"name": "London"}],
  "budgetEstimate": {"range": "medium"}
}

Let me know if you need more.
"""
    data = ScriptAnalysisService._parse_json_payload(raw)
    assert data["locations"][0]["name"] == "London"


def _build_settings(**overrides) -> Settings:
    defaults = {
        "ANTHROPIC_API_KEY": "test-key",
        "ANTHROPIC_MODEL": "claude-test",
        "ANTHROPIC_MAX_TOKENS": 4000,
        "ANTHROPIC_ANALYSIS_TIMEOUT": 120,
        "ANTHROPIC_MAX_TOKENS_SCRIPT_CHUNK": None,
        "ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE": None,
        "ANTHROPIC_MAX_TOKENS_REPORT": None,
        "ANTHROPIC_TIMEOUT_SCRIPT_CHUNK": None,
        "ANTHROPIC_TIMEOUT_SCRIPT_AGGREGATE": None,
        "ANTHROPIC_TIMEOUT_REPORT": None,
        "SCRIPT_ANALYSIS_CHUNKED_ENABLED": False,
    }
    defaults.update(overrides)
    return _as_settings(SimpleNamespace(**defaults))


class _FakeAnthropic:
    def __init__(self, *args, **kwargs):
        self.messages = self

    def create(self, **kwargs):
        return {"ok": True, "request": kwargs}


def test_stage_specific_values_override_legacy(monkeypatch):
    monkeypatch.setattr("app.modules.scripts.service.Anthropic", _FakeAnthropic)
    settings = _build_settings(
        ANTHROPIC_MAX_TOKENS_SCRIPT_CHUNK=1500,
        ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE=3000,
        ANTHROPIC_MAX_TOKENS_REPORT=7000,
        ANTHROPIC_TIMEOUT_SCRIPT_CHUNK=110,
        ANTHROPIC_TIMEOUT_SCRIPT_AGGREGATE=140,
        ANTHROPIC_TIMEOUT_REPORT=180,
    )
    service = ScriptAnalysisService(settings)

    assert service._stage_max_tokens(service._STAGE_SCRIPT_CHUNK) == 1500
    assert service._stage_max_tokens(service._STAGE_SCRIPT_ANALYSIS) == 3000
    assert service._stage_max_tokens(service._STAGE_PRODUCTION_ANALYSIS) == 7000
    assert service._stage_timeout(service._STAGE_SCRIPT_CHUNK) == 110
    assert service._stage_timeout(service._STAGE_SCRIPT_ANALYSIS) == 140
    assert service._stage_timeout(service._STAGE_PRODUCTION_ANALYSIS) == 180


def test_stage_specific_values_fall_back_to_legacy(monkeypatch):
    monkeypatch.setattr("app.modules.scripts.service.Anthropic", _FakeAnthropic)
    settings = _build_settings(
        ANTHROPIC_MAX_TOKENS=4200,
        ANTHROPIC_ANALYSIS_TIMEOUT=130,
        ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE=None,
        ANTHROPIC_TIMEOUT_SCRIPT_AGGREGATE=None,
    )
    service = ScriptAnalysisService(settings)

    assert service._stage_max_tokens(service._STAGE_SCRIPT_ANALYSIS) == 4200
    assert service._stage_timeout(service._STAGE_SCRIPT_ANALYSIS) == 130


def test_call_anthropic_uses_stage_specific_values(monkeypatch):
    monkeypatch.setattr("app.modules.scripts.service.Anthropic", _FakeAnthropic)
    settings = _build_settings(
        ANTHROPIC_MAX_TOKENS_REPORT=7100,
        ANTHROPIC_TIMEOUT_REPORT=190,
    )
    service = ScriptAnalysisService(settings)

    captured = {}

    class _FakeStream:
        def __init__(self, message):
            self._message = message

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return self._message

    class _FakeClient:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            captured["create_kwargs"] = kwargs
            return {"content": []}

        def stream(self, **kwargs):
            # The narrative (production_analysis) stage streams rather than
            # blocking on a single create() read.
            captured["create_kwargs"] = kwargs
            return _FakeStream({"content": []})

    def _fake_build_client(timeout_seconds: int):
        captured["timeout"] = timeout_seconds
        return _FakeClient()

    monkeypatch.setattr(service, "_build_client", _fake_build_client)
    service._call_anthropic_with_retry(
        system_prompt="system",
        user_content="user",
        temperature=0.2,
        stage=service._STAGE_PRODUCTION_ANALYSIS,
    )

    assert captured["timeout"] == 190
    assert captured["create_kwargs"]["max_tokens"] == 7100


def test_build_script_chunks_respects_max_chunks_and_overlap(monkeypatch):
    monkeypatch.setattr("app.modules.scripts.service.Anthropic", _FakeAnthropic)
    settings = _build_settings(
        SCRIPT_ANALYSIS_CHUNKED_ENABLED=True,
        SCRIPT_CHUNK_TARGET_TOKENS=40,
        SCRIPT_CHUNK_OVERLAP_TOKENS=10,
        SCRIPT_MAX_CHUNKS=3,
    )
    service = ScriptAnalysisService(settings)
    script_text = "\n\n".join(
        [
            "INT. OFFICE - DAY\n" + ("A" * 260),
            "EXT. STREET - NIGHT\n" + ("B" * 260),
            "INT. HOUSE - NIGHT\n" + ("C" * 260),
            "EXT. HARBOR - DAY\n" + ("D" * 260),
        ]
    )

    chunks = service._build_script_chunks(script_text)
    assert 1 < len(chunks) <= 3
    # overlap should carry tail context into the next chunk
    assert any("INT. OFFICE" in chunk for chunk in chunks[:2])


def test_analyze_prefers_chunked_path_when_enabled(monkeypatch):
    monkeypatch.setattr("app.modules.scripts.service.Anthropic", _FakeAnthropic)
    settings = _build_settings(SCRIPT_ANALYSIS_CHUNKED_ENABLED=True)
    service = ScriptAnalysisService(settings)

    class _SentinelAnalysis:
        rawResponse = None

    sentinel = _SentinelAnalysis()
    monkeypatch.setattr(service, "_analyze_chunked", lambda *_args, **_kwargs: sentinel)

    result = service.analyze("INT. ROOM - DAY", "Test")
    assert result is sentinel


def test_aggregate_chunk_results_merges_locations_and_budget(monkeypatch):
    monkeypatch.setattr("app.modules.scripts.service.Anthropic", _FakeAnthropic)
    service = ScriptAnalysisService(_build_settings())
    chunk_results = [
        {
            "locations": [
                {
                    "name": "Valletta",
                    "country": "Malta",
                    "territory": "Malta",
                    "frequency": 3,
                    "isMainLocation": True,
                }
            ],
            "budgetEstimate": {"range": "low", "indicators": ["practical locations"]},
            "productionScale": {
                "crewSize": "medium",
                "principalCast": "small",
                "supportingCast": "small",
                "backgroundExtras": "small",
                "estimatedShootingDays": 24,
            },
            "equipment": {"cameraEquipment": "arri", "specialEquipment": [], "vfxRequirements": "minimal"},
            "metadata": {
                "genres": ["Drama"],
                "format": "feature",
                "tone": "Grounded",
                "targetAudience": "Adult",
            },
            "challenges": {
                "weatherDependent": False,
                "historicalPeriod": False,
                "specialPermits": False,
                "stunts": False,
                "animalWrangling": False,
                "waterWork": False,
                "nightShooting": False,
                "notes": [],
            },
        },
        {
            "locations": [
                {
                    "name": "Valletta",
                    "country": "Malta",
                    "territory": "Malta",
                    "frequency": 2,
                    "isMainLocation": False,
                }
            ],
            "budgetEstimate": {"range": "low", "indicators": ["small cast"]},
            "productionScale": {
                "crewSize": "small",
                "principalCast": "small",
                "supportingCast": "small",
                "backgroundExtras": "small",
                "estimatedShootingDays": 20,
            },
            "equipment": {"cameraEquipment": "arri", "specialEquipment": [], "vfxRequirements": "minimal"},
            "metadata": {
                "genres": ["Drama"],
                "format": "feature",
                "tone": "Grounded",
                "targetAudience": "Adult",
            },
            "challenges": {
                "weatherDependent": False,
                "historicalPeriod": False,
                "specialPermits": False,
                "stunts": False,
                "animalWrangling": False,
                "waterWork": False,
                "nightShooting": True,
                "notes": ["Night exteriors"],
            },
        },
    ]

    result = service._aggregate_chunk_results(
        chunk_results,
        script_title="My Script",
        total_chunks=2,
        failed_chunks=0,
        failed_chunk_details=[],
    )

    assert result.locations[0].territory == "Malta"
    assert result.locations[0].frequency == 5
    assert result.budgetEstimate.range == "low"
    assert result.challenges.nightShooting is True
    assert result.budgetEstimate.confidence >= 0.25
    telemetry = json.loads(result.rawResponse or "{}")
    assert telemetry["chunkTelemetry"]["totalChunks"] == 2
    assert telemetry["chunkTelemetry"]["droppedChunks"] == 0
    assert "sectionConfidence" in telemetry


def test_aggregate_chunk_results_includes_failure_telemetry(monkeypatch):
    monkeypatch.setattr("app.modules.scripts.service.Anthropic", _FakeAnthropic)
    service = ScriptAnalysisService(_build_settings())
    chunk_results = [
        {
            "locations": [],
            "budgetEstimate": {"range": "medium", "indicators": []},
            "productionScale": {
                "crewSize": "medium",
                "principalCast": "medium",
                "supportingCast": "medium",
                "backgroundExtras": "small",
                "estimatedShootingDays": 30,
            },
            "equipment": {"cameraEquipment": "arri", "specialEquipment": [], "vfxRequirements": "moderate"},
            "metadata": {
                "genres": ["Thriller"],
                "format": "feature",
                "tone": "Tense",
                "targetAudience": "Adult",
            },
            "challenges": {
                "weatherDependent": False,
                "historicalPeriod": False,
                "specialPermits": False,
                "stunts": False,
                "animalWrangling": False,
                "waterWork": False,
                "nightShooting": False,
                "notes": [],
            },
        }
    ]

    result = service._aggregate_chunk_results(
        chunk_results,
        script_title="Telemetry Script",
        total_chunks=3,
        failed_chunks=2,
        failed_chunk_details=[{"chunk": 2, "error": "timeout"}, {"chunk": 3, "error": "max_tokens"}],
    )
    telemetry = json.loads(result.rawResponse or "{}")
    assert telemetry["chunkTelemetry"]["failedChunks"] == 2
    assert telemetry["chunkTelemetry"]["droppedChunks"] == 0
    assert len(telemetry["chunkTelemetry"]["failedChunkDetails"]) == 2


def test_extract_analysis_metadata_reads_structured_payload(monkeypatch):
    monkeypatch.setattr("app.modules.scripts.service.Anthropic", _FakeAnthropic)
    payload = json.dumps(
        {
            "mode": "chunked",
            "fallbackUsed": False,
            "chunkTelemetry": {"totalChunks": 5, "usedChunks": 5, "failedChunks": 0},
            "sectionConfidence": {"budget": 0.8},
            "overallConfidence": 0.81,
        }
    )
    meta = ScriptAnalysisService.extract_analysis_metadata(payload)
    assert meta["mode"] == "chunked"
    assert meta["chunkTelemetry"]["totalChunks"] == 5
    assert meta["overallConfidence"] == 0.81


def test_analyze_with_meta_records_chunked_fallback(monkeypatch):
    monkeypatch.setattr("app.modules.scripts.service.Anthropic", _FakeAnthropic)
    service = ScriptAnalysisService(_build_settings(SCRIPT_ANALYSIS_CHUNKED_ENABLED=True))

    monkeypatch.setattr(service, "_analyze_chunked", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("boom")))

    result, meta = service.analyze_with_meta("INT. ROOM - DAY", "My Script")
    assert result.rawResponse is not None
    assert meta["chunkedFailed"] is True
    assert meta["fallbackUsed"] is True
    assert meta["mode"] == "single_pass_fallback"
    assert meta["reason"] == "chunked_analysis_failed"


# --- characters array (hallucinated-names fix) ---


def test_aggregate_chunk_results_collects_characters_by_frequency():
    service = ScriptAnalysisService(_build_settings())
    chunk_results = [
        {"characters": ["AMARA", "Dele", "amara"], "locations": []},
        {"characters": ["AMARA", "KEMI"], "locations": []},
        {"characters": [" ", "", "AMARA"], "locations": []},
    ]
    result = service._aggregate_chunk_results(
        chunk_results,
        script_title="Test",
        total_chunks=3,
        failed_chunks=0,
    )
    # Deduped case-insensitively, first casing kept, most frequent first
    assert result.characters[0] == "AMARA"
    assert set(result.characters) == {"AMARA", "Dele", "KEMI"}


def test_aggregate_chunk_results_without_characters_yields_empty_list():
    service = ScriptAnalysisService(_build_settings())
    result = service._aggregate_chunk_results(
        [{"locations": []}],
        script_title="Test",
        total_chunks=1,
        failed_chunks=0,
    )
    assert result.characters == []


def test_sanitize_passes_characters_through_and_caps_at_30():
    service = ScriptAnalysisService(_build_settings())
    result = service._sanitize({"characters": [f"NAME{i}" for i in range(40)]})
    assert len(result.characters) == 30
    assert result.characters[0] == "NAME0"


def test_sanitize_defaults_characters_to_empty():
    service = ScriptAnalysisService(_build_settings())
    result = service._sanitize({})
    assert result.characters == []
