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
    # Signal counts (aggregated from chunk analysis)
    extIntRatio: float | None = None        # 0.0–1.0  (exterior scenes / total scenes)
    nightSceneCount: int | None = None      # total night-shoot scenes
    waterSceneCount: int | None = None      # scenes requiring water work
    vfxHeavySceneCount: int | None = None   # scenes requiring significant VFX


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
