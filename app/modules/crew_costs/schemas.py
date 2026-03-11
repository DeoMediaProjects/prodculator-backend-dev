from pydantic import BaseModel, ConfigDict


class CrewRate(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    territory: str | None = None
    role: str | None = None
    category: str | None = None
    dayRate: float | None = None
    weekRate: float | None = None
    union: str | None = None
    lastUpdated: str | None = None
    source: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None

    # Enriched fields for data integrity
    currency: str | None = None
    sourceUrl: str | None = None
    budgetBand: str | None = None
    rateNotes: str | None = None
    lastVerifiedAt: str | None = None
