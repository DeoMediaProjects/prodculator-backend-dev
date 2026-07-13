from typing import Any
from pydantic import BaseModel, ConfigDict


class FilmFestival(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    year: int | None = None
    genres: list[str] | None = None
    budgetTiers: list[str] | None = None
    location: str | None = None
    festivalDates: str | None = None
    premiereRequirement: str | None = None
    tier: str | None = None
    acceptanceRate: float | None = None
    websiteUrl: str | None = None
    filmfreewayUrl: str | None = None
    dataSource: str | None = None
    verified: bool | None = None
    isNew: bool | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    deadlines: list[dict[str, Any]] | None = None
    currentStatus: str | None = None
    nextDeadline: dict[str, Any] | None = None
    daysUntilNextDeadline: int | None = None
    lastVerifiedAt: str | None = None
    notableAlumni: list[str] | None = None
    averageBudgetOfAcceptedFilms: str | None = None
    notes: str | None = None

    # v2 source-of-truth fields
    continent: str | None = None
    representationFocus: list[str] | None = None   # sourced/opt-in — never inferred
    eligibleFormats: list[str] | None = None
    minMonthsAfterCompletion: int | None = None
    maxMonthsAfterCompletion: int | None = None
    deadlinePattern: str | None = None
    oscarQualifying: bool | None = None
    baftaQualifying: bool | None = None
