from pydantic import BaseModel, field_validator

from app.core.territories import resolve_territory


class WatchlistAddRequest(BaseModel):
    territory: str

    @field_validator("territory", mode="before")
    @classmethod
    def normalise_territory(cls, v: str) -> str:
        """Normalise territory strings to canonical Territory labels."""
        if not v:
            return v
        t = resolve_territory(v)
        return t.label if t else v


class WatchlistResponse(BaseModel):
    territories: list[str]

