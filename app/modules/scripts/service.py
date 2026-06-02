from collections import Counter
import json
import logging
import re
from time import perf_counter, sleep
from typing import Any

import pdfplumber
from anthropic import Anthropic

from app.core.config import Settings
from app.modules.reports.validator import ReportValidator
from app.modules.scripts.schemas import (
    BudgetEstimate,
    Challenges,
    Equipment,
    Location,
    Metadata,
    ProductionScale,
    ScriptAnalysisResult,
)

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"pdf", "txt", "fountain", "fdx"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_PROMPT_TEXT_CHARS = 240
MAX_PROMPT_LIST_ITEMS = 8
CHARS_PER_TOKEN_ESTIMATE = 4

BUDGET_BOUNDS_USD = {
    "micro": (50_000, 500_000),
    "low": (500_000, 5_000_000),
    "medium": (5_000_000, 30_000_000),
    "high": (30_000_000, 100_000_000),
    "tentpole": (100_000_000, 250_000_000),
}
SCALE_ORDER = {"small": 1, "medium": 2, "large": 3, "extra_large": 4}
VFX_ORDER = {"minimal": 1, "moderate": 2, "heavy": 3, "intensive": 4}
CAMERA_OPTIONS = {"arri", "red", "sony", "panavision", "blackmagic", "canon", "other"}
FORMAT_OPTIONS = {"feature", "tv_series", "limited_series", "documentary", "short"}

SCRIPT_CHUNK_EXTRACTION_PROMPT = """You extract production signals from a script chunk.

GOLDEN RULE: If it is not explicitly written in the script, it does not exist.
No inference, no assumption. Count what is written.
- If a scene heading says INT., it is interior. If it says EXT., it is exterior.
- If a language is not spoken by a character, do not list it.
- Count scene headings (INT./EXT./INT-EXT.) for scene counts.
- Count NIGHT/DUSK/DAWN for night scenes, DAY/MORNING/AFTERNOON for day scenes.
- Only list languages with actual dialogue written in that language OR explicit stage direction.

Return ONLY valid JSON (no markdown fences, no extra text) with these top-level keys:
- "locations": array of {name, country, territory, frequency (int), isMainLocation (bool)}
- "budgetEstimate": {range, indicators (array of strings)}
- "productionScale": {crewSize, principalCast, supportingCast, backgroundExtras, estimatedShootingDays (int)}
- "equipment": {cameraEquipment, specialEquipment (array), vfxRequirements}
- "metadata": {genres (array), format, tone, targetAudience}
- "challenges": {weatherDependent (bool), historicalPeriod (bool), specialPermits (bool), stunts (bool), animalWrangling (bool), waterWork (bool), nightShooting (bool), notes (array), extSceneCount (int), intSceneCount (int), nightSceneCount (int), waterSceneCount (int), vfxHeavySceneCount (int), daySceneCount (int), languages (array), voiceOvers (bool), namedLocations (array of {name, count}), musicPerformanceScenes (int), conflictType, irresolution (bool), stuntSequences (int), crowdScenes (int)}

Use only evidence present in this chunk.
For unknown values use conservative defaults:
- budgetEstimate.range: "unknown"
- productionScale labels: "unknown"
- equipment.cameraEquipment: "other"
- equipment.vfxRequirements: "unknown"
- metadata.format: "unknown"
Keep lists concise.
"""

SCRIPT_CHUNK_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "country": {"type": "string"},
                    "territory": {"type": "string"},
                    "frequency": {"type": "integer"},
                    "isMainLocation": {"type": "boolean"},
                },
                "required": ["name", "country", "territory", "frequency", "isMainLocation"],
            },
        },
        "budgetEstimate": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "range": {"type": "string"},
                "indicators": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["range", "indicators"],
        },
        "productionScale": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "crewSize": {"type": "string"},
                "principalCast": {"type": "string"},
                "supportingCast": {"type": "string"},
                "backgroundExtras": {"type": "string"},
                "estimatedShootingDays": {"type": "integer"},
            },
            "required": [
                "crewSize",
                "principalCast",
                "supportingCast",
                "backgroundExtras",
                "estimatedShootingDays",
            ],
        },
        "equipment": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cameraEquipment": {"type": "string"},
                "specialEquipment": {"type": "array", "items": {"type": "string"}},
                "vfxRequirements": {"type": "string"},
            },
            "required": ["cameraEquipment", "specialEquipment", "vfxRequirements"],
        },
        "metadata": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "genres": {"type": "array", "items": {"type": "string"}},
                "format": {"type": "string"},
                "tone": {"type": "string"},
                "targetAudience": {"type": "string"},
            },
            "required": ["genres", "format", "tone", "targetAudience"],
        },
        "challenges": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "weatherDependent": {"type": "boolean"},
                "historicalPeriod": {"type": "boolean"},
                "specialPermits": {"type": "boolean"},
                "stunts": {"type": "boolean"},
                "animalWrangling": {"type": "boolean"},
                "waterWork": {"type": "boolean"},
                "nightShooting": {"type": "boolean"},
                "notes": {"type": "array", "items": {"type": "string"}},
                "extSceneCount": {"type": "integer"},
                "intSceneCount": {"type": "integer"},
                "nightSceneCount": {"type": "integer"},
                "waterSceneCount": {"type": "integer"},
                "vfxHeavySceneCount": {"type": "integer"},
                # v3 structured extraction fields
                "daySceneCount": {"type": "integer"},
                "languages": {"type": "array", "items": {"type": "string"}},
                "voiceOvers": {"type": "boolean"},
                "namedLocations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string"},
                            "count": {"type": "integer"},
                        },
                        "required": ["name", "count"],
                    },
                },
                "musicPerformanceScenes": {"type": "integer"},
                "conflictType": {"type": "string"},
                "irresolution": {"type": "boolean"},
                "stuntSequences": {"type": "integer"},
                "crowdScenes": {"type": "integer"},
            },
            "required": [
                "weatherDependent",
                "historicalPeriod",
                "specialPermits",
                "stunts",
                "animalWrangling",
                "waterWork",
                "nightShooting",
                "notes",
            ],
        },
    },
    "required": [
        "locations",
        "budgetEstimate",
        "productionScale",
        "equipment",
        "metadata",
        "challenges",
    ],
}

