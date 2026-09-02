from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AIUsageEventCreate(BaseModel):
    lead_id: UUID | None = None
    agent_run_id: UUID | None = None
    provider: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")
    model: str = Field(min_length=1, max_length=120)
    operation: str = Field(
        pattern=r"^(qualification|research_context|email_draft|embedding_document|embedding_query)$"
    )
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class AIUsageEventRecord(AIUsageEventCreate):
    id: UUID
    created_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")
