from pydantic import BaseModel, ConfigDict


class FilmFestival(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    location: str | None = None
    submission_deadline: str | None = None
    website_url: str | None = None
    filmfreeway_url: str | None = None

