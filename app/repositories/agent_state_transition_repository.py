from typing import Protocol
from uuid import UUID

from supabase import Client

from app.agents.state import AgentStateTransition
from app.core.exceptions import DatabaseUnavailableError
from app.models.orchestration import AgentStateTransitionRecord


class AgentStateTransitionRepository(Protocol):
    def create_many(
        self,
        *,
        agent_run_id: UUID,
        lead_id: UUID,
        transitions: list[AgentStateTransition],
    ) -> list[AgentStateTransitionRecord]: ...


class SupabaseAgentStateTransitionRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def create_many(
        self,
        *,
        agent_run_id: UUID,
        lead_id: UUID,
        transitions: list[AgentStateTransition],
    ) -> list[AgentStateTransitionRecord]:
        if not transitions:
            return []

        values = [
            {
                "agent_run_id": str(agent_run_id),
                "lead_id": str(lead_id),
                "from_state": transition.from_state.value,
                "to_state": transition.to_state.value,
                "route": transition.route.value if transition.route else None,
                "payload": transition.payload,
            }
            for transition in transitions
        ]

        try:
            response = (
                self._client.table("agent_state_transitions")
                .insert(values)
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError(
                "Failed to persist agent state transitions"
            ) from exc

        if not isinstance(response.data, list) or len(response.data) != len(values):
            raise DatabaseUnavailableError(
                "Database returned incomplete agent state transitions"
            )
        return [
            AgentStateTransitionRecord.model_validate(row) for row in response.data
        ]
