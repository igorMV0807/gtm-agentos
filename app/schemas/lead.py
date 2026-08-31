from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field, field_validator


RequiredText = Annotated[str, Field(min_length=1, max_length=200)]
OptionalText = Annotated[str, Field(min_length=1, max_length=200)]


class LeadQualifyRequest(BaseModel):
    external_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    name: RequiredText
    email: EmailStr
    company: RequiredText
    job_title: OptionalText | None = None
    company_size: Annotated[int, Field(ge=1, le=10_000_000)] | None = None
    industry: OptionalText | None = None
    country: OptionalText | None = None
    website: AnyHttpUrl | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()

    def to_persistence_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")