class ScriptAnalysisService:
    _STAGE_SCRIPT_CHUNK = "script_chunk"
    _STAGE_SCRIPT_AGGREGATE = "script_aggregate"
    _STAGE_SCRIPT_ANALYSIS = "script_analysis"
    _STAGE_PRODUCTION_ANALYSIS = "production_analysis"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = self._build_client(settings.ANTHROPIC_ANALYSIS_TIMEOUT)

    def _build_client(self, timeout_seconds: int) -> Anthropic:
        return Anthropic(
            api_key=self.settings.ANTHROPIC_API_KEY,
            timeout=float(timeout_seconds),
            max_retries=0,  # We handle retries ourselves in _call_anthropic_with_retry
        )

    def _stage_max_tokens(self, stage: str) -> int:
        legacy = self.settings.ANTHROPIC_MAX_TOKENS
        stage_specific: int | None
        if stage == self._STAGE_SCRIPT_CHUNK:
            stage_specific = getattr(self.settings, "ANTHROPIC_MAX_TOKENS_SCRIPT_CHUNK", None)
        elif stage in (self._STAGE_SCRIPT_AGGREGATE, self._STAGE_SCRIPT_ANALYSIS):
            stage_specific = getattr(self.settings, "ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE", None)
        elif stage == self._STAGE_PRODUCTION_ANALYSIS:
            stage_specific = getattr(self.settings, "ANTHROPIC_MAX_TOKENS_REPORT", None)
        else:
            stage_specific = None

        return stage_specific if isinstance(stage_specific, int) and stage_specific > 0 else legacy

    def _stage_timeout(self, stage: str) -> int:
        legacy = self.settings.ANTHROPIC_ANALYSIS_TIMEOUT
        stage_specific: int | None
        if stage == self._STAGE_SCRIPT_CHUNK:
            stage_specific = getattr(self.settings, "ANTHROPIC_TIMEOUT_SCRIPT_CHUNK", None)
        elif stage in (self._STAGE_SCRIPT_AGGREGATE, self._STAGE_SCRIPT_ANALYSIS):
            stage_specific = getattr(self.settings, "ANTHROPIC_TIMEOUT_SCRIPT_AGGREGATE", None)
        elif stage == self._STAGE_PRODUCTION_ANALYSIS:
            stage_specific = getattr(self.settings, "ANTHROPIC_TIMEOUT_REPORT", None)
        else:
            stage_specific = None

        return stage_specific if isinstance(stage_specific, int) and stage_specific > 0 else legacy

    def validate_file(self, filename: str, file_size: int) -> tuple[bool, str | None]:
        """Validate script file type and size."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            return False, f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        if file_size > MAX_FILE_SIZE:
            return False, "File too large. Maximum size: 50MB"
        return True, None

    def extract_text_from_pdf(self, file_bytes: bytes) -> str:
        """Extract text from PDF using pdfplumber."""
        import io

        started = perf_counter()
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        combined = "\n".join(text_parts)
        logger.debug(
            "PDF text extraction complete: pages=%s bytes=%s chars=%s elapsed_ms=%s",
            page_count,
            len(file_bytes),
            len(combined),
            int((perf_counter() - started) * 1000),
        )
        return combined

    def extract_text(self, filename: str, file_bytes: bytes) -> str:
        """Extract text from various script formats."""
        ext = filename.rsplit(".", 1)[-1].lower()
        logger.debug(
            "Extracting text from file: filename=%s ext=%s bytes=%s",
            filename,
            ext,
            len(file_bytes),
        )
        if ext == "pdf":
            return self.extract_text_from_pdf(file_bytes)
        return file_bytes.decode("utf-8")

    def analyze(self, script_content: str, script_title: str) -> ScriptAnalysisResult:
        """Analyze script using Anthropic Claude."""
        analysis, _meta = self.analyze_with_meta(script_content, script_title)
        return analysis

    def analyze_with_meta(
        self,
        script_content: str,
        script_title: str,
    ) -> tuple[ScriptAnalysisResult, dict[str, Any]]:
        """Analyze script and return result plus metadata about the analysis path."""
        if not self.settings.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key is not configured")

        chunked_enabled = bool(getattr(self.settings, "SCRIPT_ANALYSIS_CHUNKED_ENABLED", False))
        analysis_meta: dict[str, Any] = {
            "mode": "chunked",
            "chunkedEnabled": chunked_enabled,
            "fallbackUsed": False,
        }
        if not chunked_enabled:
            logger.warning(
                "SCRIPT_ANALYSIS_CHUNKED_ENABLED is false, but legacy hard-trim path is removed; running chunked analysis anyway"
            )

        try:
            result = self._analyze_chunked(script_content, script_title)
            analysis_meta.update(self.extract_analysis_metadata(result.rawResponse))
            analysis_meta.setdefault("mode", "chunked")
            analysis_meta.setdefault("fallbackUsed", False)
            self._emit_script_analysis_metrics(script_title, script_content, analysis_meta)
            return result, analysis_meta
        except Exception as exc:
            analysis_meta.update(
                {
                    "chunkedFailed": True,
                    "chunkedError": str(exc)[:220],
                    "fallbackUsed": True,
                    "reason": "chunked_analysis_failed",
                }
            )
            logger.exception(
                "Chunked script analysis failed, using default fallback: title=%s error=%s",
                script_title,
                exc,
            )
            fallback_result = self._fallback(script_title, reason="chunked_analysis_failed")
            analysis_meta.update(self.extract_analysis_metadata(fallback_result.rawResponse))
            self._emit_script_analysis_metrics(script_title, script_content, analysis_meta)
            return fallback_result, analysis_meta

    def _emit_script_analysis_metrics(
        self,
        script_title: str,
        script_content: str,
        analysis_meta: dict[str, Any],
    ) -> None:
        chunk_telemetry = analysis_meta.get("chunkTelemetry")
        chunk_telemetry = chunk_telemetry if isinstance(chunk_telemetry, dict) else {}
        mode = str(analysis_meta.get("mode", "single_pass"))
        fallback_used = bool(analysis_meta.get("fallbackUsed"))
        if mode.startswith("single"):
            chunk_count = 1
        else:
            chunk_count = chunk_telemetry.get("totalChunks")

        stop_reason = analysis_meta.get("reason")
        if not stop_reason:
            stop_reasons = chunk_telemetry.get("stopReasons")
            if isinstance(stop_reasons, dict) and stop_reasons:
                stop_reason = ",".join([f"{k}:{v}" for k, v in sorted(stop_reasons.items())])
        if not stop_reason:
            stop_reason = "none"

        metrics = {
            "script_chars": len(script_content),
            "estimated_input_tokens": self._estimate_tokens(script_content),
            "chunk_count": chunk_count,
            "dropped_chunks": chunk_telemetry.get("droppedChunks", 0),
            "stop_reason": stop_reason,
            "fallback_used": fallback_used,
            "mode": mode,
        }
        logger.info("Script analysis metrics: title=%s metrics=%s", script_title, metrics)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text.strip()) // CHARS_PER_TOKEN_ESTIMATE) if text.strip() else 0

    @staticmethod
    def extract_analysis_metadata(raw_response: str | None) -> dict[str, Any]:
        """Extract structured metadata from rawResponse when available."""
        if not raw_response:
            return {}

        payload = raw_response.strip()
        if not payload:
            return {}

        parsed: dict[str, Any] | None = None
        if payload.startswith("{") and payload.endswith("}"):
            try:
                loaded = json.loads(payload)
                if isinstance(loaded, dict):
                    parsed = loaded
            except (json.JSONDecodeError, ValueError):
                parsed = None

        if isinstance(parsed, dict):
            metadata_keys = {
                "mode",
                "fallbackUsed",
                "reason",
                "chunkTelemetry",
                "sectionConfidence",
                "overallConfidence",
                "aggregationEvidence",
            }
            if metadata_keys.intersection(parsed.keys()):
                metadata: dict[str, Any] = {}
                for key in [
                    "mode",
                    "fallbackUsed",
                    "reason",
                    "chunkTelemetry",
                    "sectionConfidence",
                    "overallConfidence",
                    "aggregationEvidence",
                ]:
                    if key in parsed:
                        metadata[key] = parsed[key]
                return metadata

        if "fallback analysis used" in payload.lower():
            return {"mode": "single_pass_fallback", "fallbackUsed": True, "reason": "fallback_marker"}

        return {}

    def _analyze_chunked(self, script_content: str, script_title: str) -> ScriptAnalysisResult:
        started = perf_counter()
        chunks, chunk_stats = self._build_script_chunks_with_stats(script_content)
        if not chunks:
            raise ValueError("Script file appears to be empty")

        logger.info(
            "Chunked script analysis started: title=%s input_chars=%s chunk_count=%s dropped_chunks=%s model=%s chunk_tokens=%s chunk_timeout_s=%s",
            script_title,
            len(script_content),
            len(chunks),
            chunk_stats.get("droppedChunks", 0),
            self.settings.ANTHROPIC_MODEL,
            self._stage_max_tokens(self._STAGE_SCRIPT_CHUNK),
            self._stage_timeout(self._STAGE_SCRIPT_CHUNK),
        )

        chunk_results: list[dict[str, Any]] = []
        failed_chunks = 0
        failed_chunk_details: list[dict[str, Any]] = []
        stop_reason_counts: Counter[str] = Counter()
        for idx, chunk_text in enumerate(chunks, start=1):
            try:
                chunk_payload = self._extract_chunk_analysis(chunk_text, idx, len(chunks))
                chunk_payload["_chunkIndex"] = idx
                chunk_results.append(chunk_payload)
            except Exception as exc:
                failed_chunks += 1
                stop_reason = self._infer_stop_reason(exc)
                if stop_reason:
                    stop_reason_counts[stop_reason] += 1
                failed_chunk_details.append(
                    {
                        "chunk": idx,
                        "error": str(exc)[:220],
                        "stopReason": stop_reason or "unknown",
                    }
                )
                logger.warning(
                    "Chunk extraction failed: title=%s chunk=%s/%s chars=%s stop_reason=%s error=%s",
                    script_title,
                    idx,
                    len(chunks),
                    len(chunk_text),
                    stop_reason or "unknown",
                    exc,
                )

        if not chunk_results:
            raise ValueError("Chunk extraction produced no usable results")

        aggregated = self._aggregate_chunk_results(
            chunk_results,
            script_title=script_title,
            total_chunks=len(chunks),
            failed_chunks=failed_chunks,
            failed_chunk_details=failed_chunk_details,
            dropped_chunks=chunk_stats.get("droppedChunks", 0),
            generated_chunks=chunk_stats.get("generatedChunks", len(chunks)),
            stop_reasons=dict(stop_reason_counts),
        )
        if failed_chunks:
            logger.warning(
                "Chunked script analysis had partial failures: title=%s failed_chunks=%s/%s failed_indices=%s",
                script_title,
                failed_chunks,
                len(chunks),
                [detail.get("chunk") for detail in failed_chunk_details][:20],
            )
        logger.info(
            "Chunked script analysis completed: title=%s succeeded_chunks=%s/%s failed_chunks=%s locations=%s budget_estimate=%s elapsed_ms=%s",
            script_title,
            len(chunk_results),
            len(chunks),
            failed_chunks,
            len(aggregated.locations),
            aggregated.budgetEstimate.range,  # AI-estimated range from script content
            int((perf_counter() - started) * 1000),
        )
        return aggregated

    def _build_script_chunks(self, script_content: str) -> list[str]:
        chunks, _stats = self._build_script_chunks_with_stats(script_content)
        return chunks

    def _build_script_chunks_with_stats(self, script_content: str) -> tuple[list[str], dict[str, int]]:
        clean = script_content.strip()
        if not clean:
            return [], {"generatedChunks": 0, "returnedChunks": 0, "droppedChunks": 0}

        target_tokens = max(int(getattr(self.settings, "SCRIPT_CHUNK_TARGET_TOKENS", 1800) or 1800), 200)
        overlap_tokens = max(int(getattr(self.settings, "SCRIPT_CHUNK_OVERLAP_TOKENS", 200) or 0), 0)
        max_chunks = max(int(getattr(self.settings, "SCRIPT_MAX_CHUNKS", 80) or 80), 1)
        overlap_tokens = min(overlap_tokens, target_tokens // 2)

        target_chars = target_tokens * CHARS_PER_TOKEN_ESTIMATE
        overlap_chars = overlap_tokens * CHARS_PER_TOKEN_ESTIMATE

        scenes = self._split_by_scene_headings(clean)
        packed: list[str] = []
        current = ""
        for scene in scenes:
            if len(scene) > target_chars:
                if current:
                    packed.append(current.strip())
                    current = ""
                packed.extend(self._split_large_block(scene, target_chars))
                continue

            candidate = scene if not current else f"{current}\n\n{scene}"
            if current and len(candidate) > target_chars:
                packed.append(current.strip())
                current = scene
            else:
                current = candidate

        if current:
            packed.append(current.strip())

        if not packed:
            packed = self._split_large_block(clean, target_chars)

        if overlap_chars > 0 and len(packed) > 1:
            overlapped = [packed[0]]
            for idx in range(1, len(packed)):
                tail = packed[idx - 1][-overlap_chars:]
                overlapped.append(f"{tail}\n\n{packed[idx]}".strip())
            packed = overlapped

        generated_chunks = len(packed)
        if len(packed) > max_chunks:
            logger.warning(
                "Chunk count exceeded limit; truncating: chunks=%s max_chunks=%s",
                len(packed),
                max_chunks,
            )
            packed = packed[:max_chunks]
            packed[-1] = packed[-1] + "\n\n[...ADDITIONAL SCRIPT CHUNKS OMITTED DUE TO LIMIT...]"

        final_chunks = [chunk for chunk in packed if chunk]
        dropped_chunks = max(0, generated_chunks - len(final_chunks))
        return final_chunks, {
            "generatedChunks": generated_chunks,
            "returnedChunks": len(final_chunks),
            "droppedChunks": dropped_chunks,
        }

    @staticmethod
    def _split_by_scene_headings(script_text: str) -> list[str]:
        heading_re = re.compile(r"(?m)^(?:\s{0,8})(?:INT\.?|EXT\.?|INT/EXT\.?|I/E\.?|EST\.?)\b")
        matches = list(heading_re.finditer(script_text))
        if not matches:
            return [script_text]

        boundaries = [m.start() for m in matches] + [len(script_text)]
        scenes: list[str] = []
        for idx in range(len(boundaries) - 1):
            chunk = script_text[boundaries[idx]:boundaries[idx + 1]].strip()
            if chunk:
                scenes.append(chunk)
        return scenes or [script_text]

    @staticmethod
    def _split_large_block(text: str, target_chars: int) -> list[str]:
        if len(text) <= target_chars:
            return [text.strip()]

        chunks = [text[idx:idx + target_chars].strip() for idx in range(0, len(text), target_chars)]
        if len(chunks) > 1 and len(chunks[-1]) < max(target_chars // 5, 200):
            chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}".strip()
            chunks.pop()
        return [c for c in chunks if c]

    def _extract_chunk_analysis(self, chunk_text: str, chunk_index: int, total_chunks: int) -> dict[str, Any]:
        prompt_attempts = [chunk_text]
        if len(chunk_text) > 4_000:
            prompt_attempts.append(chunk_text[: int(len(chunk_text) * 0.65)])

        last_error: Exception | None = None
        for prompt_text in prompt_attempts:
            response = self._call_anthropic_with_retry(
                system_prompt=SCRIPT_CHUNK_EXTRACTION_PROMPT,
                user_content=(
                    f"Chunk {chunk_index} of {total_chunks}.\n"
                    f"Estimate frequency relative to this chunk only.\n\n"
                    f"=== CHUNK TEXT START ===\n{prompt_text}\n=== CHUNK TEXT END ==="
                ),
                temperature=0.1,
                stage=self._STAGE_SCRIPT_CHUNK,
                # NOTE: output_config with json_schema causes timeouts on claude-sonnet-4-6
                # with this schema size. Using prompt-based JSON instead.
            )
            raw = self._extract_text_response(response)
            try:
                return self._parse_json_payload(raw)
            except Exception:
                if getattr(response, "stop_reason", None) == "max_tokens":
                    last_error = ValueError("Chunk extraction output was truncated at max_tokens")
                    continue
                raise

        if last_error:
            raise last_error
        raise ValueError("Chunk extraction failed")

    @staticmethod
    def _infer_stop_reason(exc: Exception) -> str | None:
        message = str(exc).lower()
        if "max_tokens" in message or "truncated" in message:
            return "max_tokens"
        if "timeout" in message or "timed out" in message:
            return "timeout"
        if "rate limit" in message or "429" in message:
            return "rate_limit"
        if "parse" in message or "json" in message:
            return "parse_error"
        return None

    def _aggregate_chunk_results(
        self,
        chunk_results: list[dict[str, Any]],
        *,
        script_title: str,
        total_chunks: int,
        failed_chunks: int,
        failed_chunk_details: list[dict[str, Any]] | None = None,
        dropped_chunks: int = 0,
        generated_chunks: int | None = None,
        stop_reasons: dict[str, int] | None = None,
    ) -> ScriptAnalysisResult:
        location_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        budget_ranges: list[str] = []
        budget_indicators: list[str] = []
        crew_values: list[str] = []
        principal_values: list[str] = []
        supporting_values: list[str] = []
        extras_values: list[str] = []
        shoot_days_values: list[int] = []
        camera_values: list[str] = []
        special_equipment_values: list[str] = []
        vfx_values: list[str] = []
        genre_values: list[str] = []
        format_values: list[str] = []
        tone_values: list[str] = []
        audience_values: list[str] = []
        challenge_notes: list[str] = []
        challenge_flags = {
            "weatherDependent": False,
            "historicalPeriod": False,
            "specialPermits": False,
            "stunts": False,
            "animalWrangling": False,
            "waterWork": False,
            "nightShooting": False,
        }
        challenge_true_counts = {key: 0 for key in challenge_flags.keys()}
        # Signal count accumulators
        ext_scene_total = 0
        int_scene_total = 0
        night_scene_total = 0
        water_scene_total = 0
        vfx_heavy_scene_total = 0
        # v3 accumulators
        day_scene_total = 0
        music_performance_total = 0
        stunt_sequences_total = 0
        crowd_scenes_total = 0
        all_languages: list[str] = []
        voice_overs_seen = False
        named_locations_agg: dict[str, int] = {}
        conflict_type_values: list[str] = []
        irresolution_values: list[bool] = []
        section_signal_counts = {
            "locations": 0,
            "budget": 0,
            "productionScale": 0,
            "equipment": 0,
            "metadata": 0,
            "challenges": 0,
        }
        failed_chunk_details = failed_chunk_details or []
        used_chunks = len(chunk_results)
        stop_reasons = stop_reasons or {}
        generated_chunks = generated_chunks if isinstance(generated_chunks, int) else total_chunks

        for chunk in chunk_results:
            locations = chunk.get("locations", []) if isinstance(chunk.get("locations"), list) else []
            if locations:
                section_signal_counts["locations"] += 1
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                territory = str(loc.get("territory", "")).strip() or "Unknown"
                name = str(loc.get("name", "")).strip() or territory
                country = str(loc.get("country", "")).strip() or "Unknown"
                frequency = loc.get("frequency", 1)
                if not isinstance(frequency, int):
                    try:
                        frequency = int(frequency)
                    except (TypeError, ValueError):
                        frequency = 1
                frequency = max(1, frequency)
                is_main = bool(loc.get("isMainLocation", False))
                key = (territory.lower(), name.lower(), country.lower())
                existing = location_map.get(key)
                if existing:
                    existing["frequency"] += frequency
                    existing["isMainLocation"] = existing["isMainLocation"] or is_main
                else:
                    location_map[key] = {
                        "name": name,
                        "country": country,
                        "territory": territory,
                        "frequency": frequency,
                        "isMainLocation": is_main,
                    }

            budget = chunk.get("budgetEstimate", {}) if isinstance(chunk.get("budgetEstimate"), dict) else {}
            budget_range = str(budget.get("range", "")).strip().lower()
            if budget_range in BUDGET_BOUNDS_USD:
                budget_ranges.append(budget_range)
                section_signal_counts["budget"] += 1
            indicators = budget.get("indicators", [])
            if isinstance(indicators, list):
                budget_indicators.extend([str(i).strip() for i in indicators if str(i).strip()])

            scale = chunk.get("productionScale", {}) if isinstance(chunk.get("productionScale"), dict) else {}
            crew_value = str(scale.get("crewSize", "")).strip().lower()
            principal_value = str(scale.get("principalCast", "")).strip().lower()
            supporting_value = str(scale.get("supportingCast", "")).strip().lower()
            extras_value = str(scale.get("backgroundExtras", "")).strip().lower()
            crew_values.append(crew_value)
            principal_values.append(principal_value)
            supporting_values.append(supporting_value)
            extras_values.append(extras_value)
            if any(value in SCALE_ORDER for value in (crew_value, principal_value, supporting_value, extras_value)):
                section_signal_counts["productionScale"] += 1
            shoot_days = scale.get("estimatedShootingDays")
            if isinstance(shoot_days, int) and shoot_days > 0:
                shoot_days_values.append(shoot_days)

            equipment = chunk.get("equipment", {}) if isinstance(chunk.get("equipment"), dict) else {}
            camera_value = str(equipment.get("cameraEquipment", "")).strip().lower()
            vfx_value = str(equipment.get("vfxRequirements", "")).strip().lower()
            camera_values.append(camera_value)
            vfx_values.append(vfx_value)
            specials = equipment.get("specialEquipment", [])
            if isinstance(specials, list):
                special_equipment_values.extend([str(item).strip() for item in specials if str(item).strip()])
            if camera_value in CAMERA_OPTIONS or vfx_value in VFX_ORDER or (
                isinstance(specials, list) and len(specials) > 0
            ):
                section_signal_counts["equipment"] += 1

            metadata = chunk.get("metadata", {}) if isinstance(chunk.get("metadata"), dict) else {}
            genres = metadata.get("genres", [])
            if isinstance(genres, list):
                genre_values.extend([str(g).strip() for g in genres if str(g).strip()])
            format_value = str(metadata.get("format", "")).strip().lower()
            format_values.append(format_value)
            tone = str(metadata.get("tone", "")).strip()
            if tone:
                tone_values.append(tone[:180])
            audience = str(metadata.get("targetAudience", "")).strip()
            if audience:
                audience_values.append(audience[:180])
            if (isinstance(genres, list) and len(genres) > 0) or format_value in FORMAT_OPTIONS or tone or audience:
                section_signal_counts["metadata"] += 1

            challenges = chunk.get("challenges", {}) if isinstance(chunk.get("challenges"), dict) else {}
            has_challenge_signal = False
            for key in challenge_flags.keys():
                if bool(challenges.get(key, False)):
                    challenge_true_counts[key] += 1
                    has_challenge_signal = True
            notes = challenges.get("notes", [])
            if isinstance(notes, list):
                challenge_notes.extend([str(n).strip() for n in notes if str(n).strip()])
                has_challenge_signal = has_challenge_signal or bool(notes)
            # Accumulate scene-signal counts
            for total_var, key in (
                (ext_scene_total, "extSceneCount"),
                (int_scene_total, "intSceneCount"),
                (night_scene_total, "nightSceneCount"),
                (water_scene_total, "waterSceneCount"),
                (vfx_heavy_scene_total, "vfxHeavySceneCount"),
            ):
                val = challenges.get(key)
                if isinstance(val, int) and val > 0:
                    if key == "extSceneCount":
                        ext_scene_total += val
                    elif key == "intSceneCount":
                        int_scene_total += val
                    elif key == "nightSceneCount":
                        night_scene_total += val
                    elif key == "waterSceneCount":
                        water_scene_total += val
                    elif key == "vfxHeavySceneCount":
                        vfx_heavy_scene_total += val
            # v3 field accumulation
            day_val = challenges.get("daySceneCount")
            if isinstance(day_val, int) and day_val > 0:
                day_scene_total += day_val
            music_val = challenges.get("musicPerformanceScenes")
            if isinstance(music_val, int) and music_val > 0:
                music_performance_total += music_val
            stunt_val = challenges.get("stuntSequences")
            if isinstance(stunt_val, int) and stunt_val > 0:
                stunt_sequences_total += stunt_val
            crowd_val = challenges.get("crowdScenes")
            if isinstance(crowd_val, int) and crowd_val > 0:
                crowd_scenes_total += crowd_val
            if bool(challenges.get("voiceOvers", False)):
                voice_overs_seen = True
            chunk_langs = challenges.get("languages")
            if isinstance(chunk_langs, list):
                all_languages.extend([str(lang).strip() for lang in chunk_langs if str(lang).strip()])
            chunk_named_locs = challenges.get("namedLocations")
            if isinstance(chunk_named_locs, list):
                for entry in chunk_named_locs:
                    if isinstance(entry, dict):
                        loc_name = entry.get("name", "")
                        count = entry.get("count", 0)
                        if loc_name and isinstance(count, int) and count > 0:
                            named_locations_agg[str(loc_name)] = named_locations_agg.get(str(loc_name), 0) + count
            elif isinstance(chunk_named_locs, dict):
                for loc_name, count in chunk_named_locs.items():
                    if isinstance(count, int) and count > 0:
                        named_locations_agg[str(loc_name)] = named_locations_agg.get(str(loc_name), 0) + count
            ct = challenges.get("conflictType")
            if isinstance(ct, str) and ct.strip():
                conflict_type_values.append(ct.strip())
            irr = challenges.get("irresolution")
            if isinstance(irr, bool):
                irresolution_values.append(irr)

            if has_challenge_signal:
                section_signal_counts["challenges"] += 1

        location_rows = sorted(location_map.values(), key=lambda row: row["frequency"], reverse=True)
        if not location_rows:
            location_rows = [
                {
                    "name": "Los Angeles",
                    "country": "United States",
                    "territory": "California (USA)",
                    "frequency": 1,
                    "isMainLocation": True,
                }
            ]

        budget_range = self._choose_mode(budget_ranges, default="medium")
        budget_min, budget_max = BUDGET_BOUNDS_USD.get(budget_range, BUDGET_BOUNDS_USD["medium"])
        for key, true_count in challenge_true_counts.items():
            challenge_flags[key] = true_count >= max(1, int(round(used_chunks * 0.2)))

        section_confidence = {
            section: self._compute_section_confidence(
                signal_chunks=count,
                used_chunks=used_chunks,
                total_chunks=total_chunks,
            )
            for section, count in section_signal_counts.items()
        }
        overall_confidence = round(
            sum(section_confidence.values()) / max(len(section_confidence), 1),
            2,
        )

        # Shoot days: trimmed mean with scene-count sanity floor.
        # Each chunk estimates days for its own slice; the mean can collapse to 2–5 for
        # large scripts. Floor = total_scenes / 10 (industry standard: ~10 scenes/day).
        raw_shoot_days = self._trimmed_mean_int(shoot_days_values, default=30)
        total_scenes = ext_scene_total + int_scene_total
        if total_scenes > 0:
            scene_floor = max(1, total_scenes // 10)
            raw_shoot_days = max(raw_shoot_days, scene_floor)

        production_scale = {
            "crewSize": self._choose_weighted_mode(crew_values, SCALE_ORDER, "medium"),
            "principalCast": self._choose_weighted_mode(principal_values, SCALE_ORDER, "medium"),
            "supportingCast": self._choose_weighted_mode(supporting_values, SCALE_ORDER, "medium"),
            "backgroundExtras": self._choose_weighted_mode(extras_values, SCALE_ORDER, "medium"),
            "estimatedShootingDays": raw_shoot_days,
        }

        camera = self._choose_mode([c for c in camera_values if c in CAMERA_OPTIONS], default="arri")
        vfx = self._choose_weighted_mode(vfx_values, VFX_ORDER, "moderate")
        genres = self._unique_non_empty(genre_values)[:6] or ["Drama"]
        metadata_format = self._choose_mode([f for f in format_values if f in FORMAT_OPTIONS], default="feature")
        evidence_notes = self._build_aggregation_evidence(
            location_rows=location_rows,
            budget_range=budget_range,
            section_signal_counts=section_signal_counts,
            used_chunks=used_chunks,
            challenge_true_counts=challenge_true_counts,
        )

        payload = {
            "locations": location_rows[:20],
            "budgetEstimate": {
                "range": budget_range,
                "minUSD": budget_min,
                "maxUSD": budget_max,
                "confidence": section_confidence["budget"],
                "indicators": self._unique_non_empty(budget_indicators)[:8] or ["Chunked script signal synthesis"],
            },
            "productionScale": production_scale,
            "equipment": {
                "cameraEquipment": camera,
                "specialEquipment": self._unique_non_empty(special_equipment_values)[:12],
                "vfxRequirements": vfx,
            },
            "metadata": {
                "genres": genres,
                "format": metadata_format,
                "tone": self._choose_mode(tone_values, default="Unknown"),
                "targetAudience": self._choose_mode(audience_values, default="General audiences"),
            },
            "challenges": {
                **challenge_flags,
                "notes": self._unique_non_empty(challenge_notes)[:12],
                "extIntRatio": (
                    round(ext_scene_total / (ext_scene_total + int_scene_total), 3)
                    if (ext_scene_total + int_scene_total) > 0
                    else None
                ),
                "nightSceneCount": night_scene_total if night_scene_total > 0 else None,
                "waterSceneCount": water_scene_total if water_scene_total > 0 else None,
                "vfxHeavySceneCount": vfx_heavy_scene_total if vfx_heavy_scene_total > 0 else None,
                # v3 structured extraction fields
                "total_scenes": (ext_scene_total + int_scene_total) if (ext_scene_total + int_scene_total) > 0 else None,
                "interior_scenes": int_scene_total if int_scene_total > 0 else None,
                "exterior_scenes": ext_scene_total if ext_scene_total > 0 else None,
                "interior_pct": (
                    round(int_scene_total / (ext_scene_total + int_scene_total) * 100, 1)
                    if (ext_scene_total + int_scene_total) > 0 else None
                ),
                "exterior_pct": (
                    round(ext_scene_total / (ext_scene_total + int_scene_total) * 100, 1)
                    if (ext_scene_total + int_scene_total) > 0 else None
                ),
                "day_scenes": day_scene_total if day_scene_total > 0 else None,
                "night_scenes": night_scene_total if night_scene_total > 0 else None,
                "languages": list(dict.fromkeys(all_languages)) if all_languages else None,
                "voice_overs": voice_overs_seen if voice_overs_seen else None,
                "named_locations": named_locations_agg if named_locations_agg else None,
                "primary_location": (
                    max(named_locations_agg, key=named_locations_agg.get) if named_locations_agg else None  # type: ignore[arg-type]
                ),
                "music_performance_scenes": music_performance_total if music_performance_total > 0 else None,
                "conflict_type": self._choose_mode(conflict_type_values, default="") or None,
                "irresolution": any(irresolution_values) if irresolution_values else None,
                "stunt_sequences": stunt_sequences_total if stunt_sequences_total > 0 else None,
                "crowd_scenes": crowd_scenes_total if crowd_scenes_total > 0 else None,
            },
            "rawResponse": json.dumps(
                {
                    "mode": "chunked",
                    "scriptTitle": script_title,
                    "chunkTelemetry": {
                        "totalChunks": total_chunks,
                        "generatedChunks": generated_chunks,
                        "usedChunks": used_chunks,
                        "failedChunks": failed_chunks,
                        "droppedChunks": dropped_chunks,
                        "successRatio": round(used_chunks / max(total_chunks, 1), 3),
                        "stopReasons": stop_reasons,
                        "failedChunkDetails": failed_chunk_details[:20],
                    },
                    "sectionConfidence": section_confidence,
                    "overallConfidence": overall_confidence,
                    "aggregationEvidence": evidence_notes,
                },
                separators=(",", ":"),
            ),
        }
        return self._sanitize(payload)

    @staticmethod
    def _choose_mode(values: list[str], default: str) -> str:
        cleaned = [value for value in values if value]
        if not cleaned:
            return default
        return Counter(cleaned).most_common(1)[0][0]

    @staticmethod
    def _choose_max_scale(values: list[str], order: dict[str, int], default: str) -> str:
        filtered = [value for value in values if value in order]
        if not filtered:
            return default
        return max(filtered, key=lambda value: order[value])

    @staticmethod
    def _choose_weighted_mode(values: list[str], order: dict[str, int], default: str) -> str:
        filtered = [value for value in values if value in order]
        if not filtered:
            return default
        counts = Counter(filtered)
        return max(
            counts.keys(),
            key=lambda value: (counts[value], order.get(value, 0)),
        )

    @staticmethod
    def _trimmed_mean_int(values: list[int], default: int) -> int:
        if not values:
            return default
        ordered = sorted(v for v in values if isinstance(v, int) and v > 0)
        if not ordered:
            return default
        if len(ordered) > 4:
            ordered = ordered[1:-1]
        return max(1, int(round(sum(ordered) / len(ordered))))

    @staticmethod
    def _compute_section_confidence(*, signal_chunks: int, used_chunks: int, total_chunks: int) -> float:
        if used_chunks <= 0 or total_chunks <= 0:
            return 0.3
        coverage_ratio = signal_chunks / used_chunks
        success_ratio = used_chunks / total_chunks
        confidence = (0.15 + (0.55 * coverage_ratio) + (0.30 * success_ratio)) * (
            0.85 + (0.15 * success_ratio)
        )
        return round(max(0.25, min(0.98, confidence)), 2)

    @staticmethod
    def _build_aggregation_evidence(
        *,
        location_rows: list[dict[str, Any]],
        budget_range: str,
        section_signal_counts: dict[str, int],
        used_chunks: int,
        challenge_true_counts: dict[str, int],
    ) -> list[str]:
        notes: list[str] = []
        if location_rows:
            top = location_rows[:3]
            location_note = ", ".join([f"{loc['territory']} ({loc['frequency']})" for loc in top])
            notes.append(f"Top location frequency signals: {location_note}")
        notes.append(
            "Section coverage: "
            + ", ".join(
                [
                    f"{section} {count}/{max(used_chunks, 1)}"
                    for section, count in section_signal_counts.items()
                ]
            )
        )
        notes.append(f"Budget consensus resolved to '{budget_range}' from chunk-level signals")
        active_challenges = [name for name, count in challenge_true_counts.items() if count > 0]
        if active_challenges:
            notes.append(f"Challenge signals seen: {', '.join(active_challenges)}")
        return notes[:10]

    @staticmethod
    def _unique_non_empty(values: list[str]) -> list[str]:
        seen = set()
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(normalized)
        return result

    def _trim_value_for_prompt(self, value: Any) -> Any:
        if isinstance(value, str):
            if len(value) <= MAX_PROMPT_TEXT_CHARS:
                return value
            return value[:MAX_PROMPT_TEXT_CHARS] + "..."
        if isinstance(value, list):
            return [self._trim_value_for_prompt(item) for item in value[:MAX_PROMPT_LIST_ITEMS]]
        if isinstance(value, dict):
            result = {}
            for idx, (k, v) in enumerate(value.items()):
                if idx >= 30:
                    break
                result[k] = self._trim_value_for_prompt(v)
            return result
        return value

    def _call_anthropic_with_retry(
        self,
        *,
        system_prompt: str,
        user_content: str,
        temperature: float,
        stage: str,
        output_config: dict[str, Any] | None = None,
    ):
        retry_delays = [8, 20]
        max_attempts = len(retry_delays) + 1
        stage_max_tokens = self._stage_max_tokens(stage)
        stage_timeout = self._stage_timeout(stage)
        for attempt in range(1, max_attempts + 1):
            try:
                client = self._build_client(stage_timeout)
                api_key_preview = (self.settings.ANTHROPIC_API_KEY or "")[:12] + "..."
                logger.info(
                    "Anthropic request: stage=%s attempt=%s/%s model=%s "
                    "max_tokens=%s timeout=%s base_url=%s api_key=%s "
                    "has_output_config=%s prompt_chars=%s",
                    stage, attempt, max_attempts, self.settings.ANTHROPIC_MODEL,
                    stage_max_tokens, stage_timeout,
                    getattr(client, "_base_url", getattr(client, "base_url", "unknown")),
                    api_key_preview, bool(output_config), len(user_content),
                )
                request_payload: dict[str, Any] = {
                    "model": self.settings.ANTHROPIC_MODEL,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_content}],
                    "temperature": temperature,
                    "max_tokens": stage_max_tokens,
                }
                if output_config:
                    request_payload["output_config"] = output_config
                import time as _time
                _t0 = _time.monotonic()
                logger.info("Anthropic API call starting: stage=%s attempt=%s", stage, attempt)
                result = client.messages.create(
                    **request_payload,
                )
                _elapsed = _time.monotonic() - _t0
                logger.info(
                    "Anthropic API call completed: stage=%s attempt=%s elapsed=%.1fs stop_reason=%s",
                    stage, attempt, _elapsed, getattr(result, "stop_reason", None),
                )
                return result
            except Exception as exc:
                logger.error(
                    "Anthropic request failed: stage=%s attempt=%s/%s "
                    "exc_type=%s error=%s",
                    stage, attempt, max_attempts,
                    type(exc).__name__, exc,
                )
                is_retryable = (
                    self._is_rate_limit_error(exc)
                    or self._is_timeout_error(exc)
                    or self._is_connection_error(exc)
                )
                if not is_retryable or attempt >= max_attempts:
                    raise
                delay = retry_delays[attempt - 1]
                if self._is_timeout_error(exc):
                    error_kind = "timeout"
                elif self._is_connection_error(exc):
                    error_kind = "connection_error"
                else:
                    error_kind = "rate_limit"
                logger.warning(
                    "Anthropic %s at stage=%s attempt=%s/%s, retrying in %ss (max_tokens=%s timeout_s=%s)",
                    error_kind,
                    stage,
                    attempt,
                    max_attempts,
                    delay,
                    stage_max_tokens,
                    stage_timeout,
                )
                sleep(delay)

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "rate_limit_error" in message
            or "rate limit" in message
            or "429" in message
            or "529" in message
            or "overloaded_error" in message
            or "overloaded" in message
            or "input tokens per minute" in message
        )

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "timeout" in message or "timed out" in message

    @staticmethod
    def _is_connection_error(exc: Exception) -> bool:
        from anthropic import APIConnectionError
        if isinstance(exc, APIConnectionError):
            return True
        message = str(exc).lower()
        return (
            "connection error" in message
            or "broken pipe" in message
            or "connectionerror" in message
            or "apiconnectionerror" in message
        )

    @staticmethod
    def _recover_truncated_json(raw: str) -> dict[str, Any] | None:
        """Attempt to recover a valid JSON object from truncated LLM output.

        When stop_reason is max_tokens, the JSON is valid up to the truncation
        point. We close all open brackets/braces and try to parse.
        """
        text = raw.strip()
        # Strip markdown fences
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)

        # Find the start of the JSON object
        start = text.find("{")
        if start == -1:
            return None
        text = text[start:]

        # Remove any trailing incomplete string value (e.g. truncated mid-string)
        # Find the last complete line that ends with a JSON-valid token
        lines = text.rstrip().split("\n")
        while lines:
            last = lines[-1].rstrip()
            # If line ends with a valid JSON boundary, keep it
            if last and last[-1] in '",}]0123456789':
                break
            # If line ends with true/false/null
            if last.rstrip().endswith(("true", "false", "null")):
                break
            lines.pop()

        if not lines:
            return None

        text = "\n".join(lines)

        # Remove trailing comma
        text = text.rstrip().rstrip(",")

        # Count open brackets and close them
        open_braces = 0
        open_brackets = 0
        in_string = False
        escape = False
        for ch in text:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                open_braces += 1
            elif ch == "}":
                open_braces -= 1
            elif ch == "[":
                open_brackets += 1
            elif ch == "]":
                open_brackets -= 1

        # If we're inside an unterminated string, close it
        if in_string:
            text += '"'

        # Close open brackets/braces
        text += "]" * max(0, open_brackets)
        text += "}" * max(0, open_braces)

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                logger.info("Truncated JSON recovery: parsed successfully with %d top-level keys", len(data))
                return data
        except (json.JSONDecodeError, ValueError):
            # Try with repair
            repaired = ScriptAnalysisService._repair_json(text)
            try:
                data = json.loads(repaired)
                if isinstance(data, dict):
                    logger.info("Truncated JSON recovery: parsed after repair with %d top-level keys", len(data))
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    @staticmethod
    def _repair_json(text: str) -> str:
        """Best-effort repair of common LLM JSON mistakes."""
        # Remove trailing commas before } or ]
        text = re.sub(r",\s*([}\]])", r"\1", text)
        # Insert missing commas between a value and the next key:
        #   "value"\n  "nextKey"  or  "value"  "nextKey"
        #   number/bool/null\n  "nextKey"
        text = re.sub(
            r'(?<=["\d\w])\s*\n(\s*")', r',\n\1', text
        )
        # Fix: true/false/null followed by "key" on next line (the lookbehind above
        # catches most, but be explicit for end-of-word boundaries)
        text = re.sub(
            r'(true|false|null)\s*\n(\s*")', r'\1,\n\2', text
        )
        # Fix missing comma after ] or } followed by "key"
        text = re.sub(r'([}\]])\s*\n(\s*")', r'\1,\n\2', text)
        return text

    @staticmethod
    def _parse_json_payload(raw: str) -> dict[str, Any]:
        payload = raw.strip()

        # Handle fenced JSON responses: ```json ... ```
        if payload.startswith("```"):
            payload = re.sub(r"^```(?:json)?\s*", "", payload, flags=re.IGNORECASE)
            payload = re.sub(r"\s*```$", "", payload)
            payload = payload.strip()

        def _try_load(text: str) -> dict[str, Any] | None:
            try:
                data = json.loads(text)
                return data if isinstance(data, dict) else None
            except (json.JSONDecodeError, ValueError):
                return None

        # 1. Try raw payload as-is
        result = _try_load(payload)
        if result is not None:
            return result

        # 2. Extract first JSON object block from mixed text
        match = re.search(r"\{[\s\S]*\}", payload)
        if match:
            result = _try_load(match.group(0))
            if result is not None:
                return result

            # 3. Try repairing extracted JSON
            repaired = ScriptAnalysisService._repair_json(match.group(0))
            result = _try_load(repaired)
            if result is not None:
                logger.warning("JSON required repair to parse successfully")
                return result

        preview = payload[:220].replace("\n", " ")
        raise ValueError(f"No valid JSON object found in model response: {preview}")

    def _extract_text_response(self, response) -> str:
        """Extract plain text from Anthropic messages API response blocks."""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        logger.error(
            "Anthropic response had no text block: block_types=%s",
            [getattr(block, "type", None) for block in getattr(response, "content", [])],
        )
        raise ValueError("Anthropic returned no text content")

    @staticmethod
    def _default_scoring_methodology() -> dict:
        """Return a static scoring-methodology block that explains how scores work."""
        return {
            "overview": (
                "Each territory is scored out of 100 based on six weighted "
                "dimensions. The overall score is a weighted average that "
                "reflects the production's stated priorities."
            ),
            "dimensions": [
                {
                    "name": "Cost Efficiency",
                    "key": "costEfficiency",
                    "description": (
                        "Measures how far your budget stretches in this territory "
                        "— crew day-rates, stage hire, equipment rental, and "
                        "general cost of living relative to comparable markets."
                    ),
                },
                {
                    "name": "Crew Depth",
                    "key": "crewDepth",
                    "description": (
                        "Availability of experienced, English-speaking crew across "
                        "all key departments — camera, grip, electric, art, VFX, "
                        "and post-production."
                    ),
                },
                {
                    "name": "Infrastructure",
                    "key": "infrastructure",
                    "description": (
                        "Quality and capacity of studio stages, post-production "
                        "facilities, equipment houses, and supporting logistics "
                        "such as transport and accommodation."
                    ),
                },
                {
                    "name": "Incentive Strength",
                    "key": "incentiveStrength",
                    "description": (
                        "Value of available tax credits, rebates, and grants — "
                        "factoring in the rebate percentage, spend caps, "
                        "qualification complexity, and typical payment timelines."
                    ),
                },
                {
                    "name": "Currency Advantage",
                    "key": "currencyAdvantage",
                    "description": (
                        "Purchasing power of the production's budget currency "
                        "versus the territory's local currency. Computed from "
                        "live exchange rates at report generation time."
                    ),
                },
                {
                    "name": "Incentive Reliability",
                    "key": "incentiveReliability",
                    "description": (
                        "How bankable is the incentive? Based on payment reliability "
                        "score, payment timeline, and programme stability. A high "
                        "rate with low reliability should not be included in "
                        "investor projections without verification."
                    ),
                },
            ],
            "weightingNote": (
                "Dimension weights are adjusted to match the production priority "
                "you selected. 'Incentive-first' emphasises incentive strength "
                "(40%). 'Location-first' emphasises crew depth and infrastructure "
                "(25% each). Currency advantage is always 10%. Incentive "
                "reliability is 5–10%."
            ),
            "colorKey": {
                "green": "Score ≥ 70 — strong fit",
                "gold": "Score 40–69 — moderate fit, review trade-offs",
                "red": "Score ≤ 39 — potential challenges, proceed with caution",
            },
        }

    # ── Builder-path: narrative-only AI call + merge ─────────────────────────

    _NARRATIVE_FILL_PROMPT = """You are a senior production consultant.
