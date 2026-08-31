from typing import Protocol
from uuid import UUID

from supabase import Client

from app.core.exceptions import DatabaseUnavailableError
from app.models.lead import AgentRunRecord
from app.schemas.qualification import QualificationResult


class AgentRunRepository(Protocol):
    def create_started(
        self,
        *,
        lead_id: UUID,
        agent_type: str,
        model: str,
        input_data: dict[str, object],
    ) -> AgentRunRecord: ...

    def mark_completed(
        self,
        run_id: UUID,
        *,
        output: QualificationResult,
        latency_ms: int,
    ) -> AgentRunRecord: ...

    def mark_completed_payload(
        self,
        run_id: UUID,
        *,
        output: dict[str, object],
        latency_ms: int,
    ) -> AgentRunRecord: ...

    def mark_failed(
        self,
        run_id: UUID,
        *,
        error: str,
        latency_ms: int,
    ) -> AgentRunRecord: ...


class SupabaseAgentRunRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def create_started(
        self,
        *,
        lead_id: UUID,
        agent_type: str,
        model: str,
        input_data: dict[str, object],
    ) -> AgentRunRecord:
        values = {
            "lead_id": str(lead_id),
            "agent_type": agent_type,
            "model": model,
            "status": "started",
            "input": input_data,
        }
        return self._insert(values)

    def mark_completed(
        self,
        run_id: UUID,
        *,
        output: QualificationResult,
        latency_ms: int,
    ) -> AgentRunRecord:
        values = {
            "status": "completed",
            "output": output.model_dump(mode="json"),
            "error": None,
            "latency_ms": latency_ms,
        }
        return self._update(run_id, values)

    def mark_completed_payload(
        self,
        run_id: UUID,
        *,
        output: dict[str, object],
        latency_ms: int,
    ) -> AgentRunRecord:
        values = {
            "status": "completed",
            "output": output,
            "error": None,
            "latency_ms": latency_ms,
        }
        return self._update(run_id, values)

    def mark_failed(
        self,
        run_id: UUID,
        *,
        error: str,
        latency_ms: int,
    ) -> AgentRunRecord:
        values = {
            "status": "failed",
            "error": error[:1000],
            "latency_ms": latency_ms,
        }
        return self._update(run_id, values)

    def _insert(self, values: dict[str, object]) -> AgentRunRecord:
        try:
            response = self._client.table("agent_runs").insert(values).execute()
            return self._one(response.data, "create agent run")
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to create agent run") from exc

    def _update(self, run_id: UUID, values: dict[str, object]) -> AgentRunRecord:
        try:
            response = (
                self._client.table("agent_runs")
                .update(values)
                .eq("id", str(run_id))
                .execute()
            )
            return self._one(response.data, "update agent run")
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to update agent run") from exc

    @staticmethod
    def _one(data: object, operation: str) -> AgentRunRecord:
        if not isinstance(data, list) or not data:
            raise DatabaseUnavailableError(f"Database returned no row for {operation}")
        return AgentRunRecord.model_validate(data[0])
