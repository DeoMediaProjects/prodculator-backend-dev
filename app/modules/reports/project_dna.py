"""Shared, provenance-aware facts for report recommendation engines.

Unknown is a first-class state. In particular, a screenplay setting is not a
production origin, and silence about a premiere or rights is not a negative answer.
This object is built without asking the producer another question.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.formats import canonical_format


@dataclass(frozen=True)
class ProjectFact:
    value: Any = None
    source: str | None = None
    confidence: float | None = None
    confirmation_required: bool = True

    @property
    def state(self) -> str:
        return "KNOWN" if self.value is not None else "UNKNOWN"


@dataclass(frozen=True)
class ProjectDNA:
    facts: dict[str, ProjectFact]

    def get(self, name: str) -> ProjectFact:
        return self.facts.get(name, ProjectFact())

    def value(self, name: str) -> Any:
        return self.get(name).value


PROJECT_FACT_FIELDS = (
    "format",
    "content_form",
    "technique",
    "animation_percentage",
    "student_status",
    "attached_team",
    "application_materials",
    "commercial_positioning",
    "runtime_minutes",
    "genres",
    "declared_genres",
    "script_genres",
    "tone",
    "themes",
    "primary_languages",
    "script_dialogue_languages",
    "production_countries",
    "story_countries",
    "target_audience",
    "target_sales_territories",
    "release_profile",
    "stage",
    "budget_gbp",
    "completion_date",
    "premiere_history",
    "prior_screenings",
    "public_online_availability",
    "applicant_nationality",
    "applicant_residency",
    "project_rights",
    "secured_finance",
    "participant_credits",
    "footage_readiness",
)


def _clean_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return None
    result = sorted({str(item).strip() for item in value if item is not None and str(item).strip()})
    return result or None


def _clean_text(value: Any) -> str | None:
    if value is None or isinstance(value, (list, dict, bool)):
        return None
    result = str(value).strip()
    if result.casefold() in {"unknown", "undecided", "not sure", "n/a", "tbd"}:
        return None
    return result or None


def build_project_dna(
    request_metadata: dict[str, Any],
    datasets: dict[str, Any],
    script_analysis: Any = None,
) -> ProjectDNA:
    """Normalise only observed inputs; never fill missing eligibility facts."""
    facts = {name: ProjectFact() for name in PROJECT_FACT_FIELDS}

    def record(name: str, value: Any, source: str, *, confirmation: bool = False) -> None:
        if value is not None and value != "" and value != []:
            facts[name] = ProjectFact(value, source, None, confirmation)

    metadata = getattr(script_analysis, "metadata", None)
    challenges = getattr(script_analysis, "challenges", None)

    raw_format = request_metadata.get("format")
    format_source = "intake.format"
    if not _clean_text(raw_format):
        raw_format = getattr(metadata, "format", None)
        format_source = "script_analysis.metadata.format"
    format_label = _clean_text(raw_format)
    format_token = canonical_format(format_label)
    # The legacy canonical format conflates form and technique. Preserve what
    # the label explicitly says, without inferring a feature from "animation"
    # or "documentary" alone.
    if format_label and format_label.casefold() == "animated feature":
        format_token = "feature"
    elif format_label and format_label.casefold() == "animation series":
        format_token = "tv_series"
    elif format_label and format_label.casefold() in {"animation", "documentary"}:
        format_token = None
    record(
        "format",
        format_token,
        format_source,
        confirmation=format_source.startswith("script_analysis"),
    )

    # Content form and technique are separate dimensions. Explicit intake
    # fields take precedence over information embedded in the intake format.
    raw_form = _clean_text(request_metadata.get("content_form"))
    raw_technique = _clean_text(request_metadata.get("technique"))
    record("content_form", raw_form.lower() if raw_form else None, "intake.content_form")
    record("technique", raw_technique.lower() if raw_technique else None, "intake.technique")
    if format_source == "intake.format" and format_label:
        label = format_label.casefold()
        if label in {"documentary", "docuseries"} and not raw_form:
            record("content_form", "documentary", "intake.format")
        if label in {"animated feature", "animation series", "animation"} and not raw_technique:
            record("technique", "animation", "intake.format")

    runtime = datasets.get("_runtime_minutes")
    if runtime is None:
        runtime = request_metadata.get("runtime_minutes")
    try:
        runtime = float(runtime) if runtime is not None and not isinstance(runtime, bool) else None
    except (TypeError, ValueError):
        runtime = None
    record(
        "runtime_minutes",
        runtime if runtime and runtime > 0 else None,
        "report_inputs.runtime_minutes",
    )

    genres = _clean_list(request_metadata.get("genre"))
    script_genres = _clean_list(getattr(metadata, "genres", None))
    record("declared_genres", genres, "intake.genre")
    record(
        "script_genres", script_genres, "script_analysis.metadata.genres",
        confirmation=True,
    )
    if genres:
        record("genres", genres, "intake.genre")
    else:
        record(
            "genres",
            script_genres,
            "script_analysis.metadata.genres",
            confirmation=True,
        )
    record(
        "tone",
        _clean_text(getattr(metadata, "tone", None)),
        "script_analysis.metadata.tone",
        confirmation=True,
    )
    record("themes", _clean_list(request_metadata.get("themes")), "intake.themes")
    record(
        "primary_languages",
        _clean_list(request_metadata.get("primary_languages") or request_metadata.get("language")),
        "intake.primary_languages",
    )
    record(
        "script_dialogue_languages",
        _clean_list(getattr(challenges, "languages", None)),
        "script_analysis.challenges.languages",
        confirmation=True,
    )

    home = _clean_text(
        request_metadata.get("production_country")
        or request_metadata.get("producer_country")
        or request_metadata.get("country")
    )
    co_producers = _clean_list(request_metadata.get("co_production_countries")) or []
    record(
        "production_countries",
        sorted(set(([home] if home else []) + co_producers)) or None,
        "intake.production_countries",
    )
    locations = getattr(script_analysis, "locations", None) or []
    story = _clean_list(
        [getattr(loc, "country", None) or getattr(loc, "territory", None) for loc in locations]
    )
    record("story_countries", story, "script_analysis.locations", confirmation=True)

    record(
        "target_audience",
        _clean_list(request_metadata.get("target_audience")),
        "intake.target_audience",
    )
    record(
        "target_sales_territories",
        _clean_list(request_metadata.get("target_sales_territories")),
        "intake.target_sales_territories",
    )
    record(
        "release_profile",
        _clean_list(request_metadata.get("release_profile")),
        "intake.release_profile",
    )
    record("stage", _clean_text(request_metadata.get("project_stage")), "intake.project_stage")
    budget = datasets.get("_budget_gbp")
    if isinstance(budget, dict):
        budget = budget.get("converted")
    try:
        budget = float(budget) if budget is not None and not isinstance(budget, bool) else None
    except (TypeError, ValueError):
        budget = None
    record("budget_gbp", budget if budget and budget > 0 else None, "report_inputs.budget_gbp")
    record(
        "completion_date",
        _clean_text(request_metadata.get("completion_date")),
        "intake.completion_date",
    )

    # These are not gathered by the current report intake. Do not convert absence
    # into false, zero, "none", or a screenplay-derived assumption.
    return ProjectDNA(facts)
