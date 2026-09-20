"""Source-gated comparable and sales/distribution matching for the v2 report.

This is an isolated engine contract. Legacy distributor and TMDB rows are not
implicitly promoted into it, and no score is an acquisition or revenue claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from app.modules.reports.commercial_freeze import ACCESS_UNKNOWN
from app.modules.reports.commercial_scoring import (
    COMPONENT_WEIGHTS,
    NOT_SUITABLE,
    ComponentScore,
    StrategicFit,
    access_component,
    overlap_component,
    resolve_match_state,
    score_strategic_fit,
    select_portfolio,
)
from app.modules.reports.project_dna import ProjectDNA

#: The canonical match states, from the implementation note's section 9. The
#: engine-local FIT_CONFIRMED / POTENTIAL_FIT / NOT_A_FIT vocabulary this module
#: used to carry was a third dialect for the same question, and a report showing
#: one of its words beside another section's would have been describing the same
#: state in two languages.
CommercialStatus = Literal[
    "STRATEGIC_MATCH",
    "POTENTIAL_MATCH_NEEDS_CONFIRMATION",
    "ACCESS_ROUTE_UNKNOWN",
    "NOT_SUITABLE",
]
RelationshipKind = Literal["COMPANY", "FESTIVAL", "MARKET_LAB_WIP"]

#: The two roles a comparable can play, from the implementation note's section
#: 10. They are kept apart because their evidence is different and their
#: guardrails are opposite.
#:
#: A PRODUCTION comparable is evidence about making the film: territory, scale,
#: incentive route, crew and location profile. It says nothing about who bought
#: the film, and the note is explicit that it must not imply similar buyer
#: interest or commercial performance.
#:
#: A COMMERCIAL comparable is evidence about selling it: genre, tone, audience,
#: release trajectory and — decisively — sourced company-title relationships.
#: Only this role may support a sales or distribution recommendation.
#:
#: A title can hold both roles. It holds them because each was established
#: separately, never because holding one implies the other.
PRODUCTION_COMPARABLE = "PRODUCTION"
COMMERCIAL_COMPARABLE = "COMMERCIAL"
ComparableRole = Literal["PRODUCTION", "COMMERCIAL"]

#: Fields that evidence each role. A comparable earns a role by carrying sourced
#: values in that role's dimensions, so the role is derived from the evidence
#: rather than asserted alongside it.
_PRODUCTION_DIMENSIONS: tuple[str, ...] = (
    "format",
    "budget_gbp",
    "production_countries",
)
_COMMERCIAL_DIMENSIONS: tuple[str, ...] = (
    "genres",
    "tone",
    "themes",
    "target_audience",
    "release_profile",
)


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
    #: Which roles this title's sourced evidence actually supports. Section 10
    #: states each comparable's roles, so a reader can see that a title offered
    #: as a production analogue is not also being offered as buyer evidence.
    roles: tuple[str, ...] = ()

    @property
    def supports_commercial_evidence(self) -> bool:
        """Whether this title may back a sales or distribution recommendation.

        The note's guardrail in one property: production similarity alone can
        never create commercial buyer evidence. A company match reads this
        rather than re-deriving it, so the rule lives in one place.
        """
        return COMMERCIAL_COMPARABLE in self.roles


@dataclass(frozen=True)
class ComparableStrategy:
    universe_count: int
    recommendations: tuple[ComparableMatch, ...]
    #: The one input state this result was computed against. Refused rather
    #: than merged when it differs from the report's own snapshot.
    projectfacts_snapshot_id: str | None = None
    projectfacts_version: str | None = None


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
    #: Canonical access route, from ``commercial_freeze.normalise_access_route``.
    #: Defaults to unknown rather than to anything permissive: a company whose
    #: route nobody established is not thereby approachable.
    access_route: str = ACCESS_UNKNOWN
    #: The key deciding whether two companies compete for one package slot. A
    #: parent and its own label are one commercial route, and a list holding
    #: both has spent two of a producer's five slots reaching the same company.
    portfolio_group: str | None = None


@dataclass(frozen=True)
class CompanyMatch:
    profile: CompanyProfile
    status: CommercialStatus
    #: The full frozen breakdown, not just its total. A 40 built from eight
    #: known components and a 40 built from two are different claims, and a
    #: report showing only the number cannot tell them apart.
    fit: StrategicFit
    reasons: tuple[str, ...]
    conditions_to_confirm: tuple[str, ...]
    gate_results: tuple[tuple[str, str], ...]

    @property
    def score(self) -> int:
        """The 100-point total. Kept so existing readers need no change."""
        return self.fit.score


@dataclass(frozen=True)
class SalesDistributionStrategy:
    universe_count: int
    actionable_count: int
    recommendations: tuple[CompanyMatch, ...]
    projectfacts_snapshot_id: str | None = None
    projectfacts_version: str | None = None


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


def _roles_for(
    profile: ComparableProfile,
    relationships: tuple[ComparableRelationship, ...],
    today: date,
) -> tuple[str, ...]:
    """Which roles this title's sourced evidence supports.

    Derived from the evidence rather than asserted alongside it, so a title
    cannot be labelled a commercial comparable by anyone who did not first
    source the dimensions that make it one.

    A verified company relationship confers the commercial role on its own. It
    is the strongest evidence the note recognises — this company handled this
    film — and a title carrying one is commercial evidence whether or not its
    tone and audience were also recorded.
    """
    roles: list[str] = []
    if any(
        getattr(profile, name) and getattr(profile, name).known(today)
        for name in _PRODUCTION_DIMENSIONS
    ):
        roles.append(PRODUCTION_COMPARABLE)
    if any(r.target_kind == "COMPANY" for r in relationships) or any(
        getattr(profile, name) and getattr(profile, name).known(today)
        for name in _COMMERCIAL_DIMENSIONS
    ):
        roles.append(COMMERCIAL_COMPARABLE)
    return tuple(roles)


def match_comparables(
    profiles: list[ComparableProfile],
    project: ProjectDNA,
    *,
    today: date,
    festival_ids: set[str] | None = None,
    market_ids: set[str] | None = None,
    limit: int = 10,
    projectfacts_snapshot_id: str | None = None,
    projectfacts_version: str | None = None,
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
        matches.append(
            ComparableMatch(
                profile,
                score,
                tuple(reasons),
                tuple(unknown),
                relationships,
                roles=_roles_for(profile, relationships, today),
            )
        )
    matches.sort(key=lambda item: (-item.score, item.profile.title.casefold(), item.profile.id))
    return ComparableStrategy(
        len(profiles),
        tuple(matches[: max(0, limit)]),
        projectfacts_snapshot_id=projectfacts_snapshot_id,
        projectfacts_version=projectfacts_version,
    )


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
    festival_ids: set[str] | None = None,
    market_ids: set[str] | None = None,
) -> CompanyMatch:
    """One company's hard gates, then the frozen 100-point strategic fit.

    Gates first and scoring second, never the reverse: a company that cannot
    acquire this production is not made suitable by scoring well. And a high
    score is a modelled strategic fit, not an acquisition probability — the
    implementation note says so twice, and nothing here converts one into the
    other.

    The score is ``commercial_scoring``'s, not this module's. There was a second
    scorer here, a hand-tuned tally of two points for genre and three per
    comparable capped at nine, and two scorers in one codebase drift until the
    number a report shows depends on which one the caller reached for.
    """
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
        return CompanyMatch(
            company, NOT_SUITABLE, score_strategic_fit({}), (), (), ()
        )

    # ── Hard gates, before any scoring ───────────────────────────────────────
    gates: list[tuple[str, str]] = []
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
        if (
            claim.known(today)
            and budget.state == "KNOWN"
            and not budget.confirmation_required
        ):
            try:
                result = (
                    "PASS" if compare(float(budget.value), float(claim.value)) else "FAIL"
                )
            except (TypeError, ValueError):
                pass
        gates.append((label, result))

    hard_gate_failed = any(result == "FAIL" for _, result in gates)

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

    fit = score_strategic_fit(
        _score_components(
            company,
            project,
            today=today,
            comparables=comparables,
            festival_ids=festival_ids or set(),
            market_ids=market_ids or set(),
        )
    )
    state = resolve_match_state(
        fit,
        access_route=company.access_route,
        hard_gate_failed=hard_gate_failed,
        conditions_to_confirm=conditions,
    )
    return CompanyMatch(
        profile=company,
        status=state,
        fit=fit,
        reasons=fit.evidence,
        conditions_to_confirm=tuple(conditions),
        gate_results=tuple(gates),
    )


def _project_values(project: ProjectDNA, field: str) -> set[str] | None:
    """A project fact as comparable values, or None when it is not established.

    A fact needing confirmation reads as unknown. It may well be true, and the
    engine has not been told that it is, which is the same thing to a score.
    """
    fact = project.get(field)
    if fact.state == "UNKNOWN" or fact.confirmation_required:
        return None
    return _values(fact.value)


def _claim_values(claim: SourcedValue | None, today: date) -> set[str] | None:
    if claim is None or not claim.known(today):
        return None
    return _values(claim.value)


def _score_components(
    company: CompanyProfile,
    project: ProjectDNA,
    *,
    today: date,
    comparables: ComparableStrategy | None,
    festival_ids: set[str],
    market_ids: set[str],
) -> dict[str, ComponentScore]:
    """The eight frozen components, each unknown unless both sides are sourced."""
    components: dict[str, ComponentScore] = {}

    for key, project_field, claim, prefix in (
        ("genre_content_audience_fit", "genres", company.genres, "Verified genre fit"),
        ("format_scale_fit", "format", company.formats, "Verified format fit"),
        (
            "lifecycle_stage_fit",
            "stage",
            company.acquisition_stages,
            "Verified acquisition stage fit",
        ),
    ):
        components[key] = overlap_component(
            key,
            _project_values(project, project_field),
            _claim_values(claim, today),
            evidence_prefix=prefix,
        )

    # Territory is scored apart from the loop because a worldwide rights scope
    # is a full fit rather than a non-overlap. The hard gate already treats it
    # that way; scoring it as zero because the literal string "worldwide" does
    # not match "Kenya" would penalise the broadest company in the catalogue for
    # being broad.
    territory_claim = _claim_values(company.rights_territories, today)
    if territory_claim and territory_claim & {"all", "global", "worldwide"}:
        components["territory_rights_fit"] = ComponentScore(
            "territory_rights_fit",
            COMPONENT_WEIGHTS["territory_rights_fit"],
            1.0,
            ("Verified worldwide rights scope",),
        )
    else:
        components["territory_rights_fit"] = overlap_component(
            "territory_rights_fit",
            _project_values(project, "target_sales_territories"),
            territory_claim,
            evidence_prefix="Verified rights territory fit",
        )

    components["access_path_quality"] = access_component(company.access_route)

    # ── Evidence drawn only from sourced, typed relationships ────────────────
    # A company's history with a comparable title is the strongest single signal
    # the contract recognises, at 25 of 100. It is also the easiest to fake by
    # inference, so only a verified typed relationship counts: a production
    # similarity between two films says nothing about who sold either one.
    handled: list[str] = []
    intersecting = False
    if comparables is not None:
        for item in comparables.recommendations:
            # Section 10's guardrail, enforced rather than described: a title
            # that is only a production analogue cannot become buyer evidence.
            # Similar budget, territory and scale say nothing about who bought
            # a film, and letting them through here is how production
            # similarity turns into a commercial claim nobody sourced.
            if not item.supports_commercial_evidence:
                continue
            for relationship in item.verified_relationships:
                if (
                    relationship.target_kind == "COMPANY"
                    and relationship.target_id == company.id
                ):
                    handled.append(item.profile.title)
                elif (
                    relationship.target_kind == "FESTIVAL"
                    and relationship.target_id in festival_ids
                ) or (
                    relationship.target_kind == "MARKET_LAB_WIP"
                    and relationship.target_id in market_ids
                ):
                    intersecting = True

    if comparables is None:
        # No comparable layer ran, so these are unknown rather than zero. The
        # distinction costs this company nothing in points either way and is the
        # difference between "no history found" and "we did not look".
        for key in ("commercial_comparable_evidence", "slate_similarity"):
            components[key] = ComponentScore(key, COMPONENT_WEIGHTS[key], None)
    else:
        # Three sourced titles is treated as a full evidence base. The cap stops
        # a company with a large catalogue crowding out one with a closer but
        # smaller history, which would be volume standing in for relevance.
        components["commercial_comparable_evidence"] = ComponentScore(
            "commercial_comparable_evidence",
            COMPONENT_WEIGHTS["commercial_comparable_evidence"],
            min(len(handled) / 3, 1.0),
            (f"Verified comparable title history: {', '.join(sorted(handled)[:3])}",)
            if handled
            else (),
        )
        components["slate_similarity"] = ComponentScore(
            "slate_similarity",
            COMPONENT_WEIGHTS["slate_similarity"],
            min(len(handled) / 2, 1.0),
            (f"{len(handled)} sourced titles in this company's recent slate",)
            if handled
            else (),
        )

    if comparables is None or not (festival_ids or market_ids):
        components["festival_market_intersection"] = ComponentScore(
            "festival_market_intersection",
            COMPONENT_WEIGHTS["festival_market_intersection"],
            None,
        )
    else:
        components["festival_market_intersection"] = ComponentScore(
            "festival_market_intersection",
            COMPONENT_WEIGHTS["festival_market_intersection"],
            1.0 if intersecting else 0.0,
            ("Verified festival or market intersection with a comparable title",)
            if intersecting
            else (),
        )

    return components


def build_sales_distribution_strategy(
    companies: list[CompanyProfile],
    project: ProjectDNA,
    *,
    package: str,
    today: date,
    comparables: ComparableStrategy | None = None,
    festival_ids: set[str] | None = None,
    market_ids: set[str] | None = None,
    projectfacts_snapshot_id: str | None = None,
    projectfacts_version: str | None = None,
) -> SalesDistributionStrategy:
    """Rank the full universe, then apply package depth, dedupe and diversify.

    Depth is applied last and only to display. The same universe and the same
    ranking run for every package, so a cheaper tier gets fewer names rather
    than different ones.
    """
    evaluated = [
        match_company(
            company,
            project,
            today=today,
            comparables=comparables,
            festival_ids=festival_ids,
            market_ids=market_ids,
        )
        for company in companies
    ]
    entitlement = 10 if str(package).strip().lower() in {"producer", "studio"} else 5
    selected = select_portfolio(
        evaluated,
        entitlement=entitlement,
        state_of=lambda item: item.status,
        score_of=lambda item: item.fit.score,
        group_of=lambda item: item.profile.portfolio_group or item.profile.id,
        route_of=lambda item: item.profile.access_route,
        name_of=lambda item: item.profile.name,
    )
    return SalesDistributionStrategy(
        universe_count=len(companies),
        actionable_count=sum(item.status != NOT_SUITABLE for item in evaluated),
        recommendations=selected,
        projectfacts_snapshot_id=projectfacts_snapshot_id,
        projectfacts_version=projectfacts_version,
    )
