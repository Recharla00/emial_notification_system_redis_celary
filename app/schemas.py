from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, field_validator


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: EmailStr
    subject: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100_000)
    html: str | None = Field(default=None, max_length=200_000)
    scheduled_at: AwareDatetime | None = None

    @field_validator("subject")
    @classmethod
    def no_header_injection(cls, value):
        if "\r" in value or "\n" in value:
            raise ValueError("Subject must be a single line")
        return value


class EmailStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    recipient: str
    subject: str
    status: str
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None
