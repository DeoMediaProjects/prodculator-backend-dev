import json
import logging
import re
from time import perf_counter, sleep
from typing import Any

import pdfplumber
from anthropic import Anthropic

from app.core.config import Settings
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
MAX_SCRIPT_CHARS = 12_000
MAX_PROMPT_TEXT_CHARS = 240
MAX_PROMPT_LIST_ITEMS = 8

SCRIPT_ANALYSIS_PROMPT = """You are a professional film production analyst. Analyze the provided script and extract detailed production intelligence.

IMPORTANT INSTRUCTIONS:
1. Be specific and accurate in your analysis
2. Base all estimates on industry standards
3. Only extract information that is clearly evident from the script
4. Respond ONLY with valid JSON (no markdown, no explanation)

RESPONSE FORMAT (valid JSON only):
{
  "locations": [
    {
      "name": "Location name (city, region, or country)",
      "country": "Country name",
      "territory": "One of: United Kingdom, England, Scotland, Wales, Northern Ireland, British Columbia, Ontario, Quebec, Alberta, Manitoba, Nova Scotia, Georgia (USA), California (USA), New York (USA), Louisiana (USA), New Mexico (USA), North Carolina (USA), Illinois (USA), Massachusetts (USA), Malta, South Africa (National), Western Cape (SA), KwaZulu-Natal (SA), Gauteng (SA)",
      "frequency": number,
      "isMainLocation": true or false
    }
  ],
  "budgetEstimate": {
    "range": "micro|low|medium|high|tentpole",
    "minUSD": number,
    "maxUSD": number,
    "confidence": number (0-1),
    "indicators": ["reason 1", "reason 2"]
  },
  "productionScale": {
    "crewSize": "small|medium|large|extra_large",
    "principalCast": "small|medium|large|extra_large",
    "supportingCast": "small|medium|large|extra_large",
    "backgroundExtras": "small|medium|large|extra_large",
    "estimatedShootingDays": number
  },
  "equipment": {
    "cameraEquipment": "arri|red|sony|panavision|blackmagic|canon|other",
    "specialEquipment": ["item 1", "item 2"],
    "vfxRequirements": "minimal|moderate|heavy|intensive"
  },
  "metadata": {
    "genres": ["genre 1", "genre 2"],
    "format": "feature|tv_series|limited_series|documentary|short",
    "tone": "description",
    "targetAudience": "description"
  },
  "challenges": {
    "weatherDependent": true or false,
    "historicalPeriod": true or false,
    "specialPermits": true or false,
    "stunts": true or false,
    "animalWrangling": true or false,
    "waterWork": true or false,
    "nightShooting": true or false,
    "notes": ["challenge 1", "challenge 2"]
  }
}

BUDGET RANGES:
- micro: $50K - $500K
- low: $500K - $5M
- medium: $5M - $30M
- high: $30M - $100M
- tentpole: $100M+

SCALE DEFINITIONS:
small: 1-10, medium: 11-50, large: 51-150, extra_large: 150+

ANALYZE THE SCRIPT BELOW:"""


