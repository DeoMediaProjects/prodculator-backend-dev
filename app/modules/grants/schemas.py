from pydantic import BaseModel, ConfigDict


class GrantOpportunity(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    description: str | None = None
    territory: str | None = None
    deadline: str | None = None
    amount: str | None = None
    website_url: str | None = None

