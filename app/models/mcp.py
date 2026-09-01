from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ToolCallStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class ToolCallAuditCreate(BaseModel):
    agent_run_id: UUID | None = None
    lead_id: UUID | None = None
    tool_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    input: dict[str, JsonValue] = Field(default_factory=dict)
    output: dict[str, JsonValue] | None = None
    status: ToolCallStatus
    error: str | None = Field(default=None, max_length=200)
    latency_ms: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class ToolCallRecord(ToolCallAuditCreate):
    id: UUID
    created_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")


class PipelineCounts(BaseModel):
    total_leads: int = Field(ge=0)
    hot: int = Field(ge=0)
    warm: int = Field(ge=0)
    cold: int = Field(ge=0)
    research: int = Field(ge=0)
    nurture: int = Field(ge=0)
    stop: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")
