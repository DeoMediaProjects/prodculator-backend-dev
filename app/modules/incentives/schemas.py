from pydantic import BaseModel, ConfigDict, field_validator

from app.core.territories import resolve_territory


class IncentiveProgram(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    territory: str | None = None
    program: str | None = None
    rate: str | None = None
    cap: str | None = None
    lastUpdated: str | None = None
    status: str | None = None
    sourceUrl: str | None = None
    autoSyncEnabled: bool | None = None
    lastAutoCheck: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None

    # Enriched fields for territory data integrity
    rateGross: float | None = None
    rateNet: float | None = None
    rateType: str | None = None
    rateTierJson: str | None = None
    capAmount: float | None = None
    capCurrency: str | None = None
    capPerPerson: float | None = None
    capPerPersonCurrency: str | None = None
    qualifyingSpendMin: float | None = None
    qualifyingSpendCapPct: float | None = None
    qualifyingSpendCurrency: str | None = None
    paymentTimelineDaysMin: int | None = None
    paymentTimelineDaysMax: int | None = None
    paymentTimelineNotes: str | None = None
    eligibilityRulesJson: str | None = None
    expiryDate: str | None = None
    currency: str | None = None
    warningsJson: str | None = None
    lastVerifiedAt: str | None = None
    sourceName: str | None = None

    # Regional incentive stacking fields
    scope: str | None = None               # 'national' | 'regional' | 'municipal'
    parentTerritory: str | None = None
    stackingGroup: str | None = None
    stackableWith: str | None = None       # JSON array of compatible program names

    # Producer eligibility / nationality fields
    nationalityRequirements: str | None = None   # JSON array of qualifying country codes
    coProductionEligible: bool | None = None
    coProductionTreaties: str | None = None      # JSON array of treaty partner codes
    spvEligible: bool | None = None

    @field_validator("territory", mode="before")
    @classmethod
    def normalise_territory(cls, v: str | None) -> str | None:
        """Normalise territory strings to canonical Territory labels."""
        if not v:
            return v
        t = resolve_territory(v)
        return t.label if t else v
