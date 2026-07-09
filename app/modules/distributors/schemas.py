from pydantic import BaseModel, ConfigDict


class Distributor(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    sourceUrl: str | None = None
    specialtyGenres: list[str] | None = None
    specialtyRepresentation: list[str] | None = None   # sourced/opt-in — never inferred
    territoryReach: list[str] | None = None
    scoutsFestivals: list[str] | None = None
    rightsType: str | None = None
    budgetTierFit: str | None = None
    submissionProcess: str | None = None
    notes: str | None = None
    verifiedAt: str | None = None
    activeStatus: str | None = None                    # 'confirmed_active' | 'verify_current_status' | ...
    primaryMarket: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
