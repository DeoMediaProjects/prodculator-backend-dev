import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.database_client import DatabaseClient

logger = logging.getLogger(__name__)

# Minimum year for scraped dates — anything older is likely stale data
_MIN_DATE_YEAR = 2024

# Canonical territory names — AI extractors may return abbreviations or variants
_TERRITORY_ALIASES: dict[str, str] = {
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "britain": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "usa": "United States",
    "america": "United States",
    "eu": "European Union",
    "czech": "Czech Republic",
    "czechia": "Czech Republic",
}


def normalize_territory(value: str | None) -> str | None:
    """Map common abbreviations/variants to canonical country names."""
    if value is None:
        return None
    return _TERRITORY_ALIASES.get(value.strip().lower(), value.strip())


# Primary label field per resource type — used for display in pending changes
_LABEL_FIELDS = {
    "incentives": "program",
    "grants": "title",
    "festivals": "name",
}

# Fields to diff per resource type — only these generate pending_changes
_DIFF_FIELDS = {
    "incentives": ["rate", "cap", "status"],
    "grants": ["max_amount", "application_deadline", "status"],
    "festivals": ["tier", "acceptance_rate", "premiere_requirement"],
}

# Keys used to match an extracted record to an existing DB row
_MATCH_KEYS = {
    "incentives": ("territory", "program"),
    "grants": ("title", "territory"),
    "festivals": ("name", "year"),
}

_TABLES = {
    "incentives": "incentive_programs",
    "grants": "grant_opportunities",
    "festivals": "film_festivals",
}

# Date-type diff fields — these get stale-date validation
_DATE_FIELDS = {"application_deadline", "application_opens"}

# ISO date pattern for validation
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Null-ish placeholder strings AI extraction falls back to when a page
# doesn't actually contain the requested value. These must never be queued
# as a confirmed detected change — they mean "couldn't find it", not "found
# an empty value".
_PLACEHOLDER_VALUES = {
    "n/a", "na", "none", "unknown", "tbd", "null", "-", "--",
    "not available", "not found", "no data", "no rate", "n.a.",
}


def _is_placeholder_value(value: str) -> bool:
    """Return True if *value* is a null-ish placeholder rather than real data."""
    return value.strip().lower() in _PLACEHOLDER_VALUES


def _is_stale_date(value: str) -> bool:
    """Return True if a date string represents a year before _MIN_DATE_YEAR."""
    m = _ISO_DATE_RE.match(value.strip())
    if not m:
        return False
    try:
        year = int(value[:4])
        return year < _MIN_DATE_YEAR
    except (ValueError, IndexError):
        return False


def diff_and_queue(
    resource_type: str,
    extracted_records: list[dict[str, Any]],
    source_url: str,
    db: DatabaseClient,
    confidence: str = "medium",
    territory_hint: str | None = None,
) -> int:
    """Compare extracted records against DB rows.
    Inserts pending_changes for any field that differs.
    Returns count of pending_changes created.

    ``territory_hint`` is the territory declared on the scrape source config
    (trusted, one URL maps to exactly one territory). When set, it overrides
    whatever territory the AI extractor guessed from page text — the page
    text can mention other territories in passing (e.g. a national incentive
    page referencing a named province), which would otherwise cause the
    record to be matched and written into the wrong territory's row.
    """
    table = _TABLES[resource_type]
    match_keys = _MATCH_KEYS[resource_type]
    diff_fields = _DIFF_FIELDS[resource_type]
    label_field = _LABEL_FIELDS.get(resource_type)
    now = datetime.now(timezone.utc).isoformat()
    changes_created = 0
    normalized_hint = normalize_territory(territory_hint)

    for record in extracted_records:
        # Normalise territory before matching and diffing
        if "territory" in record:
            record["territory"] = normalize_territory(record["territory"])

        # A trusted source-configured territory always wins over whatever
        # the AI extracted from the page text — see docstring above.
        if normalized_hint is not None:
            record["territory"] = normalized_hint

        existing = _find_existing(db, table, record, match_keys)
        resource_id = existing.get("id") if existing else None
        territory = (
            record.get("territory")
            or (existing.get("territory") if existing else None)
            or "Unknown"
        )

        # Get the human-readable label for this record
        record_label = record.get(label_field) if label_field else None

        for field in diff_fields:
            extracted_val = record.get(field)
            if extracted_val is None:
                continue

            extracted_str = str(extracted_val).strip()

            # Extraction failed to find a real value — don't queue a null-ish
            # placeholder as though it were a confirmed finding.
            if _is_placeholder_value(extracted_str):
                logger.warning(
                    "Extraction failed for %s.%s in %s: got placeholder %r (source: %s)",
                    resource_type, field, territory, extracted_str, source_url,
                )
                continue

            # Skip stale dates (e.g. 2017-01-16 from old page content)
            if field in _DATE_FIELDS and _is_stale_date(extracted_str):
                logger.info(
                    "Skipping stale date for %s.%s: %s (source: %s)",
                    resource_type, field, extracted_str, source_url,
                )
                continue

            current_val = existing.get(field) if existing else None
            current_str = str(current_val).strip() if current_val is not None else None

            if current_str == extracted_str:
                continue

            # Dedup: skip if a pending change for this same territory + field
            # already exists, regardless of exact wording — otherwise the same
            # underlying fact gets queued repeatedly worded slightly
            # differently each time (e.g. "53%" vs "Enhanced rate").
            if _pending_change_exists(db, resource_type, resource_id, territory, field):
                continue

            change_row: dict[str, Any] = {
                "id": str(uuid4()),
                "resource_type": resource_type,
                "resource_id": resource_id,
                "territory": territory,
                "field": field,
                "current_value": current_str,
                "detected_value": extracted_str,
                "confidence": confidence,
                "source": source_url,
                "status": "pending",
                "created_at": now,
            }

            # Store identifying label so the UI can show e.g. "BFI Fund"
            # instead of just "Ireland - application_deadline"
            if record_label:
                change_row["record_label"] = record_label

            db.table("pending_changes").insert(change_row).execute()
            changes_created += 1
            logger.info(
                "Queued change: %s.%s [%s] in %s: %r -> %r (source: %s)",
                resource_type, field, record_label or "?",
                territory, current_str, extracted_str, source_url,
            )

    return changes_created


def _find_existing(
    db: DatabaseClient,
    table: str,
    record: dict[str, Any],
    match_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    query = db.table(table).select("*")
    for key in match_keys:
        val = record.get(key)
        if val is not None:
            query = query.eq(key, val)
    result = query.execute()
    rows = result.data or []
    return rows[0] if rows else None


def _pending_change_exists(
    db: DatabaseClient,
    resource_type: str,
    resource_id: str | None,
    territory: str | None,
    field: str,
) -> bool:
    """Prevent duplicate pending_changes for the same territory + field.

    Matches on the fact being changed (same resource, or same territory when
    the record isn't linked to an existing row), not on the exact wording of
    the detected value — two extractions of the same underlying fact can be
    worded differently (e.g. "53%" vs "Enhanced rate") and would otherwise
    both slip past an exact-value dedup check.
    """
    query = (
        db.table("pending_changes")
        .select("*", count="exact", head=True)
        .eq("resource_type", resource_type)
        .eq("field", field)
        .eq("status", "pending")
    )
    if resource_id:
        query = query.eq("resource_id", resource_id)
    else:
        query = query.eq("territory", territory)
    result = query.execute()
    return (result.count or 0) > 0
