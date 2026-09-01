from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class KnowledgeDocumentCreateRequest(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    document_type: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
    ]
    content: Annotated[str, Field(min_length=1, max_length=100_000)]
    source: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class KnowledgeDocumentCreateResponse(BaseModel):
    document_id: UUID
    chunks_created: Annotated[int, Field(ge=1)]
    embedding_provider: str
    embedding_model: str

    model_config = ConfigDict(extra="forbid")


class RetrievedChunk(BaseModel):
    document_id: UUID
    chunk_id: UUID
    title: str
    content: str
    similarity: Annotated[float, Field(ge=0.0, le=1.0)]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class ResearchSource(BaseModel):
    document_id: UUID
    chunk_id: UUID
    title: str
    similarity: Annotated[float, Field(ge=0.0, le=1.0)]

    model_config = ConfigDict(extra="forbid")


class GroundedResearchResult(BaseModel):
    research_context: Annotated[str, Field(min_length=1, max_length=4000)]

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
