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
