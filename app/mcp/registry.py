from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.core.exceptions import ToolNotFoundError
from app.mcp.schemas import (
    GetAgentRunInput,
    GetAgentRunOutput,
    GetLeadHistoryInput,
    GetLeadHistoryOutput,
    GetLeadInput,
    GetLeadOutput,
    GetPipelineSummaryInput,
    GetPipelineSummaryOutput,
    SearchInternalKnowledgeInput,
    SearchInternalKnowledgeOutput,
    SearchLeadsInput,
    SearchLeadsOutput,
    ToolName,
)
from app.mcp.tools.analytics import AnalyticsTools
from app.mcp.tools.knowledge import KnowledgeTools
from app.mcp.tools.leads import LeadTools
from app.repositories.mcp_repository import MCPDataRepository
from app.services.retrieval_service import RetrievalService


ToolHandler = Callable[[BaseModel], BaseModel]


@dataclass(frozen=True)
class ToolDefinition:
    name: ToolName
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler

    @property
    def input_schema(self) -> dict[str, object]:
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, object]:
        return self.output_model.model_json_schema()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name.value in self._tools:
            raise ValueError(f"Duplicate tool registration: {definition.name.value}")
        self._tools[definition.name.value] = definition

    def get(self, name: str) -> ToolDefinition:
        definition = self._tools.get(name)
        if definition is None:
            raise ToolNotFoundError("Tool name is not registered")
        return definition

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)


def build_tool_registry(
    *,
    repository: MCPDataRepository,
    retrieval_service: RetrievalService,
) -> ToolRegistry:
    leads = LeadTools(repository)
    knowledge = KnowledgeTools(retrieval_service)
    analytics = AnalyticsTools(repository)
    registry = ToolRegistry()
    definitions = (
        ToolDefinition(
            name=ToolName.GET_LEAD,
            description="Return safe, non-secret details for one GTM AgentOS lead.",
            input_model=GetLeadInput,
            output_model=GetLeadOutput,
            handler=leads.get_lead,
        ),
        ToolDefinition(
            name=ToolName.SEARCH_LEADS,
            description=(
                "Search leads using only approved classification, industry, country, "
                "company, and result-limit filters."
            ),
            input_model=SearchLeadsInput,
            output_model=SearchLeadsOutput,
            handler=leads.search_leads,
        ),
        ToolDefinition(
            name=ToolName.GET_LEAD_HISTORY,
            description=(
                "Return qualification runs, orchestration runs, routes, and state "
                "transitions for one lead."
            ),
            input_model=GetLeadHistoryInput,
            output_model=GetLeadHistoryOutput,
            handler=leads.get_lead_history,
        ),
        ToolDefinition(
            name=ToolName.SEARCH_INTERNAL_KNOWLEDGE,
            description=(
                "Search approved internal GTM knowledge through the existing Voyage "
                "and pgvector retrieval service."
            ),
            input_model=SearchInternalKnowledgeInput,
            output_model=SearchInternalKnowledgeOutput,
            handler=knowledge.search_internal_knowledge,
        ),
        ToolDefinition(
            name=ToolName.GET_AGENT_RUN,
            description="Return a safe, auditable summary of one agent execution.",
            input_model=GetAgentRunInput,
            output_model=GetAgentRunOutput,
            handler=analytics.get_agent_run,
        ),
        ToolDefinition(
            name=ToolName.GET_PIPELINE_SUMMARY,
            description=(
                "Return bounded aggregate counts for lead classifications and "
                "orchestration routes."
            ),
            input_model=GetPipelineSummaryInput,
            output_model=GetPipelineSummaryOutput,
            handler=analytics.get_pipeline_summary,
        ),
    )
    for definition in definitions:
        registry.register(definition)
    return registry
