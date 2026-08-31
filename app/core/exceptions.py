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
