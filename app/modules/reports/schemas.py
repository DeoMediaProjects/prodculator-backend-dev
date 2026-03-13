from typing import Literal

from pydantic import BaseModel


# --- Input Schemas ---


class CreateReportRequest(BaseModel):
    script_title: str
    report_type: Literal["preview", "paid", "b2b"] = "paid"
    # script_file_path is no longer used — the script file is uploaded as multipart
    # and text is extracted in-memory, never stored.
    script_file_path: str | None = None

    # Project metadata (required)
    genre: list[str]
    budget_range: Literal["<500k", "500k-2m", "2m-5m", "5m-15m", "15m-30m", "30m+"]
    format: Literal[
        "Feature Film",
        "Short",
        "TV Series",
        "Limited Series",
        "Mini-Series",
        "Documentary",
        "Docuseries",
        "Animated Feature",
        "Animation Series",
        "Commercial",
        "Music Video",
        "Interactive",
        "VR",
    ]
    country: Literal[
        "UK",
        "Canada",
        "USA",
        "Australia",
        "Malta",
        "Ireland",
        "France",
        "Germany",
        "Spain",
        "Czech Republic",
        "Hungary",
        "Other",
    ]
    location_strategy: Literal["domestic", "open", "international"]
    production_priority: Literal["incentive", "full", "location"] = "full"

    # Email gate (required for preview reports from unauthenticated users)
    email: str | None = None

    # Conditional / optional metadata
    state_province: str | None = None
    territories_considering: list[str] | None = None
    filming_start_date: str | None = None
    filming_duration: int | None = None
    camera_equipment: list[str] | None = None
    crew_size: int | None = None
    principal_cast: int | None = None
    supporting_cast: int | None = None
    target_audience: str | None = None
    language: str | None = None


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
    # Enriched data-integrity fields
    paymentSpeed: str | None = None           # payment_timeline_notes from dataset
    rateType: str | None = None               # e.g. "cash_rebate", "tax_credit"
    rateTiers: str | None = None              # human-readable tier summary
    eligibilityRules: list[str] | None = None # eligibility_rules_json from dataset
    expiryDate: str | None = None             # expiry_date from dataset
    dataFreshness: str | None = None          # e.g. "Verified 45 days ago"
    warnings: list[str] | None = None         # warnings_json + staleness warnings
    stalenessWarning: str | None = None       # set by validator if data_freshness_days > 365


class CrewInsight(BaseModel):
    territory: str
    availability: Literal["High", "Medium", "Low"]
    costVsUSD: str
    qualityRating: int  # 1-5
    specialties: list[str]  # up to 5 roles
    tradeoff: str
    # Enriched FX fields (populated by ReportValidator)
    currency: str | None = None    # source currency of underlying data
    fxRate: float | None = None    # rate used for GBP conversion
    fxDate: str | None = None      # date of FX rate used
    dataSource: str | None = None  # source attribution for crew rates


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


class FundingOpportunity(BaseModel):
    type: Literal["Fund", "Festival"]
    name: str
    genre: list[str]
    deadline: str
    notes: str
    website: str | None = None
    tier: str | None = None


class ExecutiveSummary(BaseModel):
    keyInsights: str
    recommendedTerritory: str
    recommendedTerritoryScore: int
    recommendedTerritoryRebate: str | None = None
    recommendedTerritoryInfrastructure: str | None = None
    recommendedTerritoryPaymentSpeed: str | None = None
    shootDays: int | None = None
    budget: str | None = None
    budgetRange: str | None = None
    primaryLocations: list[str] | None = None


class FinancialScenario(BaseModel):
    territory: str
    localSpend: str
    rebateRate: str
    grossRebate: str
    netBudget: str


class CrewCostRow(BaseModel):
    role: str
    territories: dict[str, str]  # territory_name -> salary range string


class FinancialAnalysis(BaseModel):
    budgetScenarios: list[FinancialScenario]
    crewCostComparison: list[CrewCostRow]


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
    crewInsights: list[CrewInsight]
    comparables: list[ComparableProductionEntry]
    weatherLogistics: list[WeatherLogistic]
    fundingOpportunities: list[FundingOpportunity]
    executiveSummary: ExecutiveSummary | None = None
    financialAnalysis: FinancialAnalysis | None = None
    territoryDeepDives: list[TerritoryDeepDive] | None = None
    alternativeStrategy: str | None = None


class ProductionIntelligence(BaseModel):
    marketTrends: dict
    competitiveAnalysis: dict
    riskAssessment: dict


# --- Response Schemas ---


class ReportResponse(BaseModel):
    id: str
    title: str
    reportType: str
    createdAt: str
    analysis: dict | None = None
    pdfUrl: str | None = None


class ReportStatusResponse(BaseModel):
    status: str
    report_id: str
    message: str | None = None
    error: str | None = None
    progress: int | None = None


class PreviewReportResponse(BaseModel):
    reportType: str = "preview"
    analysis: dict
