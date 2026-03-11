from pydantic import BaseModel, ConfigDict


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
