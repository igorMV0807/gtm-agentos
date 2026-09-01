from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class KnowledgeDocumentRecord(BaseModel):
    id: UUID
    title: str
    document_type: str
    source: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")


class KnowledgeChunkCreate(BaseModel):
    content: str
    chunk_index: int
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    embedding: list[float]

    model_config = ConfigDict(extra="forbid")


class KnowledgeChunkRecord(BaseModel):
    id: UUID
    document_id: UUID
    content: str
    chunk_index: int
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")


class RagRetrievalRecord(BaseModel):
    id: UUID
    agent_run_id: UUID
    lead_id: UUID
    query: str
    chunk_id: UUID
    similarity: float
    rank: int
    created_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")
