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
