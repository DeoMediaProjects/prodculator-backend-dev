from pydantic import BaseModel, ConfigDict


class TerritoryProfileAdmin(BaseModel):
    """Admin payload for a territory profile row.

    Tier/score of None means "not yet curated" and bankability fields of
    None mean "no verified data" — the API never substitutes defaults for
    either. Rating bands are NOT part of this schema: they are provisional,
    display-only derivations and are never stored (see admin UI).
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    territory: str | None = None
    isoCode: str | None = None
    region: str | None = None
    hemisphere: str | None = None
    # Crew depth / infrastructure (None = not curated)
    crewDepthTier: str | None = None
    crewDepthScore: int | None = None
    crewDepthNotes: str | None = None
    infrastructureTier: str | None = None
    infrastructureScore: int | None = None
    infrastructureNotes: str | None = None
    # Bankability — verified payment-timing data (None = no verified data)
    certWeeksMin: float | None = None
    certWeeksMax: float | None = None
    paymentWeeksMin: float | None = None
    paymentWeeksMax: float | None = None
    bankabilitySourceQuality: str | None = None   # government_direct | industry_secondary | government_plus_industry | unverified
    bankabilitySourceNote: str | None = None
    bankabilityRealWorldConfirms: bool | None = None  # None = unconfirmed (NOT false)
    bankabilitySuspended: bool | None = None
    bankabilitySourceUrl: str | None = None
    bankabilityAiRule: str | None = None
    # Governance
    lastReviewedAt: str | None = None
    reviewedBy: str | None = None
    reviewNotes: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
