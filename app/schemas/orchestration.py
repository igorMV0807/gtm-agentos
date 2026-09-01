from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.qualification import LeadClassification
from app.schemas.knowledge import ResearchSource


class AgentRoute(str, Enum):
    RESEARCH = "research"
    NURTURE = "nurture"
    STOP = "stop"


class AgentNextAction(str, Enum):
    RESEARCH_COMPANY = "research_company"
    NURTURE_SEQUENCE = "nurture_sequence"
    DISCARD = "discard"


class AgentStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentOrchestrationResponse(BaseModel):
    lead_id: UUID
    agent_run_id: UUID
    score: Annotated[int, Field(ge=0, le=100)]
    classification: LeadClassification
    route: AgentRoute
    next_action: AgentNextAction
    status: AgentStatus
    research_context: str | None = None
    sources: list[ResearchSource] | None = None

    model_config = ConfigDict(extra="forbid")
