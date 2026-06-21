from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


ContactInquiryCategory = Literal[
    "general",
    "sales",
    "partnership",
    "support",
    "billing",
]


class ContactMessageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    company: str | None = Field(default=None, max_length=200)
    category: ContactInquiryCategory = "general"
    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=10, max_length=4000)
    page_url: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "subject")
    @classmethod
    def trim_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("message")
    @classmethod
    def trim_message(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 10:
            raise ValueError("Message must be at least 10 characters")
        return stripped

    @field_validator("company", "page_url")
    @classmethod
    def trim_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ContactMessageSubmitResponse(BaseModel):
    success: bool = True
    message_id: str
    message: str = "Message received"