PRODUCTION_ANALYSIS_PROMPT = """You are an expert production intelligence analyst with access to verified industry datasets. Your task is to produce a comprehensive production analysis report as a single valid JSON object.

You will receive:
1. Script analysis data (locations, budget, production scale, challenges) — may be absent for preview reports
2. User-submitted project metadata (genre, budget range, format, country, priorities)
3. Reference datasets from the Prodculator admin database (incentive programs, crew costs, comparable productions, grants, festivals)

CRITICAL RULES:
- Return ONLY valid JSON matching the exact schema below — no markdown, no explanation
- Use actual data from the reference datasets. Cite real program names, real rates, real festival names
- Do NOT fabricate incentive programs, crew rates, or festival names — only use what is in the datasets
- If a dataset is empty for a territory, note limited data availability in reasoning
- Maintain consistent territory coverage: all sections must reference the same set of territories that appear in locationRankings
- Be specific, not generic. "Malta offers a 40% cash rebate under the MFTI programme" is useful. "Malta has good incentives" is not
- If script analysis data is provided, reference script details in reasoning (e.g. "The script's harbour sequences align with Malta's maritime infrastructure")

SCORING RULES for locationRankings:
- Each territory gets sub-scores (0-100) for: costEfficiency, crewDepth, infrastructure, incentiveStrength, currencyAdvantage
- The overall `score` (0-100) is a weighted average of sub-scores, based on the user's production_priority:
  - "incentive": incentiveStrength weight x2 (40%), other four share remaining 60% equally (15% each)
  - "location": crewDepth and infrastructure weight x1.5 each (25% each), other three share 50% (~17% each)
  - "full": all five sub-scores weighted equally (20% each)
- Use territories_considering to bias selection toward named territories; if empty or contains "Open to all", choose globally optimal set
- Use filming_start_date and filming_duration to affect seasonal scoring where relevant

BUDGET RANGE MIDPOINTS (GBP) for estimating rebate amounts:
- "<500k": £250,000
- "500k-2m": £1,250,000
- "2m-5m": £3,500,000
- "5m-15m": £10,000,000
- "15m-30m": £22,500,000
- "30m+": £40,000,000

RESPONSE JSON SCHEMA:
{
  "genre": "string — primary genre",
  "tone": "string — narrative tone",
  "scale": "string — production scale label",
  "complexity": "Low | Medium | High | Very High",
  "locationRankings": [
    {
      "name": "territory name",
      "country": "parent country",
      "score": 0-100,
      "costEfficiency": 0-100,
      "crewDepth": 0-100,
      "infrastructure": 0-100,
      "incentiveStrength": 0-100,
      "currencyAdvantage": 0-100,
      "reasoning": ["bullet 1", "bullet 2", "bullet 3"],
      "isAssessmentOnly": false
    }
  ],
  "incentiveEstimates": [
    {
      "territory": "string",
      "program": "exact program name from dataset",
      "rate": "e.g. 25%",
      "cap": "e.g. No cap or €500,000",
      "qualifyingSpend": "minimum spend requirement",
      "estimatedRebate": "GBP estimate based on budget range midpoint",
      "requirements": ["requirement 1", "requirement 2", "requirement 3"],
      "disclaimer": "Estimate only. Final eligibility depends on official approval.",
      "dataSource": "Prodculator backend datasets",
      "lastUpdated": "ISO timestamp from dataset"
    }
  ],
  "crewInsights": [
    {
      "territory": "string",
      "availability": "High | Medium | Low",
      "costVsUSD": "e.g. £3,200/day",
      "qualityRating": 1-5,
      "specialties": ["up to 5 crew roles"],
      "tradeoff": "one sentence summary of key trade-off"
    }
  ],
  "comparables": [
    {
      "title": "production title from dataset",
      "genre": "genre label",
      "budgetRange": "e.g. £5M–£15M",
      "visualScale": "scope description",
      "location": "primary filming territory",
      "year": 2024,
      "source": "data attribution"
    }
  ],
  "weatherLogistics": [
    {
      "territory": "string",
      "bestMonths": ["Apr", "May", "Sep"],
      "weatherRisk": "Low | Medium | High",
      "infrastructure": "production support summary",
      "travelVisa": "crew travel/visa notes",
      "avgTempRange": "optional",
      "avgRainfall": "optional",
      "daylightHours": "optional",
      "seasonalConsiderations": "optional"
    }
  ],
  "fundingOpportunities": [
    {
      "type": "Fund | Festival",
      "name": "exact name from dataset",
      "genre": ["genre1", "genre2"],
      "deadline": "human-readable deadline string",
      "notes": "funding amount or festival tier/location",
      "website": "optional URL",
      "tier": "optional — A-List, Tier 2, Regional, Specialized"
    }
  ]
}

SECTION REQUIREMENTS:
- locationRankings: up to 15 territories for paid, exactly 3 for preview (with isAssessmentOnly: true)
- incentiveEstimates: one entry per incentive program for ranked territories; only include for paid
- crewInsights: one per ranked territory; empty array [] for preview
- comparables: 5-8 productions matching genre/budget/territory; empty array [] for preview
- weatherLogistics: one per ranked territory; empty array [] for preview
- fundingOpportunities: mix of at least 2 grants + 3 festivals; empty array [] for preview
"""


