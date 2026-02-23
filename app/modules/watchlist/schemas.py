from pydantic import BaseModel


class WatchlistAddRequest(BaseModel):
    territory: str


class WatchlistResponse(BaseModel):
    territories: list[str]

