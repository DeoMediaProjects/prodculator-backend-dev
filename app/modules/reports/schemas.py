from typing import Literal

from pydantic import BaseModel

from app.modules.scripts.schemas import ScriptAnalysisResult


class IncentiveDetail(BaseModel):
    programName: str
    rate: str
    cap: str
    potentialRebateUSD: int


class CrewCostBreakdown(BaseModel):
    role: str
    dayRate: float
    weekRate: float


class EstimatedCrewCosts(BaseModel):
    dailyTotal: float
    weeklyTotal: float
    totalForProduction: float
    currency: str
    breakdown: list[CrewCostBreakdown]


class LocationMatch(BaseModel):
    score: float
    reasons: list[str]


class TerritoryAnalysis(BaseModel):
    territory: str
    country: str
    overallScore: int
    incentives: list[IncentiveDetail]
    estimatedCrewCosts: EstimatedCrewCosts
    locationMatch: LocationMatch
    pros: list[str]
    cons: list[str]


class TopIncentive(BaseModel):
    territory: str
    programName: str
    potentialRebate: int
    rate: str


class ExecutiveSummary(BaseModel):
    recommendedTerritories: list[str]
    estimatedBudgetRange: str
    topIncentiveOpportunity: TopIncentive
    keyInsights: list[str]


class ComparableProduction(BaseModel):
    title: str
    year: int
    budget: str
    territory: str
    incentiveUsed: str
    genres: list[str]
    relevanceScore: int


class GrantOpportunity(BaseModel):
    title: str
    organization: str
    amount: str
    deadline: str
    territory: str
    matchScore: int


class FestivalRecommendation(BaseModel):
    name: str
    location: str
    deadline: str
    tier: str
    submissionFees: str
    matchScore: int


class ProductionDetails(BaseModel):
    format: str
    genres: list[str]
    estimatedShootingDays: int
    crewSize: str
    castSize: str
    vfxRequirements: str
    specialRequirements: list[str]


class ReportMetadata(BaseModel):
    analysisVersion: str = "1.0.0"


class B2CReport(BaseModel):
    reportId: str
    scriptTitle: str
    generatedAt: str
    executiveSummary: ExecutiveSummary
    territoryAnalysis: list[TerritoryAnalysis]
    comparableProductions: list[ComparableProduction]
    grantOpportunities: list[GrantOpportunity]
    festivalRecommendations: list[FestivalRecommendation]
    productionDetails: ProductionDetails
    _metadata: ReportMetadata = ReportMetadata()


class ProductionIntelligence(BaseModel):
    marketTrends: dict
    competitiveAnalysis: dict
    riskAssessment: dict


class B2BReport(B2CReport):
    productionIntelligence: ProductionIntelligence | None = None
    branding: dict | None = None


class CreateReportRequest(BaseModel):
    script_title: str
    report_type: Literal["free", "paid", "b2b"] = "free"
    script_file_path: str | None = None


class ReportResponse(BaseModel):
    id: str
    user_id: str
    script_title: str
    status: str
    report_type: str
    report_data: dict | None = None
    pdf_url: str | None = None
    created_at: str
    completed_at: str | None = None


class ReportStatusResponse(BaseModel):
    status: str
    report_id: str
    message: str | None = None
    error: str | None = None
