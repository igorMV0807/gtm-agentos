import logging
from typing import Annotated, cast
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS
from pydantic import Field, JsonValue, ValidationError

from app.api.dependencies import get_tool_execution_service
from app.core.exceptions import (
    GTMAgentOSError,
    ToolInputInvalidError,
    ToolNotFoundError,
)
from app.core.logging import configure_logging
from app.mcp.execution import ToolExecutionService
from app.mcp.schemas import (
    GetAgentRunOutput,
    GetLeadHistoryOutput,
    GetLeadOutput,
    GetPipelineSummaryOutput,
    SearchInternalKnowledgeOutput,
    SearchLeadsOutput,
    ToolName,
)
from app.schemas.qualification import LeadClassification


logger = logging.getLogger(__name__)


_NON_TOOL_METHODS = (
    "prompts/list",
    "prompts/get",
    "resources/list",
    "resources/templates/list",
    "resources/read",
    "resources/subscribe",
    "resources/unsubscribe",
)


class _ToolSecurityBoundary:
    """Audit and reject unknown or schema-invalid calls before any handler."""

    def __init__(self, executor: ToolExecutionService) -> None:
        self._executor = executor

    async def __call__(
        self,
        ctx: ServerRequestContext,
        call_next: CallNext,
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)

        params = ctx.params or {}
        tool_name = params.get("name")
        raw_arguments = params.get("arguments") or {}
        if not isinstance(tool_name, str) or not isinstance(raw_arguments, dict):
            return await call_next(ctx)
        arguments = cast(dict[str, JsonValue], raw_arguments)

        try:
            definition = self._executor.registry.get(tool_name)
        except ToolNotFoundError:
            self._record_rejection(tool_name, arguments)
            raise MCPError(code=INVALID_PARAMS, message="Unknown tool") from None

        try:
            definition.input_model.model_validate(arguments)
        except ValidationError:
            self._record_rejection(tool_name, arguments)
            raise MCPError(code=INVALID_PARAMS, message="Invalid tool input") from None

        return await call_next(ctx)

    def _record_rejection(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
    ) -> None:
        lead_id = self._optional_uuid(arguments.get("lead_id"))
        agent_run_id = self._optional_uuid(arguments.get("agent_run_id"))
        try:
            self._executor.execute(
                tool_name,
                arguments,
                lead_id=lead_id,
                agent_run_id=agent_run_id,
            )
        except (ToolNotFoundError, ToolInputInvalidError):
            return

    @staticmethod
    def _optional_uuid(value: JsonValue | None) -> UUID | None:
        if not isinstance(value, str):
            return None
        try:
            return UUID(value)
        except ValueError:
            return None


def _expose_tools_only(server: MCPServer) -> None:
    """Remove empty capabilities that MCPServer registers by default."""
    for method in _NON_TOOL_METHODS:
        server._lowlevel_server._request_handlers.pop(method, None)


def _execute_tool(
    executor: ToolExecutionService,
    tool_name: str,
    arguments: dict[str, JsonValue],
):
    try:
        return executor.execute(tool_name, arguments)
    except GTMAgentOSError as exc:
        code = INVALID_PARAMS if exc.status_code < 500 else INTERNAL_ERROR
        raise MCPError(code=code, message=exc.public_message) from None


def create_mcp_server(executor: ToolExecutionService) -> MCPServer:
    server = MCPServer(
        name="gtm-agentos",
        title="GTM AgentOS Read-only Tools",
        description="Controlled internal lead, run, analytics, and RAG tools.",
        instructions=(
            "Use only the registered read-only tools. Never request credentials, "
            "arbitrary SQL, shell commands, local files, or external URLs."
        ),
        version="0.4.0",
        middleware=[_ToolSecurityBoundary(executor)],
    )
    _expose_tools_only(server)

    @server.tool(
        name=ToolName.GET_LEAD.value,
        description="Return safe, non-secret details for one GTM AgentOS lead.",
        structured_output=True,
    )
    def get_lead(lead_id: UUID) -> GetLeadOutput:
        result = _execute_tool(
            executor,
            ToolName.GET_LEAD.value,
            {"lead_id": str(lead_id)},
        )
        return GetLeadOutput.model_validate(result.output)

    @server.tool(
        name=ToolName.SEARCH_LEADS.value,
        description=(
            "Search leads with approved filters only; no arbitrary SQL or table access."
        ),
        structured_output=True,
    )
    def search_leads(
        classification: LeadClassification | None = None,
        industry: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        country: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        company: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> SearchLeadsOutput:
        result = _execute_tool(
            executor,
            ToolName.SEARCH_LEADS.value,
            {
                "classification": classification.value if classification else None,
                "industry": industry,
                "country": country,
                "company": company,
                "limit": limit,
            },
        )
        return SearchLeadsOutput.model_validate(result.output)

    @server.tool(
        name=ToolName.GET_LEAD_HISTORY.value,
        description=(
            "Return qualification runs, orchestration runs, routes, and transitions "
            "for one lead."
        ),
        structured_output=True,
    )
    def get_lead_history(lead_id: UUID) -> GetLeadHistoryOutput:
        result = _execute_tool(
            executor,
            ToolName.GET_LEAD_HISTORY.value,
            {"lead_id": str(lead_id)},
        )
        return GetLeadHistoryOutput.model_validate(result.output)

    @server.tool(
        name=ToolName.SEARCH_INTERNAL_KNOWLEDGE.value,
        description=(
            "Search only approved internal GTM knowledge through the existing RAG "
            "retrieval service."
        ),
        structured_output=True,
    )
    def search_internal_knowledge(
        query: Annotated[str, Field(min_length=1, max_length=1000)],
        top_k: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> SearchInternalKnowledgeOutput:
        result = _execute_tool(
            executor,
            ToolName.SEARCH_INTERNAL_KNOWLEDGE.value,
            {"query": query, "top_k": top_k},
        )
        return SearchInternalKnowledgeOutput.model_validate(result.output)

    @server.tool(
        name=ToolName.GET_AGENT_RUN.value,
        description="Return a safe, auditable summary of one agent execution.",
        structured_output=True,
    )
    def get_agent_run(agent_run_id: UUID) -> GetAgentRunOutput:
        result = _execute_tool(
            executor,
            ToolName.GET_AGENT_RUN.value,
            {"agent_run_id": str(agent_run_id)},
        )
        return GetAgentRunOutput.model_validate(result.output)

    @server.tool(
        name=ToolName.GET_PIPELINE_SUMMARY.value,
        description=(
            "Return aggregate lead-classification and orchestration-route counts."
        ),
        structured_output=True,
    )
    def get_pipeline_summary() -> GetPipelineSummaryOutput:
        result = _execute_tool(
            executor,
            ToolName.GET_PIPELINE_SUMMARY.value,
            {},
        )
        return GetPipelineSummaryOutput.model_validate(result.output)

    for definition in executor.registry.definitions():
        registered = server._tool_manager.get_tool(definition.name.value)
        if registered is not None:
            registered.parameters = definition.input_schema

    return server


def main() -> None:
    configure_logging()
    logger.info(
        "mcp_server_started",
        extra={"transport": "stdio", "tool_count": len(ToolName)},
    )
    create_mcp_server(get_tool_execution_service()).run(transport="stdio")


if __name__ == "__main__":
    main()
