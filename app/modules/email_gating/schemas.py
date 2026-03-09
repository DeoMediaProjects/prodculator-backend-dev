from datetime import datetime

from pydantic import BaseModel


class EmailGatingResponse(BaseModel):
    id: str
    email: str
    date: datetime
    report_generated: bool
    blocked: bool


class EmailGatingListResponse(BaseModel):
    items: list[EmailGatingResponse]
    total: int
    limit: int
    offset: int
