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
    historicalPeriod: bool  # maps to v3 period_setting
    specialPermits: bool
    stunts: bool
    animalWrangling: bool
    waterWork: bool
    nightShooting: bool
    notes: list[str]
    # Signal counts (aggregated from chunk analysis)
    extIntRatio: float | None = None        # 0.0–1.0  (exterior scenes / total scenes)
    nightSceneCount: int | None = None      # total night-shoot scenes (legacy)
    waterSceneCount: int | None = None      # scenes requiring water work
    vfxHeavySceneCount: int | None = None   # scenes requiring significant VFX
    # v3 structured extraction fields
    total_scenes: int | None = None
    interior_scenes: int | None = None
    exterior_scenes: int | None = None
    interior_pct: float | None = None       # interior_scenes / total_scenes × 100
    exterior_pct: float | None = None       # exterior_scenes / total_scenes × 100
    day_scenes: int | None = None
    night_scenes: int | None = None         # v3 field (coexists with nightSceneCount)
    languages: list[str] | None = None      # only languages with explicit dialogue
    voice_overs: bool | None = None         # true if any (V.O.) or (O.S.) present
    named_locations: dict[str, int] | None = None  # location name → scene count
    primary_location: str | None = None     # highest scene count location
    music_performance_scenes: int | None = None
    conflict_type: str | None = None        # Person vs Person / System / Self / Society
    irresolution: bool | None = None        # true if primary conflict unresolved
    stunt_sequences: int | None = None      # scenes with explicit stunt directions
    crowd_scenes: int | None = None         # scenes requiring 20+ background artists


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
