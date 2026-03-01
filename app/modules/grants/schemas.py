from typing import Any

from pydantic import BaseModel, ConfigDict


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
