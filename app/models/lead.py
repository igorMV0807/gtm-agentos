from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from app.schemas.qualification import LeadClassification, NextAction


class LeadRecord(BaseModel):
    id: UUID
    external_id: str | None = None
    name: str
    email: EmailStr
    company: str
    job_title: str | None = None
    company_size: int | None = None
    industry: str | None = None
    country: str | None = None
    website: str | None = None
    score: int | None = None
    classification: LeadClassification | None = None
    qualification_reason: str | None = None
    next_action: NextAction | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")


class AgentRunRecord(BaseModel):
    id: UUID
    lead_id: UUID
    agent_type: str
    model: str
    status: str
    input: dict[str, object]
    output: dict[str, object] | None = None
    error: str | None = None
    latency_ms: int | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")

