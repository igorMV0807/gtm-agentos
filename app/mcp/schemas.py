from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.agents.state import AgentStep
from app.schemas.orchestration import AgentRoute
from app.schemas.qualification import LeadClassification, NextAction


class ToolName(str, Enum):
    GET_LEAD = "get_lead"
    SEARCH_LEADS = "search_leads"
    GET_LEAD_HISTORY = "get_lead_history"
    SEARCH_INTERNAL_KNOWLEDGE = "search_internal_knowledge"
    GET_AGENT_RUN = "get_agent_run"
    GET_PIPELINE_SUMMARY = "get_pipeline_summary"


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GetLeadInput(ToolInput):
    lead_id: UUID


class SearchLeadsInput(ToolInput):
    classification: LeadClassification | None = None
    industry: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    country: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    company: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    limit: Annotated[int, Field(ge=1, le=50)] = 20


class GetLeadHistoryInput(ToolInput):
    lead_id: UUID


class SearchInternalKnowledgeInput(ToolInput):
    query: Annotated[str, Field(min_length=1, max_length=1000)]
    top_k: Annotated[int, Field(ge=1, le=10)] = 5


class GetAgentRunInput(ToolInput):
    agent_run_id: UUID


class GetPipelineSummaryInput(ToolInput):
    pass


class SafeLead(BaseModel):
    id: UUID
    external_id: str | None = None
    name: str
    company: str
    job_title: str | None = None
    company_size: int | None = None
    industry: str | None = None
    country: str | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    classification: LeadClassification | None = None
    next_action: NextAction | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")


class GetLeadOutput(BaseModel):
    lead: SafeLead

    model_config = ConfigDict(extra="forbid")


class SearchLeadsOutput(BaseModel):
    leads: list[SafeLead]
    count: int = Field(ge=0, le=50)

    model_config = ConfigDict(extra="forbid")


class AgentRunSummary(BaseModel):
    id: UUID
    lead_id: UUID
    agent_type: str
    model: str
    status: str
    classification: LeadClassification | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    route: AgentRoute | None = None
    next_action: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")


class StateTransitionSummary(BaseModel):
    from_state: AgentStep
    to_state: AgentStep
    route: AgentRoute | None = None
    status: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")


class GetLeadHistoryOutput(BaseModel):
    lead_id: UUID
    qualification_runs: list[AgentRunSummary]
    orchestration_runs: list[AgentRunSummary]
    transitions: list[StateTransitionSummary]

    model_config = ConfigDict(extra="forbid")


class KnowledgeSearchResult(BaseModel):
    document_id: UUID
    chunk_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=200)]
    content: Annotated[str, Field(min_length=1, max_length=4000)]
    similarity: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid")


class SearchInternalKnowledgeOutput(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=1000)]
    results: list[KnowledgeSearchResult]
    count: int = Field(ge=0, le=10)

    model_config = ConfigDict(extra="forbid")


class GetAgentRunOutput(BaseModel):
    run: AgentRunSummary

    model_config = ConfigDict(extra="forbid")


class GetPipelineSummaryOutput(BaseModel):
    total_leads: int = Field(ge=0)
    hot: int = Field(ge=0)
    warm: int = Field(ge=0)
    cold: int = Field(ge=0)
    research: int = Field(ge=0)
    nurture: int = Field(ge=0)
    stop: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class ToolExecuteRequest(BaseModel):
    tool_name: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
    ]
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    agent_run_id: UUID | None = None
    lead_id: UUID | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("arguments")
    @classmethod
    def bound_arguments(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(value) > 10:
            raise ValueError("arguments may contain at most 10 fields")
        return value


class ToolExecuteResponse(BaseModel):
    tool_call_id: UUID
    tool_name: ToolName
    status: str
    result: dict[str, JsonValue]

    model_config = ConfigDict(extra="forbid")
