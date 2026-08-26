from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

from app.core.territories import resolve_territory
from app.modules.incentives.v2_contracts import (
    CANONICAL_INPUTS,
    is_comparison_mode,
    reconcile_allocations,
)
from app.modules.incentives.v2_jurisdictions import (
    UnknownJurisdiction,
    resolve_jurisdiction,
)


# --- Input Schemas ---


class ScenarioCalculationInput(BaseModel):
    """One producer-supplied statutory cost base, with its provenance.

    ``amount`` is deliberately optional and nullable. Null means the producer has
    not supplied it; zero means they have told us it is nil. The two lead to
    different calculation statuses, so collapsing them would defeat the rule the
    v2 rebuild turns on.
    """

    input_key: str
    amount: float | None = None
    currency: str | None = None
    input_status: Literal["known", "planning_assumption", "unknown"] = "unknown"
    input_source: Literal[
        "user_entered", "imported_budget", "verified_cost_report"
    ] | None = None
    programme_id: str | None = None
    notes: str | None = None

    @field_validator("input_key")
    @classmethod
    def known_canonical_key(cls, v: str) -> str:
        """Reject an unknown input key.

        The registry is deliberately generic so a new programme reuses an existing
        key. Accepting an arbitrary key would let a caller invent a statutory base
        the engine has no rule for, which is a silent no-op rather than an error.
        """
        if v not in CANONICAL_INPUTS:
            raise ValueError(
                f"{v!r} is not a canonical statutory input. Permitted keys: "
                + ", ".join(sorted(CANONICAL_INPUTS))
            )
        return v

    @field_validator("amount")
    @classmethod
    def not_negative(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("A statutory cost base cannot be negative")
        return v

    @model_validator(mode="after")
    def provenance_matches_the_amount(self) -> "ScenarioCalculationInput":
        """An amount without provenance, or provenance without an amount, is a
        contract error rather than something to guess at."""
        if self.amount is None and self.input_status != "unknown":
            raise ValueError(
                "input_status must be 'unknown' when no amount is supplied; a "
                "status of known or planning_assumption asserts a figure exists"
            )
        if self.amount is not None and self.input_status == "unknown":
            raise ValueError(
                "An amount was supplied with input_status 'unknown'. Say whether "
                "it is 'known' or a 'planning_assumption', because the difference "
                "decides whether the result is estimated or conditional"
            )
        return self


class TerritoryScenario(BaseModel):
    """One alternative territory the producer wants compared.

    Scenarios are alternatives, not allocations. They are not summed and do not
    need to total the production budget, which is why ``scenario_spend`` is
    validated against nothing but itself.
    """

    scenario_id: str | None = None
    #: Accepts a label, an ISO code or a subdivision code; normalised on write.
    territory: str
    territory_id: str | None = None
    subdivision_id: str | None = None
    #: Nullable on purpose. A territory selected with no spend entered yet is a
    #: real state, and defaulting it to zero or to the budget is the substitution
    #: this rebuild exists to remove.
    scenario_spend: float | None = None
    scenario_currency: str | None = None
    scenario_spend_source: Literal[
        "user_entered", "imported_budget", "unknown"
    ] = "unknown"
    #: Co-production only. The agreed share of the production this partner
    #: represents. Meaningless in comparison mode, where territories are
    #: alternatives rather than partners, so it is rejected there.
    participation_percent: float | None = None
    #: Co-production only. Neither value implies competent-authority approval,
    #: which the treaty layer tracks separately.
    partner_status: Literal["candidate", "confirmed"] | None = None
    calculation_inputs: list[ScenarioCalculationInput] = []

    @field_validator("scenario_spend")
    @classmethod
    def not_negative(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("scenario_spend cannot be negative")
        return v

    @model_validator(mode="after")
    def canonical_jurisdiction(self) -> "TerritoryScenario":
        """Resolve to canonical IDs, rejecting anything unrecognised.

        The existing country validator passes unrecognised input through as free
        text. That is safe for prose and unsafe here: once the key selects a
        financial formula, a typo becomes a scenario that matches no programme and
        the report shows a territory with no analysis and no explanation.
        """
        try:
            jurisdiction = resolve_jurisdiction(self.territory)
        except UnknownJurisdiction as exc:
            raise ValueError(str(exc)) from None
        object.__setattr__(self, "territory_id", jurisdiction.territory_id)
        object.__setattr__(self, "subdivision_id", jurisdiction.subdivision_id)

        if self.participation_percent is not None and not (
            0 <= self.participation_percent <= 100
        ):
            raise ValueError(
                "participation_percent is a share of one production and must be "
                "between 0 and 100"
            )

        keys = [i.input_key for i in self.calculation_inputs]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(
                "A scenario cannot supply the same statutory input twice: "
                + ", ".join(sorted(duplicates))
            )
        return self


class CreateReportRequest(BaseModel):
    script_title: str
    report_type: Literal["preview", "paid", "b2b"] = "paid"
    # script_file_path is no longer used — the script file is uploaded as multipart
    # and text is extracted in-memory, never stored.
    script_file_path: str | None = None

    # Project metadata (required)
    genre: list[str]
    budget_amount: float  # Actual budget figure (replaces budget_range in v3)
    budget_currency: Literal[
        "GBP", "USD", "EUR", "ZAR", "CAD", "AUD", "NGN",
        "HUF", "CZK", "MAD", "NZD", "RON", "RSD",
        # Added: FX rates for these already exist in fx.service fallback table.
        "ISK", "JPY", "KRW", "SGD",
        # Added once live FX (EXCHANGE_RATE_API_KEY) is configured — these
        # convert via the live API. NOTE: they have no hardcoded offline
        # fallback rate, so if the FX API is unavailable they convert at 1:1.
        # Add sourced GBP-base fallback rates in fx.service to harden that path.
        "INR", "MXN", "BRL",
        "OTHER",
    ] = "GBP"
    # Intake contract (intake_schema.json) labels first; the longer tail is
    # retained for backward compatibility with reports already created.
    format: Literal[
        "Feature Film",
        "TV Series",
        "TV Pilot",
        "Limited Series",
        "Short",
        "Documentary",
        "Animated Feature",
        # Legacy labels (pre-contract) — still accepted, canonicalised on write
        "Short Film",
        "Mini-Series",
        "Docuseries",
        "Animation",
        "Animation Series",
        "Commercial",
        "Music Video",
        "Interactive",
        "VR",
    ]
    country: str  # Validated & normalised to canonical label by validator below
    # Optional (removed from the intake form as redundant with
    # territories_considering). Absent → the engine treats it as "open".
    location_strategy: Literal["domestic", "open", "international"] | None = None
    production_priority: Literal["incentive", "full", "location"] = "full"

    # Email gate (required for preview reports from unauthenticated users)
    email: str | None = None

    # Conditional / optional metadata
    state_province: str | None = None
    territories_considering: list[str] | None = None
    filming_start_date: str | None = None
    filming_duration: int | None = None
    # Expected completion date (intake contract: drives the festival matcher's
    # timing window; also stored as the signal's completion_window month).
    completion_date: str | None = None
    #: v2 scenario ingestion. One alternative territory per compared jurisdiction,
    #: each with its own expected spend and any statutory cost bases the producer
    #: chose to supply. Authoritative for v2 calculations;
    #: ``territories_considering`` remains read-only legacy compatibility.
    #:
    #: Empty is valid. A report generates without scenarios; what changes is that
    #: no scenario-driven calculation can run, which downgrades a status rather
    #: than blocking the wider report.
    territory_scenarios: list[TerritoryScenario] = []

    #: How the selected territories relate to one another. Not presentation: it
    #: changes whether spends are alternatives or allocations, whether they
    #: reconcile to the budget, and whether the report may rank them against each
    #: other. Defaults to comparison, which is the existing behaviour.
    production_structure_mode: Literal[
        "comparison", "coproduction", "undecided"
    ] = "comparison"
    #: Co-production only. Spend belonging to the structure but not attributed to
    #: a partner territory, so allocations can reconcile without forcing the
    #: producer to invent a territory for the remainder.
    unallocated_spend: float | None = None
    #: Co-production only, and declared rather than determined. A route named here
    #: is the producer's intent; whether a treaty applies is the treaty layer's
    #: answer and competent-authority approval is a further step again.
    co_production_route: str | None = None
    #: Co-production only. Whether to surface selective supranational support.
    #: Never a rebate and never committed finance.
    supranational_support_interest: Literal[
        "show_opportunity", "not_considering", "application_planned"
    ] | None = None

    # Hard territory constraint declared by the producer ("Must Film In").
    must_film_in: str | None = None
    # Treaty co-production openness (yes/no/undecided per the intake contract).
    co_production_interest: Literal["yes", "no", "undecided"] | None = None
    # Primary spoken languages (max 5 per contract; free-text entries today).
    primary_languages: list[str] | None = None
    # TV series episode metadata — used for UK AVEC HETV threshold verification
    total_episodes: int | None = None
    episode_runtime_minutes: int | None = None
    camera_equipment: list[str] | None = None
    crew_size: int | None = None
    principal_cast: int | None = None
    supporting_cast: int | None = None
    # Declared audience (handoff §4): target_audience = checked age quadrants
    # (kids_family / under_25 / adults_25_plus); audience_segments = declared
    # segments such as lgbtq_audience; audience_skew = stored for B2B, never
    # scored. All declared-only — never inferred from genre.
    target_audience: str | list[str] | None = None
    audience_segments: list[str] | None = None
    audience_skew: Literal["female_leaning", "male_leaning", "balanced"] | None = None
    # Representation — strict opt-in; drives representation-focused festival /
    # distributor matching only when the user filled these in.
    representation_gender: str | None = None
    representation_minority: list[str] | None = None
    language: str | None = None

    # Business Intelligence consent (CRIT-2) — explicit opt-in captured at intake.
    # Defaults to False: without it the report path never persists a production
    # signal for aggregation, and a prior consented signal for the same script is
    # removed (consent withdrawal). Checkbox copy on the frontend is a marked
    # placeholder until the solicitor wording arrives.
    b2b_consent: bool = False
    #: True when the production format is one whose incentive eligibility the
    #: programme data does not record (today: short films) and the producer
    #: confirmed at intake that they understand the rebate figures assume it.
    #: Stored with the request because the report carries the same caveat, so the
    #: acknowledgement and the disclosure can be evidenced together.
    format_eligibility_acknowledged: bool | None = None

    # Producer eligibility (for nationality / co-production checks)
    producer_country: str | None = None  # Jurisdiction of production company (ISO code, e.g. "GB")
    co_production_status: Literal[
        "sole_producer",
        "co_production_treaty",
        "co_production_informal",
        "undecided",
    ] | None = None

    @field_validator("budget_amount", mode="before")
    @classmethod
    def validate_budget_amount(cls, v: float) -> float:
        if v is not None and v <= 0:
            raise ValueError("budget_amount must be greater than 0")
        return v

    @field_validator("country", mode="before")
    @classmethod
    def normalise_country(cls, v: str) -> str:
        """Accept frontend short-forms (UK, USA, Canada) and normalise to
        the canonical Territory label used throughout the backend."""
        if not v:
            return v
        t = resolve_territory(v)
        if t is not None:
            # If sub-territory, return the parent country label
            if t.is_sub_territory and t.parent is not None:
                return t.parent.label
            return t.label
        # Allow "Other" pass-through for the catch-all option
        if v.strip().lower() == "other":
            return "Other"
        return v  # fall through — let it go; AI can still work with freeform

    @field_validator("territories_considering", mode="before")
    @classmethod
    def normalise_territories(cls, v: list[str] | None) -> list[str] | None:
        """Normalise each territory string to the canonical label.

        Left permissive deliberately. From v2 this field is legacy compatibility
        only and is not authoritative for a calculation, so an unrecognised entry
        may still travel through for creative and location analysis. The
        authoritative path is ``territory_scenarios``, which rejects it.
        """
        if not v:
            return v
        result: list[str] = []
        for raw in v:
            t = resolve_territory(raw)
            result.append(t.label if t else raw)
        return result

    @model_validator(mode="after")
    def structure_mode_matches_the_scenarios(self) -> "CreateReportRequest":
        """Reject co-production fields in comparison mode, and vice versa.

        The two modes mean different things by the same number. In comparison
        mode ``scenario_spend`` is one alternative and a participation share is
        meaningless; in co-production mode it is an allocation within a single
        structure. Accepting a participation share alongside alternatives would
        leave a report that cannot say which reading applies.
        """
        comparison = is_comparison_mode(self.production_structure_mode)
        if comparison:
            offenders = [
                s.territory for s in self.territory_scenarios
                if s.participation_percent is not None or s.partner_status is not None
            ]
            if offenders:
                raise ValueError(
                    "participation_percent and partner_status describe partners in "
                    "one production. In "
                    f"{self.production_structure_mode} mode the territories are "
                    "alternatives, so they do not apply to: "
                    + ", ".join(offenders)
                )
            for field in ("unallocated_spend", "co_production_route",
                          "supranational_support_interest"):
                if getattr(self, field) is not None:
                    raise ValueError(
                        f"{field} belongs to a co-production structure and has no "
                        f"meaning in {self.production_structure_mode} mode"
                    )
        if self.unallocated_spend is not None and self.unallocated_spend < 0:
            raise ValueError("unallocated_spend cannot be negative")
        return self

    @property
    def allocation_reconciliation(self) -> tuple[str, float | None]:
        """Whether co-production allocations account for the budget.

        Reported rather than enforced. A producer part-way through entering a
        structure is legitimately under-allocated, and an over-allocation may be a
        currency difference. Blocking either would stop them working.
        """
        if is_comparison_mode(self.production_structure_mode):
            return "not_assessable", None
        return reconcile_allocations(
            self.budget_amount,
            [s.scenario_spend for s in self.territory_scenarios],
            self.unallocated_spend,
        )

    @model_validator(mode="after")
    def scenarios_are_unique_per_jurisdiction(self) -> "CreateReportRequest":
        """One scenario per jurisdiction.

        Two scenarios for the same territory would give one project two competing
        answers for the same programme, with nothing to choose between them.
        """
        if not self.territory_scenarios:
            return self
        keys = [
            s.subdivision_id or s.territory_id for s in self.territory_scenarios
        ]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(
                "Each jurisdiction may appear once in territory_scenarios. "
                "Duplicated: " + ", ".join(sorted(str(d) for d in duplicates))
            )
        return self


# --- Output Schemas (ScriptAnalysis interface) ---


class LocationRanking(BaseModel):
    name: str
    country: str
    score: int  # 0-100
    costEfficiency: int  # 0-100
    crewDepth: int  # 0-100
    infrastructure: int  # 0-100
    # None means "not scored": the project's eligibility for this territory's
    # programme is unresolved, so the dimension is neutral in the weighted total
    # rather than crediting the territory with a rebate nobody has confirmed it can
    # claim. Zero is a different statement — a researched exclusion, no rebate here.
    incentiveStrength: int | None  # 0-100, or None when not scored
    currencyAdvantage: int  # 0-100
    reasoning: list[str]  # 3-5 bullet points
    isAssessmentOnly: bool | None = None
    rebatePercent: str | None = None
    rebateAmount: str | None = None
    culturalTestLikelihood: str | None = None
    adminComplexity: str | None = None
    paymentSpeed: str | None = None
    #: Canonical window from ``resolve_payment_timing``; ``paymentSpeed`` is
    #: its rendered label. Every section reads this one object.
    paymentTiming: dict[str, Any] | None = None
    keyAdvantages: list[str] | None = None
    keyRisks: list[str] | None = None
    # v3 scoring dimensions
    incentiveReliability: int | None = None   # 0-100 (new 6th dimension)
    crewDepthTier: Literal["Established", "Growing", "Emerging"] | str | None = None
    infrastructureTier: Literal["Established", "Growing", "Emerging"] | str | None = None
    bankabilityLabel: Literal["BANKABLE", "VERIFY FIRST", "NOT BANKABLE"] | None = None
    # Weather-schedule integration (populated by ReportValidator)
    weatherRiskImpact: int | None = None  # negative score deduction from weather risk
    # Enriched data-integrity fields (populated by ReportValidator)
    paymentTimelineSource: str | None = None  # source_name from incentive dataset
    incentiveSource: str | None = None        # source_name from incentive dataset
    dataFreshnessDays: int | None = None      # days since last_verified_at


class IncentiveEstimate(BaseModel):
    territory: str
    program: str
    rate: str
    cap: str
    qualifyingSpend: str
    estimatedRebate: str
    requirements: list[str]
    disclaimer: str = "Estimate only. Final eligibility depends on official approval."
    dataSource: str = "Prodculator admin database"
    lastUpdated: str
    # Regional stacking fields
    scope: Literal["national", "regional", "municipal"] | None = None
    parentTerritory: str | None = None
    stackableWith: list[str] | None = None
    stackingNote: str | None = None
    # Producer eligibility fields
    eligibilityStatus: Literal[
        "qualified", "requires_co_production", "requires_spv", "ineligible", "unknown"
    ] | None = None
    eligibilityNote: str | None = None
    # Set when the engine ruled the territory's headline programme out at this
    # budget and modelled a different one (PROD-FIX-007). Explains which
    # programme the figures describe and why it changed.
    programmeNote: str | None = None
    # Enriched data-integrity fields
    paymentSpeed: str | None = None           # payment_timeline_notes from dataset
    #: Per-programme format eligibility from ``evaluate_format_eligibility``:
    #: verdict, label, whitelist, condition, source and verified date. Rendered by
    #: the web report and the PDF from this one object so they cannot disagree.
    formatEligibility: dict[str, Any] | None = None
    #: False when the verdict is unverified or needs confirmation, so no surface
    #: presents the rebate as an amount available to this production.
    rebateIsConfirmed: bool = True
    #: Whether the production clears this programme's own stated thresholds:
    #: minimum qualifying spend, budget ceiling, expiry, status. Carries the
    #: verdict, a label, and one ``reasons`` entry per gate with the arithmetic
    #: spelled out, so an exclusion can be checked rather than merely asserted.
    programmeEligibility: dict[str, Any] | None = None
    #: Whether this figure may be presented as an amount the production can rely
    #: on. False for a short film whose programme has not been verified as
    #: accepting the format. Consumers must not infer this from the presence of an
    #: amount: an illustrative calculation looks identical to a confirmed one.
    incentiveIsConfirmed: bool = True
    #: eligible | ineligible | needs_confirmation | unverified
    incentiveEligibilityStatus: str | None = None
    #: The amount that may enter confirmed totals, or None when nothing may.
    confirmedIncentive: str | None = None
    #: The illustrative calculation, present only when it is NOT confirmed. Never
    #: summed into savings, net production cost, or any comparison of value.
    potentialIncentive: str | None = None
    #: The rebate figure that would have been quoted had the programme been
    #: available. Retained for audit; never rendered as an amount, because a figure
    #: this production cannot claim outlives the caveat attached to it.
    estimatedRebateWithheld: str | None = None
    #: Canonical window from ``resolve_payment_timing``; ``paymentSpeed`` is
    #: its rendered label. Every section reads this one object.
    paymentTiming: dict[str, Any] | None = None
    rateType: str | None = None               # e.g. "cash_rebate", "tax_credit"
    rateTiers: str | None = None              # human-readable tier summary
    eligibilityRules: list[str] | None = None # eligibility_rules_json from dataset
    expiryDate: str | None = None             # expiry_date from dataset
    dataFreshness: str | None = None          # e.g. "Verified 45 days ago"
    warnings: list[str] | None = None         # warnings_json + staleness warnings
    stalenessWarning: str | None = None       # set by validator if data_freshness_days > 365
    # v3 bankability
    bankabilityLabel: Literal["BANKABLE", "VERIFY FIRST", "NOT BANKABLE"] | None = None

    # ── v2 calculation status ────────────────────────────────────────────────
    #: The single conclusion about this figure, from ``resolve_calculation_status``.
    #: Derived from the verdicts above rather than a second opinion on them.
    calculationStatus: str | None = None
    calculationStatusLabel: str | None = None
    #: What the status means, from the contract, so no surface writes its own gloss.
    calculationStatusMeaning: str | None = None
    #: Why this status, in the producer's terms. One entry per failed gate or
    #: missing input, so a refusal can be checked rather than merely asserted.
    calculationStatusReasons: list[str] | None = None
    #: What would move it off this status, absent where there is no next step.
    calculationStatusNextStep: str | None = None
    #: Whether this status permits a figure at all. Read this rather than testing
    #: for the presence of an amount: an illustrative figure and a relied-upon one
    #: look identical once rendered.
    calculationCarriesFigure: bool | None = None
    #: Whether this programme may enter financial ranking and totals.
    calculationInRanking: bool | None = None
    #: The governance gate, deliberately separate from the status above. A green
    #: source badge must never imply the formula is approved for use.
    calculationVerification: str | None = None
    calculationVerificationLabel: str | None = None
    calculationIsApproved: bool | None = None


class Attribution(BaseModel):
    territory: str
    text: str


class ComparableProductionEntry(BaseModel):
    title: str
    genre: str
    budgetRange: str
    visualScale: str
    location: str
    year: int
    source: str
    relevanceDescription: str | None = None
    budgetUSD: int | None = None


class WeatherLogistic(BaseModel):
    territory: str
    bestMonths: list[str]
    weatherRisk: Literal["Low", "Medium", "High"]
    infrastructure: str
    travelVisa: str
    avgTempRange: str | None = None
    avgRainfall: str | None = None
    daylightHours: str | None = None
    seasonalConsiderations: str | None = None
    # Shoot-window integration fields (populated when filming_start_date provided)
    shootWindowOverlap: bool | None = None     # True if shoot months fall in risky period
    shootWindowRisk: str | None = None         # "Your Feb-Mar shoot overlaps with rainy season"
    exteriorExposure: str | None = None        # "High (72% exterior scenes)"
    estimatedDelayDays: int | None = None      # Estimated weather delay days
    contingencyBudget: str | None = None       # "£15,000–£25,000 recommended"


class FundingOpportunity(BaseModel):
    type: Literal["Fund", "Festival"]
    name: str
    genre: list[str]
    deadline: str
    notes: str
    website: str | None = None
    tier: str | None = None


class ScoringDimension(BaseModel):
    name: str           # Human-readable label, e.g. "Cost Efficiency"
    key: str            # Machine key matching LocationRanking fields
    description: str    # One-line explanation shown to the user


class ScoringColorKey(BaseModel):
    green: str   # e.g. "Score ≥ 70 — strong fit"
    gold: str    # e.g. "Score 40–69 — moderate fit, review trade-offs"
    red: str     # e.g. "Score ≤ 39 — potential challenges, proceed with caution"


class ScoringMethodology(BaseModel):
    overview: str                        # Brief paragraph on scoring approach
    dimensions: list[ScoringDimension]   # The six scoring dimensions (v3)
    weightingNote: str                   # How weights change per priority mode
    colorKey: ScoringColorKey            # Legend for colour bands


class ShootWindow(BaseModel):
    months: list[str]
    weatherNote: str | None = None


class ActionTimelineItem(BaseModel):
    action: str
    deadline: str | None = None
    note: str | None = None


class ExecutiveSummary(BaseModel):
    keyInsights: str
    recommendedTerritory: str
    recommendedTerritoryScore: int
    recommendedTerritoryRebate: str | None = None
    recommendedTerritoryInfrastructure: str | None = None
    recommendedTerritoryPaymentSpeed: str | None = None
    #: Weeks, despite the historical name. Kept for the Excel export and any
    #: stored report an older client still reads; prefer ``shootWeeks``.
    shootDays: int | None = None
    shootWeeks: int | None = None
    #: Canonical schedule from ``resolve_schedule``: declared weeks, the script's
    #: estimated days, which of the two the producer supplied, and whether they
    #: diverge. Both figures reach the reader, so both must be labelled.
    schedule: dict[str, Any] | None = None
    budget: str | None = None
    primaryLocations: list[str] | None = None
    shootWindow: ShootWindow | None = None
    # v3 additions
    headlineNetBudget: str | None = None
    actionTimeline: list[ActionTimelineItem] | None = None
    keyFlags: list[str] | None = None  # max 3 top-level flags


class NamedLocationShare(BaseModel):
    name: str
    scenes: int
    pct: int | None = None


class ScriptIntelligence(BaseModel):
    """Deterministic parsed-script stats (counted, not narrated)."""

    sceneCount: int | None = None
    interiorPct: int | None = None
    exteriorPct: int | None = None
    dayScenes: int | None = None
    nightScenes: int | None = None
    otherScenes: int | None = None
    estShootingDays: int | None = None
    principalCast: str | None = None
    supportingCast: str | None = None
    crowdScenes: int | None = None
    musicPerformanceScenes: int | None = None
    languages: list[str] | None = None
    namedLocations: list[NamedLocationShare] | None = None
    productionChallenges: list[str] | None = None


class FestivalRecommendation(BaseModel):
    """Festival matched on declared production attributes only — never inferred."""

    name: str
    location: str | None = None
    tier: str | None = None
    oscarQualifying: bool = False
    deadlinePattern: str | None = None
    eligibleFormats: list[str] | None = None
    matchedOn: list[str] = []
    whyMatched: str | None = None
    sourceUrl: str | None = None


class DistributorRecommendation(BaseModel):
    """Distributor ranked partly on scouting the recommended festivals."""

    name: str
    primaryMarket: str | None = None
    territoryReach: list[str] | None = None
    rightsType: str | None = None
    budgetTierFit: str | None = None
    submissionProcess: str | None = None
    scoutsRecommendedFestivals: list[str] = []
    matchedOn: list[str] = []
    whyMatched: str | None = None
    verified: bool = False
    sourceUrl: str | None = None


class FinancialScenario(BaseModel):
    territory: str
    # v3 6-step working fields
    totalBudget: str | None = None
    qualifyingSpendPct: str | None = None
    qualifyingSpend: str | None = None
    atlDeduction: str | None = None
    atlDeductionPct: str | None = None  # e.g. "15%" — set by validator from territory_financials
    netQualifyingSpend: str | None = None
    programme: str | None = None
    rateGross: str | None = None
    rateNet: str | None = None
    grossRebate: str | None = None
    netRebate: str | None = None
    netBudget: str | None = None
    notes: str | None = None
    # Legacy fields (kept for transition)
    localSpend: str | None = None
    rebateRate: str | None = None


class PaymentTimingEntry(BaseModel):
    """One territory's payment window on the "when the money arrives" chart.

    The window itself is ``paymentTiming``, the same canonical object the
    territory card and incentive table read. The certification and payment weeks
    are retained only for the bar breakdown.
    """

    territory: str
    #: Canonical window from ``resolve_payment_timing``.
    paymentTiming: dict[str, Any] | None = None
    label: str | None = None
    minMonths: int | None = None
    maxMonths: int | None = None
    certWeeksMin: float | None = None
    certWeeksMax: float | None = None
    paymentWeeksMin: float | None = None
    paymentWeeksMax: float | None = None
    totalWeeksMin: float | None = None
    totalWeeksMax: float | None = None
    sourceQuality: str | None = None
    suspended: bool = False


class FinancialAnalysis(BaseModel):
    budgetScenarios: list[FinancialScenario]
    paymentTiming: list[PaymentTimingEntry] | None = None


class ReadinessFigure(BaseModel):
    """One figure in a readiness component, with the input it came from."""

    label: str
    value: str
    basis: str


class ReadinessCheck(BaseModel):
    """One test a readiness component ran. result is pass / fail / warn / skipped."""

    name: str
    result: Literal["pass", "fail", "warn", "skipped"]
    detail: str


class ReadinessComponent(BaseModel):
    key: Literal[
        "budget_vs_cost_base",
        "incentive_confidence",
        "soft_money_coverage",
        "timeline_feasibility",
    ]
    label: str
    status: Literal["ready", "conditional", "insufficient_data", "not_ready"]
    weight: int
    headline: str
    figures: list[ReadinessFigure] = []
    checks: list[ReadinessCheck] = []
    note: str | None = None
    # incentive_confidence only: confirmed / contingent / failed
    grade: str | None = None


class ReadinessFlag(BaseModel):
    """An unverified or stale input the verdict had to work around."""

    severity: Literal["critical", "warning", "info"]
    input: str
    detail: str
    action: str


class FinancialReadiness(BaseModel):
    """Deterministic readiness assessment (handoff §4.1).

    Computed by ``reports.readiness.compute_financial_readiness`` — no AI is
    involved in any field here, and every figure cites its input.
    """

    verdict: Literal["READY", "CONDITIONAL", "NOT READY", "INSUFFICIENT DATA"]
    verdictReason: str
    rule: str
    score: int  # 0-100, weighted from component statuses
    territory: str
    programme: str | None = None
    currencySymbol: str = "£"
    components: list[ReadinessComponent] = []
    flags: list[ReadinessFlag] = []
    flagCounts: dict[str, int] = {}
    methodology: str
    computedOn: str


class TerritoryDeepDive(BaseModel):
    name: str
    country: str
    score: int
    rebate: str
    infrastructure: str
    paymentSpeed: str
    keyAdvantages: list[str]
    keyRisks: list[str]
    culturalTestLikelihood: str
    adminComplexity: str
    estimatedRebate: str


class CoProductionPartner(BaseModel):
    territory: str | None = None
    territoryId: str | None = None
    subdivisionId: str | None = None
    #: Null means the producer has not told us. Never coerced to zero, which would
    #: report a shortfall against the budget that may not exist.
    allocatedSpend: float | None = None
    currency: str | None = None
    participationPercent: float | None = None
    partnerStatus: str = "candidate"
    programme: str | None = None
    incentive: str | None = None
    #: Copied from the incentive estimate rather than recomputed, so a partner
    #: whose figure is withheld shows as withheld here too.
    calculationStatus: str | None = None
    calculationStatusLabel: str | None = None


class CoProductionStructure(BaseModel):
    """Partners in one production, reconciled rather than ranked.

    A co-production's partners are not competing for the production; each holds a
    share of it. Ordering them best-first would state something false about the
    structure, which is why ``partnersAreRanked`` is on the object and read by
    every surface instead of each deciding for itself.
    """

    mode: str
    partners: list[CoProductionPartner] = []
    partnerCount: int = 0
    currency: str | None = None
    budget: float | None = None
    unallocatedSpend: float | None = None
    reconciliationStatus: Literal[
        "reconciled", "under_allocated", "over_allocated", "not_assessable"
    ]
    reconciliationLabel: str
    reconciliationExplanation: str
    #: Positive is under-allocated, negative is over-allocated, null when the
    #: shares cannot be assessed at all.
    reconciliationRemaining: float | None = None
    route: str | None = None
    supranationalInterest: str | None = None
    structureNotes: list[str] = []
    partnersAreRanked: bool = False
    #: The partner incentives are never summed. Cumulation ceilings and public
    #: support intensity limits bite on the total, and none has been assessed.
    combinedIncentiveWithheld: bool = True
    combinedIncentiveReason: str


class ScriptAnalysis(BaseModel):
    genre: str
    tone: str
    scale: str
    complexity: Literal["Low", "Medium", "High", "Very High"]
    locationRankings: list[LocationRanking]
    incentiveEstimates: list[IncentiveEstimate]
    comparables: list[ComparableProductionEntry]
    weatherLogistics: list[WeatherLogistic]
    fundingOpportunities: list[FundingOpportunity]
    executiveSummary: ExecutiveSummary | None = None
    financialAnalysis: FinancialAnalysis | None = None
    financialReadiness: FinancialReadiness | None = None
    territoryDeepDives: list[TerritoryDeepDive] | None = None
    alternativeStrategy: str | None = None
    scoringMethodology: ScoringMethodology | None = None
    attributions: list[Attribution] | None = None
    # v3 additions
    sectionExplainers: dict[str, str] | None = None  # hardcoded, not AI-generated
    # PRO report redesign additions (all computed, None-safe)
    #: Caveat for a production format whose incentive eligibility the programme
    #: data does not record (today: short films). None when the default
    #: assumption is safe, or once applicable_formats is populated.
    formatEligibilityCaveat: str | None = None
    programmeAvailabilityCaveat: str | None = None
    #: Short-form only, and only when some displayed incentive is potential
    #: rather than confirmed. Rendered beside the figures it concerns.
    shortFormatIncentiveNotice: str | None = None
    #: Territories the producer selected that no section could analyse, each
    #: with a plain-language reason. A selection that vanishes without
    #: explanation reads as a bug and hides the useful fact.
    unanalysedTerritories: list[dict[str, Any]] = []
    scriptStats: ScriptIntelligence | None = None  # parsed stats (scriptIntelligence is the AI-narrative key)
    festivalRecommendations: list[FestivalRecommendation] | None = None
    distributorRecommendations: list[DistributorRecommendation] | None = None
    scriptOriginCallout: dict | None = None
    #: Present only for a co-production. None for a comparison, rather than an
    #: empty structure: a section that renders with nothing in it invites the
    #: reader to wonder what went missing.
    coProductionStructure: CoProductionStructure | None = None


class ProductionIntelligence(BaseModel):
    marketTrends: dict
    competitiveAnalysis: dict
    riskAssessment: dict


# --- Project Details Schemas (user-editable, producer-authored) ---


class RevenueScenario(BaseModel):
    theatrical_domestic: str | None = None
    theatrical_international: str | None = None
    svod: str | None = None
    tv_broadcast: str | None = None
    ancillary: str | None = None


class RevenueModel(BaseModel):
    low: RevenueScenario = RevenueScenario()
    base: RevenueScenario = RevenueScenario()
    high: RevenueScenario = RevenueScenario()


class RecoupmentWaterfall(BaseModel):
    distribution_fee_pct: str | None = None
    sales_agent_commission_pct: str | None = None
    pa_budget: str | None = None
    investor_equity_pct: str | None = None
    preferred_return_pct: str | None = None
    investor_net_profit_split_pct: str | None = None
    producer_net_profit_split_pct: str | None = None


class ProjectDetails(BaseModel):
    # Creative team
    director_name: str | None = None
    director_bio: str | None = None
    producer_name: str | None = None
    producer_bio: str | None = None
    # Script
    logline: str | None = None
    synopsis: str | None = None
    # Finance plan
    equity_sought: str | None = None
    equity_committed_pct: str | None = None
    minimum_investment: str | None = None
    investor_profit_share: str | None = None
    preferred_return: str | None = None
    # Phase 3 — Revenue model & waterfall
    revenue_model: RevenueModel | None = None
    waterfall: RecoupmentWaterfall | None = None


class UpdateProjectDetailsRequest(BaseModel):
    project_details: ProjectDetails


# --- Response Schemas ---


class ReportResponse(BaseModel):
    id: str
    title: str
    reportType: str
    createdAt: str
    analysis: dict | None = None
    pdfUrl: str | None = None
    userPlan: str | None = None
    shareToken: str | None = None
    projectDetails: dict | None = None


class ReportStatusResponse(BaseModel):
    status: str
    report_id: str
    message: str | None = None
    error: str | None = None
    progress: int | None = None


class PreviewReportResponse(BaseModel):
    reportType: str = "preview"
    analysis: dict
