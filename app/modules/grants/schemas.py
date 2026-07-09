
from pydantic import BaseModel, ConfigDict, field_validator

from app.core.territories import resolve_territory


class GrantOpportunity(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    territory: str | None = None
    fundingBody: str | None = None
    maxAmount: str | None = None
    currency: str | None = None
    applicationOpens: str | None = None
    applicationDeadline: str | None = None
    status: str | None = None
    daysUntilDeadline: int | None = None
    eligibility: list[str] | None = None
    websiteUrl: str | None = None
    dataSource: str | None = None
    verified: bool | None = None
    isNew: bool | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    lastVerifiedAt: str | None = None

    # v2 source-of-truth fields
    productionStage: str | None = None       # 'development' | 'production' | 'short' | 'multi'
    emergingFilmmaker: bool | None = None    # sourced flag — never inferred

    @field_validator("territory", mode="before")
    @classmethod
    def normalise_territory(cls, v: str | None) -> str | None:
        """Normalise territory strings to canonical Territory labels."""
        if not v:
            return v
        t = resolve_territory(v)
        return t.label if t else v
