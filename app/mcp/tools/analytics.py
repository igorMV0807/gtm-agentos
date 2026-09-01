from app.core.exceptions import AgentRunNotFoundError
from app.mcp.schemas import (
    GetAgentRunInput,
    GetAgentRunOutput,
    GetPipelineSummaryInput,
    GetPipelineSummaryOutput,
)
from app.mcp.tools.common import safe_agent_run
from app.repositories.mcp_repository import MCPDataRepository


class AnalyticsTools:
    def __init__(self, repository: MCPDataRepository) -> None:
        self._repository = repository

    def get_agent_run(self, payload: GetAgentRunInput) -> GetAgentRunOutput:
        run = self._repository.get_agent_run(payload.agent_run_id)
        if run is None:
            raise AgentRunNotFoundError("Agent run does not exist")
        return GetAgentRunOutput(run=safe_agent_run(run))

    def get_pipeline_summary(
        self, payload: GetPipelineSummaryInput
    ) -> GetPipelineSummaryOutput:
        del payload
        counts = self._repository.get_pipeline_counts()
        return GetPipelineSummaryOutput.model_validate(counts.model_dump())
