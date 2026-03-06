import json
import logging
import re

from anthropic import Anthropic

from app.core.config import Settings

logger = logging.getLogger(__name__)

INCENTIVES_PROMPT = """Extract film production incentive programs from this text.
Return a JSON object with key "programs", a list of objects, each with:
  territory (string — use the full country name, e.g. "United Kingdom" not "UK"),
  program (string — the official program name),
  rate (string — standardise as a percentage like "25%" or a range like "25-30%"; distinguish tax credit, rebate, and offset types by prefixing, e.g. "Tax Credit: 25%"),
  cap (string or null — maximum qualifying spend or rebate cap if mentioned),
  status ("Active"|"Inactive"),
  source_url (string or null)
Only include programs clearly described in the text. If none found, return {"programs": []}.
Respond ONLY with valid JSON, no markdown."""

CREW_COSTS_PROMPT = """Extract film/TV crew day rates and/or week rates from this text.
The text may come from a union rate card PDF with tabular data (tab-separated columns).
Return a JSON object with key "crew_costs", a list of objects, each with:
  territory (string — use full country name, e.g. "United Kingdom" not "UK"),
  role (string — the crew role/position title),
  category (string — "Above-the-Line" or "Below-the-Line"),
  day_rate (number or null — daily rate in the local currency; convert hourly to daily by multiplying by 10),
  week_rate (number or null — weekly rate in the local currency; convert daily to weekly by multiplying by 5 if not stated),
  union (string or null — union/guild name if applicable, e.g. "BECTU", "IATSE Local 891", "MEAA")
Only include figures explicitly stated or directly calculable. Return {"crew_costs": []} if none found.
Respond ONLY with valid JSON."""

GRANTS_PROMPT = """Extract film grant and funding opportunities from this text.
Return a JSON object with key "grants", a list of objects, each with:
  title (string — the official grant/fund name),
  territory (string or null — use full country name; for EU-wide programs use "European Union"),
  funding_body (string or null — the organisation providing the funding),
  max_amount (string or null — the maximum funding amount as a number string, e.g. "50000"),
  currency (string or null — ISO currency code: "GBP", "EUR", "USD", "CAD", "AUD"),
  application_deadline (string ISO date YYYY-MM-DD or null — parse dates in any format to ISO),
  eligibility (list of strings — key eligibility criteria),
  website_url (string or null),
  status ("open"|"closed"|"upcoming" — based on deadlines relative to current date)
Return {"grants": []} if none found. Respond ONLY with valid JSON."""

FESTIVALS_PROMPT = """Extract film festival information from this text.
Return a JSON object with key "festivals", a list of objects, each with:
  name (string — official festival name),
  year (integer or null — the edition year),
  location (string or null — "City, Country" format),
  tier ("A-List"|"Tier 2"|"Regional"|"Specialized"|null — A-List = FIAPF competitive, Oscar/BAFTA-qualifying),
  genres (list of strings — accepted categories e.g. ["Narrative Feature", "Documentary", "Short"]),
  premiere_requirement (string or null — e.g. "World Premiere", "International Premiere", "None"),
  acceptance_rate (string or null — e.g. "< 1%" or "3%"),
  website_url (string or null),
  deadlines (list of {tier: string, date: string} objects — tier is "early-bird"|"regular"|"late"|"extended", date is ISO YYYY-MM-DD)
Return {"festivals": []} if none found. Respond ONLY with valid JSON."""

_PROMPTS = {
    "incentives": INCENTIVES_PROMPT,
    "crew_costs": CREW_COSTS_PROMPT,
    "grants": GRANTS_PROMPT,
    "festivals": FESTIVALS_PROMPT,
}

_RESULT_KEYS = {
    "incentives": "programs",
    "crew_costs": "crew_costs",
    "grants": "grants",
    "festivals": "festivals",
}