class ScriptAnalysisService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = Anthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.ANTHROPIC_ANALYSIS_TIMEOUT,
        )

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
        if not self.settings.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key is not configured")

        started = perf_counter()
        trimmed_script, was_truncated = self._trim_script_for_prompt(script_content)
        user_content = (
            f"{SCRIPT_ANALYSIS_PROMPT}\n\n===== SCRIPT TITLE: {script_title} =====\n\n{trimmed_script}"
        )
        logger.info(
            "Script analysis started: title=%s input_chars=%s prompt_chars=%s truncated=%s model=%s",
            script_title,
            len(script_content),
            len(user_content),
            was_truncated,
            self.settings.ANTHROPIC_MODEL,
        )

        response = self._call_anthropic_with_retry(
            system_prompt="You are a professional film production analyst specializing in script breakdown and production budgeting.",
            user_content=user_content,
            temperature=0.3,
            stage="script_analysis",
        )

        raw = self._extract_text_response(response)
        try:
            data = self._parse_json_payload(raw)
            data["rawResponse"] = raw
            sanitized = self._sanitize(data)
            logger.info(
                "Script analysis completed: title=%s locations=%s budget_range=%s elapsed_ms=%s",
                script_title,
                len(sanitized.locations),
                sanitized.budgetEstimate.range,
                int((perf_counter() - started) * 1000),
            )
            return sanitized
        except (json.JSONDecodeError, Exception) as e:
            logger.exception(
                "Failed to parse script analysis response: title=%s raw_chars=%s elapsed_ms=%s error=%s",
                script_title,
                len(raw),
                int((perf_counter() - started) * 1000),
                e,
            )
            logger.warning("Using fallback script analysis: title=%s", script_title)
            return self._fallback(script_title)

    def generate_production_analysis(
        self,
        *,
        script_analysis: ScriptAnalysisResult | None,
        request_metadata: dict,
        datasets: dict,
        is_preview: bool,
    ) -> dict:
        """Generate the full ScriptAnalysis JSON from script parse + metadata + datasets."""
        if not self.settings.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key is not configured")

        started = perf_counter()
        dataset_counts = {
            key: len(value) if isinstance(value, list) else 0 for key, value in datasets.items()
        }
        compacted_datasets = self._compact_datasets_for_prompt(datasets, is_preview=is_preview)
        compacted_counts = {
            key: len(value) if isinstance(value, list) else 0 for key, value in compacted_datasets.items()
        }
        logger.info(
            "Production analysis started: preview=%s has_script_analysis=%s metadata_keys=%s dataset_counts=%s compacted_counts=%s model=%s",
            is_preview,
            bool(script_analysis),
            sorted(request_metadata.keys()),
            dataset_counts,
            compacted_counts,
            self.settings.ANTHROPIC_MODEL,
        )

        # Build user message with all context
        parts = []

        # Project metadata
        parts.append("=== PROJECT METADATA ===")
        parts.append(json.dumps(self._trim_value_for_prompt(request_metadata), default=str, separators=(",", ":")))

        # Script analysis (if available — not for preview)
        if script_analysis:
            parts.append("\n=== SCRIPT ANALYSIS (from script parse) ===")
            script_payload = self._trim_value_for_prompt(script_analysis.model_dump(exclude={"rawResponse"}))
            parts.append(json.dumps(script_payload, default=str, separators=(",", ":")))
        else:
            parts.append("\n=== SCRIPT ANALYSIS ===")
            parts.append("No script provided. Generate analysis from project metadata only.")

        # Reference datasets
        parts.append("\n=== REFERENCE DATASETS ===")
        for key, label in [
            ("incentives", "INCENTIVE PROGRAMS"),
            ("crew_costs", "CREW COST BENCHMARKS"),
            ("comparables", "COMPARABLE PRODUCTIONS"),
            ("grants", "GRANT OPPORTUNITIES"),
            ("festivals", "FILM FESTIVALS"),
        ]:
            data = compacted_datasets.get(key, [])
            parts.append(f"\n{label} ({len(data)} records in prompt):")
            if data:
                parts.append(json.dumps(data, default=str, separators=(",", ":")))
            else:
                parts.append("No data available.")

        # Mode instruction
        if is_preview:
            parts.append("\n=== MODE: PREVIEW ===")
            parts.append(
                "This is a FREE PREVIEW report. Return exactly 3 locationRankings with "
                "isAssessmentOnly: true. Return incentiveEstimates for those 3 territories. "
                "Return empty arrays [] for crewInsights, comparables, weatherLogistics, "
                "and fundingOpportunities."
            )
        else:
            parts.append("\n=== MODE: FULL PAID REPORT ===")
            parts.append(
                "This is a FULL PAID report. Return up to 15 locationRankings with "
                "isAssessmentOnly: false. Populate ALL sections with complete data."
            )

        user_message = "\n".join(parts)
        logger.info(
            "Production analysis prompt prepared: preview=%s prompt_chars=%s",
            is_preview,
            len(user_message),
        )

        response = self._call_anthropic_with_retry(
            system_prompt=PRODUCTION_ANALYSIS_PROMPT,
            user_content=user_message,
            temperature=0.2,
            stage="production_analysis",
        )

        raw = self._extract_text_response(response)
        try:
            data = self._parse_json_payload(raw)
            sanitized = self._sanitize_analysis(data, is_preview)
            logger.info(
                "Production analysis completed: preview=%s location_rankings=%s incentives=%s elapsed_ms=%s",
                is_preview,
                len(sanitized.get("locationRankings", [])),
                len(sanitized.get("incentiveEstimates", [])),
                int((perf_counter() - started) * 1000),
            )
            return sanitized
        except (json.JSONDecodeError, Exception) as e:
            logger.exception(
                "Failed to parse production analysis response: preview=%s raw_chars=%s elapsed_ms=%s error=%s",
                is_preview,
                len(raw),
                int((perf_counter() - started) * 1000),
                e,
            )
            logger.warning("Using fallback production analysis: preview=%s", is_preview)
            return self._fallback_analysis(request_metadata, is_preview)

    def _trim_script_for_prompt(self, script_content: str) -> tuple[str, bool]:
        clean = script_content.strip()
        if len(clean) <= MAX_SCRIPT_CHARS:
            return clean, False

        segment = MAX_SCRIPT_CHARS // 3
        mid_start = max((len(clean) // 2) - (segment // 2), 0)
        mid_end = mid_start + segment
        combined = (
            clean[:segment]
            + "\n\n[...SCRIPT CONTENT OMITTED FOR TOKEN CONTROL...]\n\n"
            + clean[mid_start:mid_end]
            + "\n\n[...SCRIPT CONTENT OMITTED FOR TOKEN CONTROL...]\n\n"
            + clean[-segment:]
        )
        return combined, True

    def _compact_datasets_for_prompt(self, datasets: dict, *, is_preview: bool) -> dict:
        preview_caps = {
            "incentives": 8,
            "crew_costs": 8,
            "comparables": 6,
            "grants": 8,
            "festivals": 8,
        }
        paid_caps = {
            "incentives": 18,
            "crew_costs": 18,
            "comparables": 12,
            "grants": 15,
            "festivals": 15,
        }
        field_whitelist = {
            "incentives": [
                "territory",
                "program_name",
                "program_type",
                "rate",
                "cap",
                "qualifying_spend",
                "stackable",
                "last_updated",
            ],
            "crew_costs": [
                "territory",
                "role",
                "category",
                "day_rate",
                "week_rate",
                "currency",
                "source",
                "last_updated",
            ],
            "comparables": [
                "title",
                "year",
                "genre",
                "budget_usd",
                "primary_territory",
                "incentive_used",
            ],
            "grants": [
                "title",
                "territory",
                "status",
                "amount",
                "deadline",
                "genres",
                "url",
            ],
            "festivals": [
                "name",
                "location",
                "submission_deadline",
                "deadlines",
                "genres",
                "tier",
                "website_url",
                "filmfreeway_url",
            ],
        }
        caps = preview_caps if is_preview else paid_caps
        compacted: dict[str, list] = {}
        for key, value in datasets.items():
            rows = value if isinstance(value, list) else []
            limited = rows[: caps.get(key, 10)]
            allowed_fields = field_whitelist.get(key)
            compacted_rows = []
            for row in limited:
                if not isinstance(row, dict):
                    compacted_rows.append(self._trim_value_for_prompt(row))
                    continue
                if allowed_fields:
                    slim = {field: row.get(field) for field in allowed_fields if field in row}
                else:
                    slim = row
                compacted_rows.append(self._trim_value_for_prompt(slim))
            compacted[key] = compacted_rows
        return compacted

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
    ):
        retry_delays = [8, 20]
        max_attempts = len(retry_delays) + 1
        for attempt in range(1, max_attempts + 1):
            try:
                return self.client.messages.create(
                    model=self.settings.ANTHROPIC_MODEL,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_content}],
                    temperature=temperature,
                    max_tokens=self.settings.ANTHROPIC_MAX_TOKENS,
                )
            except Exception as exc:
                if not self._is_rate_limit_error(exc) or attempt >= max_attempts:
                    raise
                delay = retry_delays[attempt - 1]
                logger.warning(
                    "Anthropic rate limit at stage=%s attempt=%s/%s, retrying in %ss",
                    stage,
                    attempt,
                    max_attempts,
                    delay,
                )
                sleep(delay)

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "rate_limit_error" in message
            or "rate limit" in message
            or "429" in message
            or "input tokens per minute" in message
        )

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

    def _sanitize_analysis(self, data: dict, is_preview: bool) -> dict:
        """Validate and fill defaults for production analysis AI response."""
        result = {
            "genre": data.get("genre", "Drama"),
            "tone": data.get("tone", "Unknown"),
            "scale": data.get("scale", "Unknown"),
            "complexity": data.get("complexity", "Medium"),
            "locationRankings": [],
            "incentiveEstimates": [],
            "crewInsights": [] if is_preview else data.get("crewInsights", []),
            "comparables": [] if is_preview else data.get("comparables", []),
            "weatherLogistics": [] if is_preview else data.get("weatherLogistics", []),
            "fundingOpportunities": [] if is_preview else data.get("fundingOpportunities", []),
        }

        # Validate complexity enum
        if result["complexity"] not in ("Low", "Medium", "High", "Very High"):
            result["complexity"] = "Medium"

        # Sanitize location rankings
        for loc in data.get("locationRankings", []):
            result["locationRankings"].append({
                "name": loc.get("name", "Unknown"),
                "country": loc.get("country", "Unknown"),
                "score": max(0, min(100, loc.get("score", 50))),
                "costEfficiency": max(0, min(100, loc.get("costEfficiency", 50))),
                "crewDepth": max(0, min(100, loc.get("crewDepth", 50))),
                "infrastructure": max(0, min(100, loc.get("infrastructure", 50))),
                "incentiveStrength": max(0, min(100, loc.get("incentiveStrength", 50))),
                "currencyAdvantage": max(0, min(100, loc.get("currencyAdvantage", 50))),
                "reasoning": loc.get("reasoning", ["No detailed reasoning available"]),
                "isAssessmentOnly": True if is_preview else loc.get("isAssessmentOnly", False),
            })

        # Sanitize incentive estimates
        for inc in data.get("incentiveEstimates", []):
            result["incentiveEstimates"].append({
                "territory": inc.get("territory", "Unknown"),
                "program": inc.get("program", "Unknown Program"),
                "rate": inc.get("rate", "N/A"),
                "cap": inc.get("cap", "N/A"),
                "qualifyingSpend": inc.get("qualifyingSpend", "N/A"),
                "estimatedRebate": inc.get("estimatedRebate", "N/A"),
                "requirements": inc.get("requirements", []),
                "disclaimer": "Estimate only. Final eligibility depends on official approval.",
                "dataSource": "Prodculator backend datasets",
                "lastUpdated": inc.get("lastUpdated", ""),
            })

        # Enforce preview limits
        if is_preview:
            result["locationRankings"] = result["locationRankings"][:3]

        return result

    def _fallback_analysis(self, request_metadata: dict, is_preview: bool) -> dict:
        """Return fallback analysis when production analysis API call fails."""
        genre = (request_metadata.get("genre") or ["Drama"])[0]
        country = request_metadata.get("country", "UK")
        return {
            "genre": genre,
            "tone": "Unknown — analysis unavailable",
            "scale": f"{request_metadata.get('budget_range', 'Unknown')} production",
            "complexity": "Medium",
            "locationRankings": [
                {
                    "name": country,
                    "country": country,
                    "score": 60,
                    "costEfficiency": 50,
                    "crewDepth": 60,
                    "infrastructure": 60,
                    "incentiveStrength": 50,
                    "currencyAdvantage": 50,
                    "reasoning": ["Default territory based on project country", "Full analysis unavailable"],
                    "isAssessmentOnly": is_preview,
                }
            ],
            "incentiveEstimates": [],
            "crewInsights": [],
            "comparables": [],
            "weatherLogistics": [],
            "fundingOpportunities": [],
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
            ),
            rawResponse=data.get("rawResponse"),
        )

    def _fallback(self, script_title: str) -> ScriptAnalysisResult:
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
            rawResponse="Fallback analysis used due to API error",
        )