You receive a pre-built production analysis report with all financial data,
scores, and structured fields already computed from verified databases.

Your task: fill in the qualitative/narrative fields ONLY.

Return a JSON object with ONLY these keys:
{
  "genre": "primary genre",
  "tone": "narrative tone description",
  "scale": "production scale label (e.g. 'Mid-budget Feature Film')",
  "complexity": "Low|Medium|High|Very High",
  "executiveSummary_keyInsights": "FOLLOW THE EXACT FORMAT IN executiveSummary_keyInsights RULES BELOW",
  "alternativeStrategy": "1-2 sentence alternative territory recommendation",
  "nextSteps": [
    {
      "priority": "URGENT|HIGH|RECOMMENDED",
      "action": "one-sentence specific action",
      "reason": "one-sentence reason referencing specific flag in this report",
      "deadline": "date or timeframe if known, else null"
    }
  ],
  "locationNarratives": {
    "Territory Name": {
      "reasoning": ["bullet 1", "bullet 2", "bullet 3"],
      "keyAdvantages": ["advantage 1", "advantage 2", "advantage 3"],
      "keyRisks_additional": ["risk beyond the DB-computed ones"]
    }
  },
  "crewNarratives": {
    "Territory Name": {
      "availability": "High|Medium|Low",
      "specialties": ["specialty 1", "specialty 2"],
      "tradeoff": "one-sentence crew trade-off summary"
    }
  },
  "comparableDescriptions": {
    "Film Title": "one-sentence relevance description"
  },
  "weatherNarratives": {
    "Territory Name": {
      "infrastructure": "one-sentence infrastructure note",
      "seasonalConsiderations": "one-sentence seasonal note"
    }
  },
  "deepDiveNarratives": {
    "Territory Name": {
      "infrastructure": "2-3 sentence infrastructure description",
      "keyAdvantages": ["advantage 1", "advantage 2"],
      "keyRisks_additional": ["risk beyond the DB-computed ones"]
    }
  }
}