def _output_schema_for(resource_type: str) -> dict:
    item_schemas: dict[str, dict] = {
        "incentives": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "territory": {"type": "string"},
                "program": {"type": "string"},
                "rate": {"type": "string"},
                "cap": {"type": ["string", "null"]},
                "status": {"type": "string"},
                "source_url": {"type": ["string", "null"]},
            },
            "required": ["territory", "program", "rate", "status"],
        },
        "crew_costs": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "territory": {"type": "string"},
                "role": {"type": "string"},
                "category": {"type": "string"},
                "day_rate": {"type": ["number", "null"]},
                "week_rate": {"type": ["number", "null"]},
                "union": {"type": ["string", "null"]},
            },
            "required": ["territory", "role", "category"],
        },
        "grants": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "territory": {"type": ["string", "null"]},
                "funding_body": {"type": ["string", "null"]},
                "max_amount": {"type": ["string", "null"]},
                "currency": {"type": ["string", "null"]},
                "application_deadline": {"type": ["string", "null"]},
                "eligibility": {"type": "array", "items": {"type": "string"}},
                "website_url": {"type": ["string", "null"]},
                "status": {"type": "string"},
            },
            "required": ["title", "status", "eligibility"],
        },
        "festivals": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "year": {"type": ["integer", "null"]},
                "location": {"type": ["string", "null"]},
                "tier": {"type": ["string", "null"]},
                "genres": {"type": "array", "items": {"type": "string"}},
                "premiere_requirement": {"type": ["string", "null"]},
                "acceptance_rate": {"type": ["string", "null"]},
                "website_url": {"type": ["string", "null"]},
                "deadlines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "tier": {"type": "string"},
                            "date": {"type": "string"},
                        },
                        "required": ["tier", "date"],
                    },
                },
            },
            "required": ["name", "genres", "deadlines"],
        },
    }
    result_key = _RESULT_KEYS[resource_type]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            result_key: {
                "type": "array",
                "items": item_schemas[resource_type],
            }
        },
        "required": [result_key],
    }


def extract(
    resource_type: str,
    page_text: str,
    territory_hint: str | None,
    settings: Settings,
) -> list[dict]:
    """Send page text to Anthropic Claude and return list of extracted records."""
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set")

    prompt = _PROMPTS.get(resource_type)
    if not prompt:
        raise ValueError(f"Unknown resource_type: {resource_type}")

    territory_note = f"\nTerritory context: {territory_hint}" if territory_hint else ""
    user_message = f"{territory_note}\n\nPAGE TEXT:\n{page_text}"

    client = Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=settings.ANTHROPIC_ANALYSIS_TIMEOUT,
    )
    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            system=prompt,
            messages=[
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=min(settings.ANTHROPIC_MAX_TOKENS, 4000),
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": _output_schema_for(resource_type),
                }
            },
        )
        raw = _extract_text_response(response)
        try:
            data = _parse_json_payload(raw)
        except Exception as parse_exc:
            if getattr(response, "stop_reason", None) == "max_tokens":
                raise ValueError(
                    "Anthropic output was truncated at max_tokens. Increase ANTHROPIC_MAX_TOKENS."
                ) from parse_exc
            raise
        result_key = _RESULT_KEYS[resource_type]
        return data.get(result_key, [])
    except Exception as exc:
        logger.error("Anthropic extraction failed for %s: %s", resource_type, exc)
        raise RuntimeError(f"Anthropic extraction failed for {resource_type}") from exc


def _extract_text_response(response) -> str:
    text_parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    text = "\n".join(text_parts).strip()
    if not text:
        raise ValueError("Anthropic returned no text content")
    return text


def _parse_json_payload(raw: str) -> dict:
    payload = raw.strip()

    # Handle fenced JSON responses: ```json ... ```
    if payload.startswith("```"):
        payload = re.sub(r"^```(?:json)?\s*", "", payload, flags=re.IGNORECASE)
        payload = re.sub(r"\s*```$", "", payload)
        payload = payload.strip()

    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            return data
        raise ValueError("JSON payload is not an object")
    except Exception:
        # Fallback: extract first JSON object-like block from mixed text.
        match = re.search(r"\{[\s\S]*\}", payload)
        if not match:
            preview = payload[:200].replace("\n", " ")
            raise ValueError(f"No JSON object found in model response: {preview}")
        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise ValueError("JSON payload is not an object")
        return data
