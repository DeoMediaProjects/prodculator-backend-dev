"""Versioned, provenance-preserving input snapshot for v2 report engines.

The snapshot records what the current upload request and script analysis held.
It does not resolve missing specialist facts by asking new questions or guessing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.modules.reports.project_dna import ProjectDNA


VERSION = "1"


@dataclass(frozen=True)
class ProjectFactsSnapshot:
    snapshot_id: str
    version: str
    _canonical_json: str

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical_json)


def _fact(value: Any, source: str, *, confirm: bool = False) -> dict[str, Any]:
    known = value is not None and value != "" and value != []
    return {
        "value": value if known else None,
        "source": source,
        "status": "KNOWN" if known else "UNKNOWN",
        "requires_confirmation": bool(confirm and known),
    }


def build_project_facts_snapshot(
    request_metadata: dict[str, Any],
    project_dna: ProjectDNA,
) -> ProjectFactsSnapshot:
    """Freeze one report run's held facts, with objective declarations intact."""
    metadata = request_metadata
    territories = metadata.get("territories_considering") or []
    if not isinstance(territories, list):
        territories = []
    spend = {str(label): None for label in territories if label}
    for item in metadata.get("territory_scenarios") or []:
        row = item if isinstance(item, dict) else item.model_dump() if hasattr(item, "model_dump") else {}
        territory = row.get("territory")
        if territory:
            spend[str(territory)] = row.get("scenario_spend")

    facts = {
        "legacy_format_selection": _fact(metadata.get("format"), "USER"),
        "declared_genres": _fact(metadata.get("genre"), "USER"),
        "total_budget": _fact(metadata.get("budget_amount"), "USER"),
        "project_currency": _fact(metadata.get("budget_currency"), "USER"),
        "production_country_declared": _fact(
            metadata.get("production_country") or metadata.get("country"), "USER"
        ),
        "territories_considering": _fact(territories, "USER"),
        "expected_spend_by_territory": {
            "value": spend if spend else None,
            "source": "USER",
            "status": "KNOWN" if any(value is not None for value in spend.values()) else "UNKNOWN",
            "requires_confirmation": False,
        },
        "filming_start_date": _fact(metadata.get("filming_start_date"), "USER"),
        "filming_duration_weeks": _fact(metadata.get("filming_duration"), "USER"),
        "expected_completion_date": _fact(metadata.get("completion_date"), "USER"),
        "declared_languages": _fact(
            metadata.get("primary_languages") or metadata.get("language"), "USER"
        ),
        "must_film_in": _fact(metadata.get("must_film_in"), "USER"),
        "official_coproduction_openness": _fact(
            metadata.get("co_production_interest"), "USER"
        ),
        "declared_target_audiences": _fact(metadata.get("target_audience"), "USER"),
        "premiere_history": _fact(project_dna.value("premiere_history"), "USER"),
        "rights_status": _fact(project_dna.value("project_rights"), "USER"),
        "secured_finance": _fact(project_dna.value("secured_finance"), "USER"),
    }
    script = {}
    for source_name, output_name in (
        ("format", "primary_format"),
        ("content_form", "content_form"),
        ("technique", "technique"),
        ("script_genres", "script_genres"),
        ("tone", "tone"),
        ("themes", "themes"),
        ("story_countries", "story_countries"),
        ("script_dialogue_languages", "detected_languages"),
        ("commercial_positioning", "commercial_positioning"),
    ):
        observed = project_dna.get(source_name)
        provenance = "SCRIPT" if (observed.source or "").startswith("script_analysis") else (
            "USER" if (observed.source or "").startswith("intake") else "COMPUTED"
        )
        script[output_name] = _fact(
            observed.value, provenance, confirm=observed.confirmation_required
        )
    payload = {
        "project_title": metadata.get("script_title"),
        "facts": facts,
        "script_analysis": script,
        "conflicts": [],
        "unknown_required_facts": [
            name for name in ("premiere_history", "rights_status", "secured_finance")
            if facts[name]["status"] == "UNKNOWN"
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256((VERSION + "\n" + canonical).encode("utf-8")).hexdigest()
    return ProjectFactsSnapshot(digest, VERSION, canonical)
