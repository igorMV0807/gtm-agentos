class GTMAgentOSError(Exception):
    """Base error carrying a safe response for API clients."""

    status_code = 500
    code = "internal_error"
    public_message = "An unexpected error occurred"

    def __init__(self, internal_message: str | None = None) -> None:
        self.internal_message = internal_message or self.public_message
        super().__init__(self.internal_message)


class ApplicationConfigurationError(GTMAgentOSError):
    status_code = 500
    code = "configuration_error"
    public_message = "The application is not configured correctly"


class DatabaseUnavailableError(GTMAgentOSError):
    status_code = 503
    code = "database_unavailable"
    public_message = "The database is temporarily unavailable"


class DuplicateLeadConflictError(GTMAgentOSError):
    """Internal signal used to recover from concurrent duplicate inserts."""


class LLMTimeoutError(GTMAgentOSError):
    status_code = 504
    code = "llm_timeout"
    public_message = "The qualification provider timed out"


class LLMInvalidResponseError(GTMAgentOSError):
    status_code = 502
    code = "llm_invalid_response"
    public_message = "The qualification provider returned an invalid response"


class LLMProviderError(GTMAgentOSError):
    status_code = 502
    code = "llm_provider_error"
    public_message = "The qualification provider is temporarily unavailable"


class AgentStateInvalidError(GTMAgentOSError):
    status_code = 500
    code = "agent_state_invalid"
    public_message = "The agent produced an invalid state"


class AgentRouteInvalidError(GTMAgentOSError):
    status_code = 500
    code = "agent_route_invalid"
    public_message = "The agent selected an invalid route"


class AgentGraphExecutionError(GTMAgentOSError):
    status_code = 500
    code = "agent_graph_error"
    public_message = "The agent workflow could not be completed"


class EmbeddingTimeoutError(GTMAgentOSError):
    status_code = 504
    code = "embedding_timeout"
    public_message = "The embedding provider timed out"


class EmbeddingProviderError(GTMAgentOSError):
    status_code = 502
    code = "embedding_provider_error"
    public_message = "The embedding provider is temporarily unavailable"


class EmbeddingInvalidResponseError(GTMAgentOSError):
    status_code = 502
    code = "embedding_invalid_response"
    public_message = "The embedding provider returned an invalid response"


class VectorSearchError(GTMAgentOSError):
    status_code = 503
    code = "vector_search_failed"
    public_message = "The internal knowledge search is temporarily unavailable"


class KnowledgeIngestionError(GTMAgentOSError):
    status_code = 500
    code = "knowledge_ingestion_failed"
    public_message = "The knowledge document could not be ingested"


class ToolNotFoundError(GTMAgentOSError):
    status_code = 404
    code = "unknown_tool"
    public_message = "The requested tool is not available"


class ToolInputInvalidError(GTMAgentOSError):
    status_code = 422
    code = "invalid_tool_input"
    public_message = "The tool input is invalid"


class ToolOutputInvalidError(GTMAgentOSError):
    status_code = 502
    code = "invalid_tool_output"
    public_message = "The tool returned an invalid result"


class ToolExecutionError(GTMAgentOSError):
    status_code = 500
    code = "tool_execution_failed"
    public_message = "The tool could not be executed"


class LeadNotFoundError(GTMAgentOSError):
    status_code = 404
    code = "lead_not_found"
    public_message = "The requested lead was not found"


class AgentRunNotFoundError(GTMAgentOSError):
    status_code = 404
    code = "agent_run_not_found"
    public_message = "The requested agent run was not found"
