from enum import Enum
from time import perf_counter
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.lead import LeadRecord
from app.schemas.knowledge import RetrievedChunk
from app.schemas.lead import LeadQualifyRequest
from app.schemas.orchestration import AgentNextAction, AgentRoute, AgentStatus
from app.schemas.qualification import LeadClassification, QualificationResult


class AgentStep(str, Enum):
    START = "START"
    LOAD_LEAD = "load_lead"
    QUALIFY_LEAD = "qualify_lead"
    ROUTE_BY_CLASSIFICATION = "route_by_classification"
    RESEARCH_STATE = "research_state"
    RETRIEVE_GTM_KNOWLEDGE = "retrieve_gtm_knowledge"
    BUILD_RESEARCH_CONTEXT = "build_research_context"
    NURTURE_STATE = "nurture_state"
    STOP_STATE = "stop_state"
    PERSIST_AGENT_STATE = "persist_agent_state"
    END = "END"


class AgentStateTransition(BaseModel):
    from_state: AgentStep
    to_state: AgentStep
    route: AgentRoute | None = None
    payload: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class AgentState(BaseModel):
    payload: LeadQualifyRequest
    lead_id: UUID | None = None
    lead: LeadRecord | None = None
    agent_run_id: UUID | None = None
    qualification: QualificationResult | None = None
    classification: LeadClassification | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    reason: str | None = None
    next_action: AgentNextAction | None = None
    current_step: AgentStep = AgentStep.START
    route: AgentRoute | None = None
    retrieval_query: str | None = None
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    research_context: str | None = None
    status: AgentStatus = AgentStatus.STARTED
    error: str | None = None
    transitions: list[AgentStateTransition] = Field(default_factory=list)
    started_at: float = Field(default_factory=perf_counter, exclude=True)

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
