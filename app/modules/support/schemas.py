from typing import Literal

from pydantic import BaseModel, Field, field_validator


SupportInquiryCategory = Literal[
    "general",
    "account",
    "billing",
    "technical",
    "report",
    "complaint",
]


class SupportInquiryCreate(BaseModel):
    category: SupportInquiryCategory = "general"
    message: str = Field(min_length=10, max_length=4000)
    selected_faq_question: str | None = Field(default=None, max_length=300)
    selected_faq_answer: str | None = Field(default=None, max_length=2000)
    page_url: str | None = Field(default=None, max_length=1000)

    @field_validator("message")
    @classmethod
    def trim_message(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 10:
            raise ValueError("Message must be at least 10 characters")
        return stripped

    @field_validator("selected_faq_question", "selected_faq_answer", "page_url")
    @classmethod
    def trim_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SupportInquirySubmitResponse(BaseModel):
    success: bool = True
    inquiry_id: str
    message: str = "Support inquiry received"
