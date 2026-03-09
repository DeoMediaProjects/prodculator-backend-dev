from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PdfReportItem(BaseModel):
    id: str
    title: str
    email: str
    generated: datetime
    downloaded: bool
    size: str | None = None


class PdfReportListResponse(BaseModel):
    items: list[PdfReportItem]
    total: int
    limit: int
    offset: int


class PdfReportPreviewResponse(BaseModel):
    url: str


class ResendPayloadInner(BaseModel):
    email: str | None = None


class ResendRequest(BaseModel):
    payload: ResendPayloadInner
