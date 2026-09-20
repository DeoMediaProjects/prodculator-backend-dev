"""Source-gated comparable and sales/distribution matching for the v2 report.

This is an isolated engine contract. Legacy distributor and TMDB rows are not
implicitly promoted into it, and no score is an acquisition or revenue claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from app.modules.reports.project_dna import ProjectDNA

CommercialStatus = Literal[
    "FIT_CONFIRMED", "POTENTIAL_FIT", "NOT_A_FIT", "NOT_ACTIONABLE"
]
RelationshipKind = Literal["COMPANY", "FESTIVAL", "MARKET_LAB_WIP"]


def _source_is_usable(source_url: str, verified_on: date, today: date) -> bool:
    if not isinstance(source_url, str):
        return False
    parsed = urlparse(source_url)
    return parsed.scheme == "https" and bool(parsed.netloc) and verified_on <= today


@dataclass(frozen=True)
class SourcedValue:
    value: Any
    source_url: str
    verified_on: date

    def known(self, today: date) -> bool:
        return (
            self.value is not None
            and self.value != ""
            and self.value != []
            and _source_is_usable(self.source_url, self.verified_on, today)
        )


@dataclass(frozen=True)
class ComparableRelationship:
    target_kind: RelationshipKind
    target_id: str
    relationship_type: str
    source_url: str
    verified_on: date

    def verified(self, today: date) -> bool:
        return bool(self.target_id and self.relationship_type) and _source_is_usable(
            self.source_url, self.verified_on, today
        )


@dataclass(frozen=True)
class ComparableProfile:
    id: str
    title: str
    source_url: str
    verified_on: date
    format: SourcedValue | None = None
    genres: SourcedValue | None = None
    tone: SourcedValue | None = None
    themes: SourcedValue | None = None
    content_form: SourcedValue | None = None
    budget_gbp: SourcedValue | None = None
    production_countries: SourcedValue | None = None
    primary_languages: SourcedValue | None = None
    target_audience: SourcedValue | None = None
    release_profile: SourcedValue | None = None
    relationships: tuple[ComparableRelationship, ...] = ()


@dataclass(frozen=True)
class ComparableMatch:
    profile: ComparableProfile
    score: int
    reasons: tuple[str, ...]
    unknown_dimensions: tuple[str, ...]
    verified_relationships: tuple[ComparableRelationship, ...]


@dataclass(frozen=True)
class ComparableStrategy:
    universe_count: int
    recommendations: tuple[ComparableMatch, ...]


@dataclass(frozen=True)
class CompanyProfile:
    id: str
    name: str
    role: SourcedValue | None = None
    active: SourcedValue | None = None
    formats: SourcedValue | None = None
    content_forms: SourcedValue | None = None
    techniques: SourcedValue | None = None
    acquisition_stages: SourcedValue | None = None
    minimum_budget_gbp: SourcedValue | None = None
    maximum_budget_gbp: SourcedValue | None = None
    production_origins: SourcedValue | None = None
    required_languages: SourcedValue | None = None
    rights_territories: SourcedValue | None = None
    genres: SourcedValue | None = None
    channels: SourcedValue | None = None
    rules_complete: bool = False


@dataclass(frozen=True)
class CompanyMatch:
    profile: CompanyProfile
    status: CommercialStatus
    score: int
    reasons: tuple[str, ...]
    conditions_to_confirm: tuple[str, ...]
    gate_results: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SalesDistributionStrategy:
    universe_count: int
    actionable_count: int
    recommendations: tuple[CompanyMatch, ...]


def _values(value: Any) -> set[str]:
    items = value if isinstance(value, (list, tuple, set)) else [value]
    return {str(item).strip().casefold() for item in items if item is not None and str(item).strip()}


def _shared(
    project: ProjectDNA, field: str, source: SourcedValue | None, today: date
) -> set[str] | None:
    fact = project.get(field)
    if fact.state == "UNKNOWN" or fact.confirmation_required or not source or not source.known(today):
        return None
    return _values(fact.value) & _values(source.value)


def match_comparables(
    profiles: list[ComparableProfile],
    project: ProjectDNA,
    *,
    today: date,
    festival_ids: set[str] | None = None,
    market_ids: set[str] | None = None,
    limit: int = 10,
) -> ComparableStrategy:
    festival_ids = festival_ids or set()
    market_ids = market_ids or set()
    matches = []
    for profile in profiles:
        if not profile.id or not profile.title or not _source_is_usable(
            profile.source_url, profile.verified_on, today
        ):
            continue
        known_format = profile.format and profile.format.known(today)
        project_format = project.get("format")
        if known_format and project_format.state == "KNOWN" and not project_format.confirmation_required:
            if not _shared(project, "format", profile.format, today):
                continue
        score = 0
        reasons = []
        unknown = []
        for field, label, weight in (
            ("format", "format", 4),
            ("genres", "genre", 2),
            ("tone", "tone", 2),
            ("themes", "themes", 2),
            ("content_form", "content form", 2),
            ("production_countries", "production origin", 1),
            ("primary_languages", "language", 1),
            ("target_audience", "audience", 1),
            ("release_profile", "release profile", 2),
        ):
            overlap = _shared(project, field, getattr(profile, field), today)
            if overlap is None:
                unknown.append(label)
            elif overlap:
                score += weight
                reasons.append(f"Shared {label}: {', '.join(sorted(overlap))}")
        budget = project.get("budget_gbp")
        if budget.state == "KNOWN" and not budget.confirmation_required and (
            profile.budget_gbp and profile.budget_gbp.known(today)
        ):
            try:
                ratio = float(profile.budget_gbp.value) / float(budget.value)
                if 1 / 3 <= ratio <= 3:
                    score += 2
                    reasons.append("Verified budget is within 3x of the project budget")
            except (TypeError, ValueError, ZeroDivisionError):
                unknown.append("budget")
        else:
            unknown.append("budget")
        relationships = tuple(r for r in profile.relationships if r.verified(today))
        if any(r.target_kind == "FESTIVAL" and r.target_id in festival_ids for r in relationships):
            score += 2
            reasons.append("Verified comparable festival trajectory intersects the project strategy")
        if any(r.target_kind == "MARKET_LAB_WIP" and r.target_id in market_ids for r in relationships):
            score += 2
            reasons.append("Verified comparable market pathway intersects the project strategy")
        # A title, genre label or large revenue figure alone is not a defensible
        # comparable. Require at least two independent sourced similarities.
        if len(reasons) < 2:
            continue
        matches.append(ComparableMatch(profile, score, tuple(reasons), tuple(unknown), relationships))
    matches.sort(key=lambda item: (-item.score, item.profile.title.casefold(), item.profile.id))
    return ComparableStrategy(len(profiles), tuple(matches[: max(0, limit)]))


def _gate(
    project: ProjectDNA,
    field: str,
    claim: SourcedValue | None,
    today: date,
    label: str,
) -> tuple[str, str]:
    if claim and claim.known(today) and _values(claim.value) & {"all", "global", "worldwide"}:
        return label, "PASS"
    overlap = _shared(project, field, claim, today)
    return label, "UNKNOWN" if overlap is None else "PASS" if overlap else "FAIL"


def match_company(
    company: CompanyProfile,
    project: ProjectDNA,
    *,
    today: date,
    comparables: ComparableStrategy | None = None,
) -> CompanyMatch:
    if (
        not company.id
        or not company.name
        or not company.active
        or not company.active.known(today)
        or company.active.value is not True
        or not company.role
        or not company.role.known(today)
        or not _values(company.role.value) <= {"sales_agent", "distributor"}
    ):
        return CompanyMatch(company, "NOT_ACTIONABLE", 0, (), (), ())
    gates = []
    for field, claim, label in (
        ("format", company.formats, "acquisition format"),
        ("content_form", company.content_forms, "content form"),
        ("technique", company.techniques, "technique"),
        ("stage", company.acquisition_stages, "acquisition stage"),
        ("production_countries", company.production_origins, "production origin"),
        ("primary_languages", company.required_languages, "required language"),
        ("target_sales_territories", company.rights_territories, "rights territory"),
    ):
        if claim is not None:
            gates.append(_gate(project, field, claim, today, label))
    budget = project.get("budget_gbp")
    for claim, label, compare in (
        (company.minimum_budget_gbp, "minimum budget", lambda a, b: a >= b),
        (company.maximum_budget_gbp, "maximum budget", lambda a, b: a <= b),
    ):
        if claim is None:
            continue
        result = "UNKNOWN"
        if claim.known(today) and budget.state == "KNOWN" and not budget.confirmation_required:
            try:
                result = "PASS" if compare(float(budget.value), float(claim.value)) else "FAIL"
            except (TypeError, ValueError):
                pass
        gates.append((label, result))
    if any(result == "FAIL" for _, result in gates):
        return CompanyMatch(company, "NOT_A_FIT", 0, (), (), tuple(gates))

    conditions = [label for label, result in gates if result == "UNKNOWN"]
    for claim, label in (
        (company.formats, "acquisition format"),
        (company.acquisition_stages, "acquisition stage"),
        (company.rights_territories, "rights territory"),
    ):
        if claim is None:
            conditions.append(f"{label} scope not verified")
    if not company.rules_complete:
        conditions.append("Material acquisition rules require source review")
    if not gates:
        conditions.append("No acquisition hard gates have been verified")
    status: CommercialStatus = "POTENTIAL_FIT" if conditions else "FIT_CONFIRMED"
    reasons = []
    score = 0
    for field, claim, label, points in (
        ("genres", company.genres, "genre", 2),
        ("production_countries", company.production_origins, "production origin", 1),
        ("release_profile", company.channels, "release channel", 2),
    ):
        overlap = _shared(project, field, claim, today)
        if overlap:
            score += points
            reasons.append(f"Verified {label} fit: {', '.join(sorted(overlap))}")
    if comparables:
        handled = [
            item.profile.title
            for item in comparables.recommendations
            if any(
                r.target_kind == "COMPANY" and r.target_id == company.id
                for r in item.verified_relationships
            )
        ]
        if handled:
            score += min(len(handled) * 3, 9)
            reasons.append(f"Verified comparable title history: {', '.join(handled[:3])}")
    if not reasons and not any(result == "PASS" for _, result in gates):
        return CompanyMatch(company, "NOT_ACTIONABLE", 0, (), tuple(conditions), tuple(gates))
    return CompanyMatch(company, status, score, tuple(reasons), tuple(conditions), tuple(gates))


def build_sales_distribution_strategy(
    companies: list[CompanyProfile],
    project: ProjectDNA,
    *,
    package: str,
    today: date,
    comparables: ComparableStrategy | None = None,
) -> SalesDistributionStrategy:
    evaluated = [match_company(company, project, today=today, comparables=comparables) for company in companies]
    rankable = [item for item in evaluated if item.status in {"FIT_CONFIRMED", "POTENTIAL_FIT"}]
    rankable.sort(key=lambda item: (
        item.status != "FIT_CONFIRMED",
        -item.score,
        len(item.conditions_to_confirm),
        item.profile.name.casefold(),
    ))
    entitlement = 10 if package.strip().lower() in {"producer", "studio"} else 5
    return SalesDistributionStrategy(
        universe_count=len(companies),
        actionable_count=sum(item.status != "NOT_ACTIONABLE" for item in evaluated),
        recommendations=tuple(rankable[:entitlement]),
    )
