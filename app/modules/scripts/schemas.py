from pydantic import BaseModel


class Location(BaseModel):
    name: str
    country: str
    territory: str
    frequency: int
    isMainLocation: bool


class BudgetEstimate(BaseModel):
    range: str  # micro | low | medium | high | tentpole
    minUSD: int
    maxUSD: int
    confidence: float
    indicators: list[str]


class ProductionScale(BaseModel):
    crewSize: str
    principalCast: str
    supportingCast: str
    backgroundExtras: str
    estimatedShootingDays: int


class Equipment(BaseModel):
    cameraEquipment: str
    specialEquipment: list[str]
    vfxRequirements: str


class Metadata(BaseModel):
    genres: list[str]
    format: str
    tone: str
    targetAudience: str


class Challenges(BaseModel):
    weatherDependent: bool
    historicalPeriod: bool
    specialPermits: bool
    stunts: bool
    animalWrangling: bool
    waterWork: bool
    nightShooting: bool
    notes: list[str]


class ScriptAnalysisResult(BaseModel):
    locations: list[Location]
    budgetEstimate: BudgetEstimate
    productionScale: ProductionScale
    equipment: Equipment
    metadata: Metadata
    challenges: Challenges
    rawResponse: str | None = None


class ValidateFileResponse(BaseModel):
    valid: bool
    error: str | None = None


class AnalysisStatusResponse(BaseModel):
    status: str
    report_id: str | None = None
    error: str | None = None
