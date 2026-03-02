import json
import logging

import pdfplumber
from openai import OpenAI

from app.core.config import Settings
from app.modules.scripts.schemas import ScriptAnalysisResult

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"pdf", "txt", "fountain", "fdx"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_SCRIPT_CHARS = 90_000

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
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

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

        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n".join(text_parts)

    def extract_text(self, filename: str, file_bytes: bytes) -> str:
        """Extract text from various script formats."""
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext == "pdf":
            return self.extract_text_from_pdf(file_bytes)
        return file_bytes.decode("utf-8")

    def analyze(self, script_content: str, script_title: str) -> ScriptAnalysisResult:
        """Analyze script using OpenAI GPT-4o."""
        if not self.settings.OPENAI_API_KEY:
            raise ValueError("OpenAI API key is not configured")

        truncated = script_content[:MAX_SCRIPT_CHARS]
        if len(script_content) > MAX_SCRIPT_CHARS:
            truncated += "\n\n[Script truncated due to length]"

        response = self.client.chat.completions.create(
            model=self.settings.OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional film production analyst specializing in script breakdown and production budgeting.",
                },
                {
                    "role": "user",
                    "content": f"{SCRIPT_ANALYSIS_PROMPT}\n\n===== SCRIPT TITLE: {script_title} =====\n\n{truncated}",
                },
            ],
            temperature=0.3,
            max_tokens=self.settings.OPENAI_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        try:
            data = json.loads(raw)
            data["rawResponse"] = raw
            return self._sanitize(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to parse OpenAI response: %s", e)
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
        if not self.settings.OPENAI_API_KEY:
            raise ValueError("OpenAI API key is not configured")

        # Build user message with all context
        parts = []

        # Project metadata
        parts.append("=== PROJECT METADATA ===")
        parts.append(json.dumps(request_metadata, indent=2))

        # Script analysis (if available — not for preview)
        if script_analysis:
            parts.append("\n=== SCRIPT ANALYSIS (from script parse) ===")
            parts.append(json.dumps(script_analysis.model_dump(exclude={"rawResponse"}), indent=2))
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
            data = datasets.get(key, [])
            parts.append(f"\n{label} ({len(data)} records):")
            if data:
                parts.append(json.dumps(data, indent=2, default=str))
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

        response = self.client.chat.completions.create(
            model=self.settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": PRODUCTION_ANALYSIS_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
            max_tokens=self.settings.OPENAI_MAX_TOKENS,
            response_format={"type": "json_object"},
            timeout=self.settings.OPENAI_ANALYSIS_TIMEOUT,
        )

        raw = response.choices[0].message.content
        try:
            data = json.loads(raw)
            return self._sanitize_analysis(data, is_preview)
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to parse production analysis response: %s", e)
            return self._fallback_analysis(request_metadata, is_preview)

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

    def _sanitize(self, data: dict) -> ScriptAnalysisResult:
        """Validate and fill defaults for AI response."""
        return ScriptAnalysisResult(
            locations=data.get("locations", []),
            budgetEstimate={
                "range": data.get("budgetEstimate", {}).get("range", "medium"),
                "minUSD": data.get("budgetEstimate", {}).get("minUSD", 5_000_000),
                "maxUSD": data.get("budgetEstimate", {}).get("maxUSD", 30_000_000),
                "confidence": data.get("budgetEstimate", {}).get("confidence", 0.7),
                "indicators": data.get("budgetEstimate", {}).get(
                    "indicators", ["General industry estimates"]
                ),
            },
            productionScale={
                "crewSize": data.get("productionScale", {}).get("crewSize", "medium"),
                "principalCast": data.get("productionScale", {}).get("principalCast", "medium"),
                "supportingCast": data.get("productionScale", {}).get("supportingCast", "medium"),
                "backgroundExtras": data.get("productionScale", {}).get(
                    "backgroundExtras", "medium"
                ),
                "estimatedShootingDays": data.get("productionScale", {}).get(
                    "estimatedShootingDays", 30
                ),
            },
            equipment={
                "cameraEquipment": data.get("equipment", {}).get("cameraEquipment", "arri"),
                "specialEquipment": data.get("equipment", {}).get("specialEquipment", []),
                "vfxRequirements": data.get("equipment", {}).get("vfxRequirements", "moderate"),
            },
            metadata={
                "genres": data.get("metadata", {}).get("genres", ["Drama"]),
                "format": data.get("metadata", {}).get("format", "feature"),
                "tone": data.get("metadata", {}).get("tone", "Unknown"),
                "targetAudience": data.get("metadata", {}).get(
                    "targetAudience", "General audiences"
                ),
            },
            challenges={
                "weatherDependent": data.get("challenges", {}).get("weatherDependent", False),
                "historicalPeriod": data.get("challenges", {}).get("historicalPeriod", False),
                "specialPermits": data.get("challenges", {}).get("specialPermits", False),
                "stunts": data.get("challenges", {}).get("stunts", False),
                "animalWrangling": data.get("challenges", {}).get("animalWrangling", False),
                "waterWork": data.get("challenges", {}).get("waterWork", False),
                "nightShooting": data.get("challenges", {}).get("nightShooting", False),
                "notes": data.get("challenges", {}).get("notes", []),
            },
            rawResponse=data.get("rawResponse"),
        )

    def _fallback(self, script_title: str) -> ScriptAnalysisResult:
        """Return fallback analysis when API fails."""
        return ScriptAnalysisResult(
            locations=[
                {
                    "name": "Los Angeles",
                    "country": "United States",
                    "territory": "California (USA)",
                    "frequency": 10,
                    "isMainLocation": True,
                }
            ],
            budgetEstimate={
                "range": "medium",
                "minUSD": 5_000_000,
                "maxUSD": 30_000_000,
                "confidence": 0.5,
                "indicators": ["Estimated based on typical feature film budget"],
            },
            productionScale={
                "crewSize": "medium",
                "principalCast": "medium",
                "supportingCast": "medium",
                "backgroundExtras": "medium",
                "estimatedShootingDays": 30,
            },
            equipment={
                "cameraEquipment": "arri",
                "specialEquipment": [],
                "vfxRequirements": "moderate",
            },
            metadata={
                "genres": ["Drama"],
                "format": "feature",
                "tone": "Unknown",
                "targetAudience": "General audiences",
            },
            challenges={
                "weatherDependent": False,
                "historicalPeriod": False,
                "specialPermits": False,
                "stunts": False,
                "animalWrangling": False,
                "waterWork": False,
                "nightShooting": False,
                "notes": ["Analysis failed - using default estimates"],
            },
            rawResponse="Fallback analysis used due to API error",
        )