RULES:
- Return ONLY valid JSON — no markdown, no explanation, no ```json fences
- Reference specific data from the skeleton (rates, rebate amounts, programme names)
- If script analysis is provided, reference script details in reasoning
- Do NOT invent financial figures — all monetary data is in the skeleton
- perPersonCapNote: reference per-person caps ONLY when a territory's perPersonCapNote is non-null in the skeleton. If null, do NOT mention per-person caps, wage caps, or ATL fee thresholds for that territory — the programme has none.
- netRatePct: when present in the skeleton, always use this as the investor-facing rate (after local tax), not the gross rate. State it as "net X%" to distinguish from the gross credit.
- payeeNote: when present, disclose who receives the rebate payment in the territory's reasoning section.
- filingNote: when present, include the filing/entity clarification in the territory's requirements.
- When referencing stacking programmes (provincial/regional credits), use ONLY the rate values shown in the skeleton data for that territory. Do NOT cite rates from your own training knowledge.
- Write like a senior consultant — authoritative, data-driven, actionable
- keyRisks_additional: ONLY risks NOT already in the skeleton's keyRisks
- Keep string values concise — short phrases, not paragraphs
- costEfficiency, crewDepth, infrastructure: Pre-computed from verified DB data. DO NOT include numeric values for these in locationNarratives. In reasoning bullets, explain what these scores mean for THIS production.

executiveSummary_keyInsights RULES — Write exactly six paragraphs with bold headings. Blank line between each. Total 350-420 words.
PARAGRAPH 1 — **Production Overview** (80-100 words): Name protagonist + world + specific desire referencing a named location. Core conflict and tone with specific scene type or cultural detail from script. Bridge to primary production challenge. Do NOT list budget, format, or genre.
PARAGRAPH 2 — **Primary Recommendation** (70-90 words): Territory name + FRS from financialReturnScore (e.g. "FRS: 84 — Bankable"). Estimated net rebate — net rate only, NOT gross. Payment timeline in plain English. One sentence why this territory wins for THIS production referencing a specific script element.
PARAGRAPH 3 — **Second Territory** (50-70 words): Territory + FRS + verdict. Key financial figure. One sentence: what producer gains vs primary recommendation and what they give up.
PARAGRAPH 4 — **Third Territory** (50-70 words): Territory + FRS + verdict. Key financial figure. One sentence: when does this become the right choice? OMIT if fewer than 3 territories.
PARAGRAPH 5 — **Production Complexity Snapshot** (40-60 words): 2-3 specific complexity flags from script analysis. Every point traceable to script — scene counts, languages, specialist requirements. No generic statements.
PARAGRAPH 6 — **Strategic Recommendations** (40-60 words): 2-3 specific time-sensitive actions. At least one must be urgent/deadline-bound from actual report flags. Reference actual programme names, deadlines, or risks from this report.
GUARDRAILS: Duration always in weeks — NEVER convert to days. NET rates only. UK AVEC always 25.5% net NOT 34% gross. FRS: use financialReturnScore and financialReturnVerdict from skeleton only. Financial figures: only values from skeleton. Bold headings on own line NOT inline.

nextSteps RULES: 4-6 items ordered by urgency (URGENT first). Each action must reference a specific territory, programme name, or flag. URGENT = deadline within 3 months or blocking contractual commitment. NEVER generate generic actions.

comparableDescriptions RULES: ONE specific reason this comparable is relevant. Reference at least one of: incentive programme, budget match, crew parallel, genre match, structural similarity. Do NOT use phrase "comparable production". Do NOT describe plot. Maximum 40 words. One sentence.
"""

    def generate_production_analysis_v2(
        self,
        *,
        skeleton: dict,
        script_analysis: ScriptAnalysisResult | None,
        request_metadata: dict,
        datasets: dict,
        is_preview: bool,
    ) -> dict:
        """Builder-path: fill narrative fields on a pre-built skeleton via AI.

        1. Calls the AI with the skeleton + script analysis for narrative-only output
        2. Merges AI narratives into the skeleton
        3. Computes overall scores
        4. Returns the complete report
        """
        from app.modules.reports.builder import ReportBuilder

        if not self.settings.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key is not configured")

        started = perf_counter()

        # Build user message with skeleton + context
        parts = ["=== REPORT SKELETON (all financial data is authoritative — do not change) ==="]
        parts.append(json.dumps(self._trim_value_for_prompt(skeleton), default=str, separators=(",", ":")))

        if script_analysis:
            parts.append("\n=== SCRIPT ANALYSIS ===")
            script_payload = self._trim_value_for_prompt(
                script_analysis.model_dump(exclude={"rawResponse"})
            )
            parts.append(json.dumps(script_payload, default=str, separators=(",", ":")))
        else:
            parts.append("\n=== SCRIPT ANALYSIS ===")
            parts.append("No script provided. Generate narratives from project metadata only.")

        parts.append("\n=== PROJECT METADATA ===")
        parts.append(json.dumps(self._trim_value_for_prompt(request_metadata), default=str, separators=(",", ":")))

        user_message = "\n".join(parts)

        logger.info(
            "Narrative fill prompt prepared: preview=%s prompt_chars=%s "
            "skeleton_territories=%s model=%s",
            is_preview,
            len(user_message),
            len(skeleton.get("locationRankings", [])),
            self.settings.ANTHROPIC_MODEL,
        )

        try:
            response = self._call_anthropic_with_retry(
                system_prompt=self._NARRATIVE_FILL_PROMPT,
                user_content=user_message,
                temperature=0.2,
                stage=self._STAGE_PRODUCTION_ANALYSIS,
            )
        except Exception as api_err:
            logger.exception(
                "Narrative fill API call failed: preview=%s elapsed_ms=%s",
                is_preview,
                int((perf_counter() - started) * 1000),
            )
            # Fall back: return skeleton with safe defaults for narrative fields
            self._fill_narrative_defaults(skeleton)
            production_priority = datasets.get("_production_priority", "full")
            ReportBuilder.compute_overall_scores(skeleton, production_priority)
            return skeleton

        raw = self._extract_text_response(response)
        try:
            ai_narratives = self._parse_json_payload(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "Failed to parse narrative fill JSON, using defaults: raw_chars=%s",
                len(raw),
            )
            self._fill_narrative_defaults(skeleton)
            production_priority = datasets.get("_production_priority", "full")
            ReportBuilder.compute_overall_scores(skeleton, production_priority)
            return skeleton

        # Merge AI narratives into skeleton
        report = self._merge_ai_narratives(skeleton, ai_narratives)

        # Compute overall scores now that AI has filled 3 qualitative dimensions
        production_priority = datasets.get("_production_priority", "full")
        ReportBuilder.compute_overall_scores(report, production_priority)

        # Post-AI financial audit: flag any rate% in narratives that deviate from DB
        audit_warnings = self._audit_financial_claims(report)
        if audit_warnings:
            report["auditWarnings"] = audit_warnings
            logger.warning(
                "Financial audit flagged %s discrepancies: preview=%s warnings=%s",
                len(audit_warnings), is_preview, audit_warnings,
            )

        # Apply production format harmonisation
        production_format = datasets.get("_production_format")
        if production_format:
            ReportValidator._patch_production_format(report, production_format, [])

        logger.info(
            "Builder-path analysis completed: preview=%s location_rankings=%s "
            "elapsed_ms=%s",
            is_preview,
            len(report.get("locationRankings", [])),
            int((perf_counter() - started) * 1000),
        )
        return report

    @staticmethod
    def _audit_financial_claims(report: dict) -> list[str]:
        """Scan AI narrative text for rate% that deviate >5pp from DB-computed rates.

        Only checks patterns like "X% rebate / credit / offset / return / incentive"
        to avoid false positives (e.g. "40% of budget qualifies").
        Flags citations that are higher than the DB gross rate by more than 5pp.
        """
        import re as _re

        _RATE_PATTERN = _re.compile(
            r"(\d+(?:\.\d+)?)\s*%\s*(?:rebate|credit|offset|return|incentive)",
            _re.IGNORECASE,
        )

        # Build territory → DB gross rate map from skeleton
        rate_by_territory: dict[str, float] = {}
        for loc in report.get("locationRankings", []):
            if not isinstance(loc, dict):
                continue
            territory = loc.get("name", "")
            rebate_pct = str(loc.get("rebatePercent") or "")
            m = _re.search(r"(\d+(?:\.\d+)?)", rebate_pct)
            if m:
                rate_by_territory[territory] = float(m.group(1))

        warnings: list[str] = []

        def _scan_text(territory: str, text: str, db_rate: float) -> None:
            for m in _RATE_PATTERN.finditer(text):
                cited = float(m.group(1))
                if cited > db_rate + 5:
                    warnings.append(
                        f"{territory}: narrative cites {cited:.0f}% but DB rate is {db_rate:.0f}%"
                    )

        for loc in report.get("locationRankings", []):
            if not isinstance(loc, dict):
                continue
            territory = loc.get("name", "")
            db_rate = rate_by_territory.get(territory)
            if db_rate is None:
                continue
            for field in ("reasoning", "keyAdvantages"):
                val = loc.get(field)
                text = " ".join(val) if isinstance(val, list) else str(val or "")
                _scan_text(territory, text, db_rate)

        for dive in report.get("territoryDeepDives", []):
            if not isinstance(dive, dict):
                continue
            territory = dive.get("name", "")
            db_rate = rate_by_territory.get(territory)
            if db_rate is None:
                continue
            for field in ("infrastructure", "keyAdvantages", "keyRisks"):
                val = dive.get(field)
                text = " ".join(val) if isinstance(val, list) else str(val or "")
                _scan_text(territory, text, db_rate)

        return warnings

    @staticmethod
    def _merge_ai_narratives(skeleton: dict, ai: dict) -> dict:
        """Merge AI-generated narrative fields into the pre-built skeleton.

        DB-authoritative fields (scores, financial data, keyRisks from DB)
        are never overwritten.  AI risks are appended, not replaced.
        """
        # Top-level narrative fields
        skeleton["genre"] = ai.get("genre") or skeleton.get("genre") or "Drama"
        skeleton["tone"] = ai.get("tone") or skeleton.get("tone") or "Unknown"
        skeleton["scale"] = ai.get("scale") or skeleton.get("scale") or "Unknown"

        complexity = ai.get("complexity", "Medium")
        if complexity not in ("Low", "Medium", "High", "Very High"):
            complexity = "Medium"
        skeleton["complexity"] = complexity

        skeleton["alternativeStrategy"] = (
            ai.get("alternativeStrategy")
            or skeleton.get("alternativeStrategy")
            or "Consider the top-ranked territory for optimal incentive value."
        )

        # Executive summary keyInsights
        summary = skeleton.get("executiveSummary")
        if isinstance(summary, dict):
            key_insights = ai.get("executiveSummary_keyInsights")
            if key_insights:
                summary["keyInsights"] = key_insights
            elif not summary.get("keyInsights"):
                summary["keyInsights"] = (
                    "Full narrative analysis unavailable. "
                    "Review the financial data and territory scores below."
                )

        # Location narratives
        location_narratives = ai.get("locationNarratives", {})
        rankings = skeleton.get("locationRankings", [])
        for loc in rankings:
            if not isinstance(loc, dict):
                continue
            territory = loc.get("name", "")
            narr = location_narratives.get(territory, {})

            # costEfficiency: AI may refine within ±15 of the DB anchor
            cost_anchor = loc.pop("_costEfficiencyAnchor", None)
            ai_cost = narr.get("costEfficiency")
            if isinstance(ai_cost, (int, float)):
                ai_cost_int = max(0, min(100, int(ai_cost)))
                if cost_anchor is not None:
                    ai_cost_int = max(cost_anchor - 15, min(cost_anchor + 15, ai_cost_int))
                loc["costEfficiency"] = ai_cost_int
            elif loc.get("costEfficiency") is None:
                loc["costEfficiency"] = cost_anchor if cost_anchor is not None else 50

            # crewDepth and infrastructure: pure AI estimates
            for dim in ("crewDepth", "infrastructure"):
                val = narr.get(dim)
                if isinstance(val, (int, float)):
                    loc[dim] = max(0, min(100, int(val)))
                elif loc.get(dim) is None:
                    loc[dim] = 50  # safe default

            # Reasoning and keyAdvantages
            if narr.get("reasoning") and isinstance(narr["reasoning"], list):
                loc["reasoning"] = narr["reasoning"]
            elif not loc.get("reasoning"):
                loc["reasoning"] = ["Territory included based on incentive data analysis"]

            if narr.get("keyAdvantages") and isinstance(narr["keyAdvantages"], list):
                loc["keyAdvantages"] = narr["keyAdvantages"]
            elif not loc.get("keyAdvantages"):
                loc["keyAdvantages"] = []

            # Append AI risks to DB risks (never overwrite DB risks)
            additional_risks = narr.get("keyRisks_additional", [])
            if isinstance(additional_risks, list):
                existing_risks = loc.get("keyRisks", [])
                for risk in additional_risks:
                    if isinstance(risk, str) and risk not in existing_risks:
                        existing_risks.append(risk)

        # Crew narratives
        crew_narratives = ai.get("crewNarratives", {})
        crew_insights = skeleton.get("crewInsights", [])
        for insight in crew_insights:
            if not isinstance(insight, dict):
                continue
            territory = insight.get("territory", "")
            narr = crew_narratives.get(territory, {})
            for field in ("availability", "specialties", "tradeoff"):
                val = narr.get(field)
                if val is not None:
                    insight[field] = val
                elif insight.get(field) is None:
                    if field == "availability":
                        insight[field] = "Medium"
                    elif field == "specialties":
                        insight[field] = []
                    elif field == "tradeoff":
                        insight[field] = "See crew cost comparison for details"

        # Comparable descriptions
        comparable_descs = ai.get("comparableDescriptions", {})
        comparables = skeleton.get("comparables", [])
        for comp in comparables:
            if not isinstance(comp, dict):
                continue
            title = comp.get("title", "")
            desc = comparable_descs.get(title)
            if desc:
                # Apply budget gap flag if present
                gap_flag = comp.pop("_budgetGapFlag", None)
                if gap_flag and "budget gap" not in desc.lower():
                    desc += f" [Note: budget gap — this comparable is {gap_flag} than the production being analysed]"
                comp["relevanceDescription"] = desc
            elif not comp.get("relevanceDescription"):
                gap_flag = comp.pop("_budgetGapFlag", None)
                comp["relevanceDescription"] = "Comparable production in a similar territory"
                if gap_flag:
                    comp["relevanceDescription"] += f" [Note: budget gap — this comparable is {gap_flag} than the production being analysed]"

        # Weather narratives
        weather_narratives = ai.get("weatherNarratives", {})
        weather_logistics = skeleton.get("weatherLogistics", [])
        for entry in weather_logistics:
            if not isinstance(entry, dict):
                continue
            territory = entry.get("territory", "")
            narr = weather_narratives.get(territory, {})
            for field in ("infrastructure", "seasonalConsiderations"):
                val = narr.get(field)
                if val is not None:
                    entry[field] = val
                elif entry.get(field) is None:
                    entry[field] = "See territory deep dive for details"

        # Deep dive narratives
        deep_dive_narratives = ai.get("deepDiveNarratives", {})
        dives = skeleton.get("territoryDeepDives", [])
        for dive in dives:
            if not isinstance(dive, dict):
                continue
            territory = dive.get("name", "")
            narr = deep_dive_narratives.get(territory, {})

            if narr.get("infrastructure"):
                dive["infrastructure"] = narr["infrastructure"]
            elif not dive.get("infrastructure"):
                dive["infrastructure"] = "See location ranking for details"

            if narr.get("keyAdvantages") and isinstance(narr["keyAdvantages"], list):
                dive["keyAdvantages"] = narr["keyAdvantages"]
            elif not dive.get("keyAdvantages"):
                dive["keyAdvantages"] = []

            additional_risks = narr.get("keyRisks_additional", [])
            if isinstance(additional_risks, list):
                dive["keyRisks"] = additional_risks
            elif not dive.get("keyRisks"):
                dive["keyRisks"] = []

        # Strip any _costEfficiencyAnchor fields that weren't consumed (fallback path)
        for loc in skeleton.get("locationRankings", []):
            if isinstance(loc, dict):
                loc.pop("_costEfficiencyAnchor", None)

        # Add scoring methodology
        skeleton["scoringMethodology"] = ScriptAnalysisService._default_scoring_methodology()

        return skeleton

    @staticmethod
    def _fill_narrative_defaults(skeleton: dict) -> None:
        """Fill safe defaults for all AI-narrative fields when AI call fails."""
        if not skeleton.get("genre"):
            skeleton["genre"] = "Drama"
        if not skeleton.get("tone"):
            skeleton["tone"] = "Unknown"
        if not skeleton.get("scale"):
            skeleton["scale"] = "Unknown"
        if not skeleton.get("complexity"):
            skeleton["complexity"] = "Medium"
        if not skeleton.get("alternativeStrategy"):
            skeleton["alternativeStrategy"] = "Review territory scores and financial data below."

        summary = skeleton.get("executiveSummary")
        if isinstance(summary, dict) and not summary.get("keyInsights"):
            summary["keyInsights"] = (
                "AI narrative generation unavailable. "
                "Financial data and territory scores are computed from verified databases."
            )

        for loc in skeleton.get("locationRankings", []):
            if not isinstance(loc, dict):
                continue
            loc.pop("_costEfficiencyAnchor", None)
            for dim in ("costEfficiency", "crewDepth", "infrastructure"):
                if loc.get(dim) is None:
                    loc[dim] = 50
            if not loc.get("reasoning"):
                loc["reasoning"] = ["Territory included based on incentive data analysis"]
            if not loc.get("keyAdvantages"):
                loc["keyAdvantages"] = []

        for insight in skeleton.get("crewInsights", []):
            if not isinstance(insight, dict):
                continue
            if not insight.get("availability"):
                insight["availability"] = "Medium"
            if insight.get("specialties") is None:
                insight["specialties"] = []
            if not insight.get("tradeoff"):
                insight["tradeoff"] = "See crew cost comparison"

        for comp in skeleton.get("comparables", []):
            if isinstance(comp, dict) and not comp.get("relevanceDescription"):
                comp.pop("_budgetGapFlag", None)
                comp["relevanceDescription"] = "Comparable production"

        for entry in skeleton.get("weatherLogistics", []):
            if isinstance(entry, dict):
                if not entry.get("infrastructure"):
                    entry["infrastructure"] = "See territory analysis"
                if not entry.get("seasonalConsiderations"):
                    entry["seasonalConsiderations"] = "Check local conditions"

        for dive in skeleton.get("territoryDeepDives", []):
            if isinstance(dive, dict):
                if not dive.get("infrastructure"):
                    dive["infrastructure"] = "See location ranking"
                if dive.get("keyAdvantages") is None:
                    dive["keyAdvantages"] = []
                if dive.get("keyRisks") is None:
                    dive["keyRisks"] = []

        skeleton["scoringMethodology"] = ScriptAnalysisService._default_scoring_methodology()

    def _fallback_analysis(self, request_metadata: dict, is_preview: bool) -> dict:
        """Return fallback analysis when production analysis API call fails."""
        genre = (request_metadata.get("genre") or ["Drama"])[0]
        country = request_metadata.get("country", "UK")
        budget_amount = request_metadata.get("budget_amount")
        budget_currency = request_metadata.get("budget_currency", "GBP")
        budget_display = f"{budget_currency} {budget_amount:,.0f}" if budget_amount else "Unknown"

        # Build location rankings from territories_considering if available
        territories = request_metadata.get("territories_considering") or []
        location_rankings = []
        if territories:
            for idx, territory in enumerate(territories[:5]):
                location_rankings.append({
                    "name": territory,
                    "country": territory,
                    "score": max(40, 70 - idx * 5),
                    "costEfficiency": 50,
                    "crewDepth": 50,
                    "infrastructure": 50,
                    "incentiveStrength": 50,
                    "currencyAdvantage": 50,
                    "incentiveReliability": 30,
                    "bankabilityLabel": None,
                    "reasoning": [
                        "Territory selected from user preferences",
                        "Full AI analysis was unavailable — scores are estimated defaults",
                    ],
                    "isAssessmentOnly": is_preview,
                })
        if not location_rankings:
            location_rankings.append({
                "name": country,
                "country": country,
                "score": 60,
                "costEfficiency": 50,
                "crewDepth": 60,
                "infrastructure": 60,
                "incentiveStrength": 50,
                "currencyAdvantage": 50,
                "incentiveReliability": 30,
                "bankabilityLabel": None,
                "reasoning": [
                    "Default territory based on project country",
                    "Full AI analysis was unavailable — scores are estimated defaults",
                ],
                "isAssessmentOnly": is_preview,
            })

        return {
            "_fallbackUsed": True,
            "genre": genre,
            "tone": "Pending analysis",
            "scale": f"{budget_display} production",
            "complexity": "Medium",
            "executiveSummary": {
                "keyInsights": (
                    f"AI analysis was temporarily unavailable for this {genre.lower()} "
                    f"{budget_display} production. The report below uses estimated defaults "
                    f"based on project metadata. We recommend regenerating this report for "
                    f"full territory analysis, financial breakdowns, and crew cost comparisons."
                ),
                "recommendedTerritory": location_rankings[0]["name"],
                "recommendedTerritoryScore": location_rankings[0]["score"],
                "recommendedTerritoryRebate": None,
                "recommendedTerritoryInfrastructure": None,
                "recommendedTerritoryPaymentSpeed": None,
                "shootDays": None,
                "budget": budget_display,
                "primaryLocations": [],
            },
            "locationRankings": location_rankings,
            "incentiveEstimates": [],
            "crewInsights": [],
            "comparables": [],
            "weatherLogistics": [],
            "fundingOpportunities": [],
            "scoringMethodology": self._default_scoring_methodology(),
        }

    def _sanitize(self, data: dict[str, Any]) -> ScriptAnalysisResult:
        """Validate and fill defaults for AI response."""
        locations_raw = data.get("locations", [])
        budget_raw = data.get("budgetEstimate", {})
        scale_raw = data.get("productionScale", {})
        equipment_raw = data.get("equipment", {})
        metadata_raw = data.get("metadata", {})
        challenges_raw = data.get("challenges", {})

        if not isinstance(locations_raw, list):
            locations_raw = []
        if not isinstance(budget_raw, dict):
            budget_raw = {}
        if not isinstance(scale_raw, dict):
            scale_raw = {}
        if not isinstance(equipment_raw, dict):
            equipment_raw = {}
        if not isinstance(metadata_raw, dict):
            metadata_raw = {}
        if not isinstance(challenges_raw, dict):
            challenges_raw = {}

        locations = [
            Location.model_validate(loc)
            for loc in locations_raw
            if isinstance(loc, dict)
        ]

        return ScriptAnalysisResult(
            locations=locations,
            budgetEstimate=BudgetEstimate(
                range=budget_raw.get("range", "medium"),
                minUSD=budget_raw.get("minUSD", 5_000_000),
                maxUSD=budget_raw.get("maxUSD", 30_000_000),
                confidence=budget_raw.get("confidence", 0.7),
                indicators=budget_raw.get("indicators", ["General industry estimates"]),
            ),
            productionScale=ProductionScale(
                crewSize=scale_raw.get("crewSize", "medium"),
                principalCast=scale_raw.get("principalCast", "medium"),
                supportingCast=scale_raw.get("supportingCast", "medium"),
                backgroundExtras=scale_raw.get("backgroundExtras", "medium"),
                estimatedShootingDays=scale_raw.get("estimatedShootingDays", 30),
            ),
            equipment=Equipment(
                cameraEquipment=equipment_raw.get("cameraEquipment", "arri"),
                specialEquipment=equipment_raw.get("specialEquipment", []),
                vfxRequirements=equipment_raw.get("vfxRequirements", "moderate"),
            ),
            metadata=Metadata(
                genres=metadata_raw.get("genres", ["Drama"]),
                format=metadata_raw.get("format", "feature"),
                tone=metadata_raw.get("tone", "Unknown"),
                targetAudience=metadata_raw.get("targetAudience", "General audiences"),
            ),
            challenges=Challenges(
                weatherDependent=challenges_raw.get("weatherDependent", False),
                historicalPeriod=challenges_raw.get("historicalPeriod", False),
                specialPermits=challenges_raw.get("specialPermits", False),
                stunts=challenges_raw.get("stunts", False),
                animalWrangling=challenges_raw.get("animalWrangling", False),
                waterWork=challenges_raw.get("waterWork", False),
                nightShooting=challenges_raw.get("nightShooting", False),
                notes=challenges_raw.get("notes", []),
                # v3 structured extraction fields (pass through if present)
                total_scenes=challenges_raw.get("total_scenes"),
                interior_scenes=challenges_raw.get("interior_scenes"),
                exterior_scenes=challenges_raw.get("exterior_scenes"),
                interior_pct=challenges_raw.get("interior_pct"),
                exterior_pct=challenges_raw.get("exterior_pct"),
                day_scenes=challenges_raw.get("day_scenes"),
                night_scenes=challenges_raw.get("night_scenes"),
                languages=challenges_raw.get("languages"),
                voice_overs=challenges_raw.get("voice_overs"),
                named_locations=challenges_raw.get("named_locations"),
                primary_location=challenges_raw.get("primary_location"),
                music_performance_scenes=challenges_raw.get("music_performance_scenes"),
                conflict_type=challenges_raw.get("conflict_type"),
                irresolution=challenges_raw.get("irresolution"),
                stunt_sequences=challenges_raw.get("stunt_sequences"),
                crowd_scenes=challenges_raw.get("crowd_scenes"),
            ),
            rawResponse=data.get("rawResponse"),
        )

    def _fallback(self, script_title: str, reason: str = "script_analysis_error") -> ScriptAnalysisResult:
        """Return fallback analysis when API fails."""
        return ScriptAnalysisResult(
            locations=[
                Location(
                    name="Los Angeles",
                    country="United States",
                    territory="California (USA)",
                    frequency=10,
                    isMainLocation=True,
                )
            ],
            budgetEstimate=BudgetEstimate(
                range="medium",
                minUSD=5_000_000,
                maxUSD=30_000_000,
                confidence=0.5,
                indicators=["Estimated based on typical feature film budget"],
            ),
            productionScale=ProductionScale(
                crewSize="medium",
                principalCast="medium",
                supportingCast="medium",
                backgroundExtras="medium",
                estimatedShootingDays=30,
            ),
            equipment=Equipment(
                cameraEquipment="arri",
                specialEquipment=[],
                vfxRequirements="moderate",
            ),
            metadata=Metadata(
                genres=["Drama"],
                format="feature",
                tone="Unknown",
                targetAudience="General audiences",
            ),
            challenges=Challenges(
                weatherDependent=False,
                historicalPeriod=False,
                specialPermits=False,
                stunts=False,
                animalWrangling=False,
                waterWork=False,
                nightShooting=False,
                notes=["Analysis failed - using default estimates"],
            ),
            rawResponse=json.dumps(
                {
                    "mode": "single_pass_fallback",
                    "fallbackUsed": True,
                    "reason": reason,
                    "scriptTitle": script_title,
                },
                separators=(",", ":"),
            ),
        )
