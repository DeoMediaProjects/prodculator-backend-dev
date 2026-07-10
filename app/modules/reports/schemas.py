from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.territories import resolve_territory


# --- Input Schemas ---


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
        "HUF", "CZK", "MAD", "NZD", "RON", "RSD", "OTHER",
    ] = "GBP"
    format: Literal[
        "Feature Film",
        "Short Film",
        "TV Series",
        "Limited Series",
        "Mini-Series",
        "Documentary",
        "Docuseries",
        "Animation",
        "Animated Feature",
        "Animation Series",
        "Commercial",
        "Music Video",
        "Interactive",
        "VR",
    ]
    country: str  # Validated & normalised to canonical label by validator below
    location_strategy: Annotated[
        Literal["domestic", "open", "international"],
        Field(description="Select a location strategy: 'domestic', 'open', or 'international'"),
    ]
    production_priority: Literal["incentive", "full", "location"] = "full"

    # Email gate (required for preview reports from unauthenticated users)
    email: str | None = None

    # Conditional / optional metadata
    state_province: str | None = None
    territories_considering: list[str] | None = None
    filming_start_date: str | None = None
    filming_duration: int | None = None
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

    # Producer eligibility (for nationality / co-production checks)
    producer_country: str | None = None  # Jurisdiction of production company (ISO code, e.g. "GB")
    co_production_status: Literal[
        "sole_producer",
        "co_production_treaty",
        "co_production_informal",
        "undecided",
    ] | None = None

    @field_validator("location_strategy", mode="before")
    @classmethod
    def validate_location_strategy(cls, v: str) -> str:
        if not v or str(v).strip() == "":
            raise ValueError("Please select a location strategy (Shooting domestically, Open to international, or Specifically international)")
        if v not in ("domestic", "open", "international"):
            raise ValueError("Location strategy must be 'domestic', 'open', or 'international'")
        return v

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
        """Normalise each territory string to the canonical label."""
        if not v:
            return v
        result: list[str] = []
        for raw in v:
            t = resolve_territory(raw)
            result.append(t.label if t else raw)
        return result


# --- Output Schemas (ScriptAnalysis interface) ---


class LocationRanking(BaseModel):
    name: str
    country: str
    score: int  # 0-100
    costEfficiency: int  # 0-100
    crewDepth: int  # 0-100
    infrastructure: int  # 0-100
    incentiveStrength: int  # 0-100
    currencyAdvantage: int  # 0-100
    reasoning: list[str]  # 3-5 bullet points
    isAssessmentOnly: bool | None = None
    rebatePercent: str | None = None
    rebateAmount: str | None = None
    culturalTestLikelihood: str | None = None
    adminComplexity: str | None = None
    paymentSpeed: str | None = None
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
    # Enriched data-integrity fields
    paymentSpeed: str | None = None           # payment_timeline_notes from dataset
    rateType: str | None = None               # e.g. "cash_rebate", "tax_credit"
    rateTiers: str | None = None              # human-readable tier summary
    eligibilityRules: list[str] | None = None # eligibility_rules_json from dataset
    expiryDate: str | None = None             # expiry_date from dataset
    dataFreshness: str | None = None          # e.g. "Verified 45 days ago"
    warnings: list[str] | None = None         # warnings_json + staleness warnings
    stalenessWarning: str | None = None       # set by validator if data_freshness_days > 365
    # v3 bankability
    bankabilityLabel: Literal["BANKABLE", "VERIFY FIRST", "NOT BANKABLE"] | None = None


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
    shootDays: int | None = None
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
    """Certification/payment receipt windows from territory_profiles bankability data."""

    territory: str
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
    territoryDeepDives: list[TerritoryDeepDive] | None = None
    alternativeStrategy: str | None = None
    scoringMethodology: ScoringMethodology | None = None
    attributions: list[Attribution] | None = None
    # v3 additions
    sectionExplainers: dict[str, str] | None = None  # hardcoded, not AI-generated
    # PRO report redesign additions (all computed, None-safe)
    scriptStats: ScriptIntelligence | None = None  # parsed stats (scriptIntelligence is the AI-narrative key)
    festivalRecommendations: list[FestivalRecommendation] | None = None
    distributorRecommendations: list[DistributorRecommendation] | None = None
    scriptOriginCallout: dict | None = None


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
