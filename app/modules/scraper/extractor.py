import json
import logging
import re
import time

from anthropic import Anthropic, InternalServerError, RateLimitError

from app.core.config import Settings

logger = logging.getLogger(__name__)

INCENTIVES_PROMPT = """Extract film production incentive programs from this text.
Return a JSON object with key "programs", a list of objects, each with:
  territory (string — use the full country name, e.g. "United Kingdom" not "UK"),
  program (string — the official program name),
  rate (string — standardise as a percentage like "25%" or a range like "25-30%"; distinguish tax credit, rebate, and offset types by prefixing, e.g. "Tax Credit: 25%"),
  rate_gross (number or null — gross percentage as a number, e.g. 25.0),
  rate_net (number or null — net percentage after local corporate tax, e.g. 18.75),
  rate_type (string or null — one of "tax_credit", "cash_rebate", "tax_relief", "none"),
  currency (string or null — ISO currency code for the incentive calculation, e.g. "GBP", "EUR"),
  cap (string or null — maximum qualifying spend or rebate cap if mentioned),
  cap_amount (number or null — numeric cap in local currency),
  cap_currency (string or null — ISO currency code for the cap),
  payment_timeline (string or null — human-readable payment timeline, e.g. "6-8 weeks from claim submission"),
  payment_timeline_days_min (integer or null — minimum days to payment),
  payment_timeline_days_max (integer or null — maximum days to payment),
  eligibility_rules (list of strings — key eligibility criteria, e.g. ["Must pass BFI cultural test", "Minimum 10% UK spend"]),
  expiry_date (string ISO date YYYY-MM-DD or null — if the programme has a sunset/expiry date),
  status ("Active"|"Inactive"),
  source_url (string or null)
Only include programs clearly described in the text. If none found, return {"programs": []}.
Respond ONLY with valid JSON, no markdown."""

CREW_COSTS_PROMPT = """Extract film/TV crew and cast occupational wage data from this government statistics text.
Return a JSON object with key "crew_costs", a list of objects, each with:
  country (string — ISO 2-letter code, e.g. "US", "GB", "CA"),
  role (string — the crew/cast role or occupational title),
  role_category (string — e.g. "HOD-Production", "HOD-Camera", "CAST-Lead", "BTL-General"),
  department (string — rate period: "day", "week", or "session"),
  union_rate_cents (integer or null — low-end rate estimate in cents of the local currency),
  non_union_rate_cents (integer or null — high-end rate estimate in cents of the local currency),
  rate_currency (string or null — ISO currency code, e.g. "USD", "GBP", "CAD"),
  source_name (string or null — the specific government source, e.g. "BLS OEWS / SOC 27-4031"),
  confidence_score (integer or null — 0-100 confidence in the estimate)
Only include figures explicitly stated or directly calculable from government data.
Return {"crew_costs": []} if none found.
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


def _nullable(type_name: str) -> dict:
    """Return an anyOf schema that allows *type_name* or null.

    Anthropic's structured-output ``json_schema`` format does **not** accept
    the draft-04 shorthand ``"type": ["string", "null"]``.  It requires the
    ``anyOf`` form instead.
    """
    return {"anyOf": [{"type": type_name}, {"type": "null"}]}


def _output_schema_for(resource_type: str) -> dict:
    # NOTE: Anthropic's structured-output ``json_schema`` requires that every
    # property is listed in ``required`` when ``additionalProperties`` is false.
    # Nullable fields use ``anyOf`` so the model can still return ``null``.
    item_schemas: dict[str, dict] = {
        "incentives": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "territory": {"type": "string"},
                "program": {"type": "string"},
                "rate": {"type": "string"},
                "rate_gross": _nullable("number"),
                "rate_net": _nullable("number"),
                "rate_type": _nullable("string"),
                "currency": _nullable("string"),
                "cap": _nullable("string"),
                "cap_amount": _nullable("number"),
                "cap_currency": _nullable("string"),
                "payment_timeline": _nullable("string"),
                "payment_timeline_days_min": _nullable("integer"),
                "payment_timeline_days_max": _nullable("integer"),
                "eligibility_rules": {"type": "array", "items": {"type": "string"}},
                "expiry_date": _nullable("string"),
                "status": {"type": "string"},
                "source_url": _nullable("string"),
            },
            "required": [
                "territory", "program", "rate", "rate_gross", "rate_net",
                "rate_type", "currency", "cap", "cap_amount", "cap_currency",
                "payment_timeline", "payment_timeline_days_min",
                "payment_timeline_days_max", "eligibility_rules",
                "expiry_date", "status", "source_url",
            ],
        },
        "crew_costs": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "country": {"type": "string"},
                "role": {"type": "string"},
                "role_category": {"type": "string"},
                "department": _nullable("string"),
                "union_rate_cents": _nullable("integer"),
                "non_union_rate_cents": _nullable("integer"),
                "rate_currency": _nullable("string"),
                "source_name": _nullable("string"),
                "confidence_score": _nullable("integer"),
            },
            "required": [
                "country", "role", "role_category", "department",
                "union_rate_cents", "non_union_rate_cents", "rate_currency",
                "source_name", "confidence_score",
            ],
        },
        "grants": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "territory": _nullable("string"),
                "funding_body": _nullable("string"),
                "max_amount": _nullable("string"),
                "currency": _nullable("string"),
                "application_deadline": _nullable("string"),
                "eligibility": {"type": "array", "items": {"type": "string"}},
                "website_url": _nullable("string"),
                "status": {"type": "string"},
            },
            "required": [
                "title", "territory", "funding_body", "max_amount",
                "currency", "application_deadline", "eligibility",
                "website_url", "status",
            ],
        },
        "festivals": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "year": _nullable("integer"),
                "location": _nullable("string"),
                "tier": _nullable("string"),
                "genres": {"type": "array", "items": {"type": "string"}},
                "premiere_requirement": _nullable("string"),
                "acceptance_rate": _nullable("string"),
                "website_url": _nullable("string"),
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
            "required": [
                "name", "year", "location", "tier", "genres",
                "premiere_requirement", "acceptance_rate", "website_url",
                "deadlines",
            ],
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

    _RETRYABLE = (InternalServerError, RateLimitError)
    max_attempts = 3
    base_delay = 5.0  # seconds

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
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
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt < max_attempts:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Anthropic transient error for %s (attempt %d/%d), retrying in %.0fs: %s",
                    resource_type, attempt, max_attempts, delay, exc,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Anthropic extraction failed for %s after %d attempts: %s",
                    resource_type, max_attempts, exc,
                )
        except Exception as exc:
            logger.error("Anthropic extraction failed for %s: %s", resource_type, exc)
            raise RuntimeError(f"Anthropic extraction failed for {resource_type}") from exc

    raise RuntimeError(f"Anthropic extraction failed for {resource_type}") from last_exc


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
