from typing import Protocol
from uuid import UUID

from supabase import Client

from app.core.exceptions import DatabaseUnavailableError
from app.models.lead import AgentRunRecord, LeadRecord
from app.models.mcp import PipelineCounts
from app.models.orchestration import AgentStateTransitionRecord
from app.schemas.qualification import LeadClassification


class MCPDataRepository(Protocol):
    def get_lead(self, lead_id: UUID) -> LeadRecord | None: ...

    def search_leads(
        self,
        *,
        classification: LeadClassification | None,
        industry: str | None,
        country: str | None,
        company: str | None,
        limit: int,
    ) -> list[LeadRecord]: ...

    def get_lead_runs(self, lead_id: UUID) -> list[AgentRunRecord]: ...

    def get_lead_transitions(
        self, lead_id: UUID
    ) -> list[AgentStateTransitionRecord]: ...

    def get_agent_run(self, agent_run_id: UUID) -> AgentRunRecord | None: ...

    def get_pipeline_counts(self) -> PipelineCounts: ...


class SupabaseMCPDataRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def get_lead(self, lead_id: UUID) -> LeadRecord | None:
        try:
            response = (
                self._client.table("leads")
                .select("*")
                .eq("id", str(lead_id))
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to read lead for MCP tool") from exc
        if not response.data:
            return None
        return LeadRecord.model_validate(response.data[0])

    def search_leads(
        self,
        *,
        classification: LeadClassification | None,
        industry: str | None,
        country: str | None,
        company: str | None,
        limit: int,
    ) -> list[LeadRecord]:
        try:
            query = self._client.table("leads").select("*")
            if classification is not None:
                query = query.eq("classification", classification.value)
            if industry is not None:
                query = query.eq("industry", industry)
            if country is not None:
                query = query.eq("country", country)
            if company is not None:
                query = query.eq("company", company)
            response = query.order("created_at", desc=True).limit(limit).execute()
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to search leads for MCP tool") from exc
        if not isinstance(response.data, list):
            raise DatabaseUnavailableError("Lead search returned invalid data")
        return [LeadRecord.model_validate(row) for row in response.data]

    def get_lead_runs(self, lead_id: UUID) -> list[AgentRunRecord]:
        try:
            response = (
                self._client.table("agent_runs")
                .select("*")
                .eq("lead_id", str(lead_id))
                .order("created_at")
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to read lead runs") from exc
        if not isinstance(response.data, list):
            raise DatabaseUnavailableError("Lead runs returned invalid data")
        return [AgentRunRecord.model_validate(row) for row in response.data]

    def get_lead_transitions(
        self, lead_id: UUID
    ) -> list[AgentStateTransitionRecord]:
        try:
            response = (
                self._client.table("agent_state_transitions")
                .select("*")
                .eq("lead_id", str(lead_id))
                .order("created_at")
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to read lead transitions") from exc
        if not isinstance(response.data, list):
            raise DatabaseUnavailableError("Lead transitions returned invalid data")
        return [AgentStateTransitionRecord.model_validate(row) for row in response.data]

    def get_agent_run(self, agent_run_id: UUID) -> AgentRunRecord | None:
        try:
            response = (
                self._client.table("agent_runs")
                .select("*")
                .eq("id", str(agent_run_id))
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to read agent run") from exc
        if not response.data:
            return None
        return AgentRunRecord.model_validate(response.data[0])

    def get_pipeline_counts(self) -> PipelineCounts:
        try:
            return PipelineCounts(
                total_leads=self._count("leads"),
                hot=self._count("leads", column="classification", value="HOT"),
                warm=self._count("leads", column="classification", value="WARM"),
                cold=self._count("leads", column="classification", value="COLD"),
                research=self._count_route("research"),
                nurture=self._count_route("nurture"),
                stop=self._count_route("stop"),
            )
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to summarize pipeline") from exc

    def _count(
        self,
        table: str,
        *,
        column: str | None = None,
        value: str | None = None,
    ) -> int:
        query = self._client.table(table).select("id", count="exact", head=True)
        if column is not None and value is not None:
            query = query.eq(column, value)
        response = query.execute()
        if response.count is None:
            raise DatabaseUnavailableError("Database count was unavailable")
        return response.count

    def _count_route(self, route: str) -> int:
        query = (
            self._client.table("agent_runs")
            .select("id", count="exact", head=True)
            .eq("agent_type", "lead_orchestration")
            .contains("output", {"route": route})
        )
        response = query.execute()
        if response.count is None:
            raise DatabaseUnavailableError("Route count was unavailable")
        return response.count
