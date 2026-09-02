from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeadMetrics(StrictModel):
    total: int = Field(ge=0)
    hot: int = Field(ge=0)
    warm: int = Field(ge=0)
    cold: int = Field(ge=0)


class AgentMetrics(StrictModel):
    runs: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    average_latency_ms: float = Field(ge=0)


class RagMetrics(StrictModel):
    retrievals: int = Field(ge=0)
    average_similarity: float = Field(ge=0, le=1)
    no_context: int = Field(ge=0)


class ToolMetrics(StrictModel):
    calls: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    rejected: int = Field(ge=0)
    average_latency_ms: float = Field(ge=0)


class ActionMetrics(StrictModel):
    pending: int = Field(ge=0)
    approved: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    rejected: int = Field(ge=0)
    waiting_approval: int = Field(ge=0)


class AIMetrics(StrictModel):
    events: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: Decimal = Field(ge=0)
    average_latency_ms: float = Field(ge=0)


class FailureItem(StrictModel):
    component: str
    error_code: str
    timestamp: datetime | None = None


class OverviewResponse(StrictModel):
    leads: LeadMetrics
    agents: AgentMetrics
    rag: RagMetrics
    tools: ToolMetrics
    actions: ActionMetrics
    ai: AIMetrics
    recent_failures: list[FailureItem]
    demo_mode: bool = False


class RagEvidenceItem(StrictModel):
    document_title: str
    similarity: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)
    timestamp: datetime | None = None


class ToolCallItem(StrictModel):
    tool_name: str
    status: str
    latency_ms: int = Field(ge=0)
    timestamp: datetime | None = None
    error_code: str | None = None


class ActionItem(StrictModel):
    id: UUID
    lead_id: UUID
    agent_run_id: UUID | None = None
    lead_name: str | None = None
    company: str | None = None
    action_type: str
    status: str
    requires_approval: bool
    payload_preview: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error_code: str | None = None
    demo: bool = False


class AgentRunItem(StrictModel):
    id: UUID
    lead_id: UUID
    lead_name: str | None = None
    company: str | None = None
    agent_type: str
    model: str
    status: str
    classification: str | None = None
    score: int | None = None
    route: str | None = None
    reasoning_summary: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    created_at: datetime | None = None
    rag_evidence: list[RagEvidenceItem] = Field(default_factory=list)
    tool_calls: list[ToolCallItem] = Field(default_factory=list)
    external_actions: list[ActionItem] = Field(default_factory=list)


class AgentRunsResponse(StrictModel):
    items: list[AgentRunItem]
    limit: int
    offset: int
    has_more: bool


class ActionsResponse(StrictModel):
    items: list[ActionItem]
    limit: int
    offset: int
    has_more: bool


class TimelineEvent(StrictModel):
    component: str
    event: str
    status: str | None = None
    timestamp: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class LeadTimelineResponse(StrictModel):
    lead_id: UUID
    lead_name: str
    company: str
    events: list[TimelineEvent]


class AIUsageItem(StrictModel):
    provider: str
    model: str
    operation: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: Decimal | None = None
    latency_ms: int = Field(ge=0)
    timestamp: datetime | None = None


class UsageResponse(StrictModel):
    summary: AIMetrics
    events: list[AIUsageItem]
    limit: int
    offset: int
    has_more: bool
