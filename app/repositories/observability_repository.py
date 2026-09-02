from typing import Protocol
from uuid import UUID

from supabase import Client

from app.core.exceptions import DatabaseUnavailableError


class ObservabilityRepository(Protocol):
    def ping(self) -> None: ...
    def overview(self) -> dict[str, object]: ...
    def recent_failures(self, limit: int) -> list[dict[str, object]]: ...
    def list_agent_runs(self, limit: int, offset: int) -> list[dict[str, object]]: ...
    def get_agent_run(self, run_id: UUID) -> dict[str, object] | None: ...
    def list_actions(
        self, limit: int, offset: int, status: str | None
    ) -> list[dict[str, object]]: ...
    def get_lead(self, lead_id: UUID) -> dict[str, object] | None: ...
    def list_lead_runs(self, lead_id: UUID) -> list[dict[str, object]]: ...
    def list_transitions(self, lead_id: UUID) -> list[dict[str, object]]: ...
    def list_rag(
        self, *, lead_id: UUID | None = None, run_id: UUID | None = None
    ) -> list[dict[str, object]]: ...
    def list_tools(
        self, *, lead_id: UUID | None = None, run_id: UUID | None = None
    ) -> list[dict[str, object]]: ...
    def list_run_actions(self, run_id: UUID) -> list[dict[str, object]]: ...
    def list_lead_actions(self, lead_id: UUID) -> list[dict[str, object]]: ...
    def list_action_events(self, action_ids: list[UUID]) -> list[dict[str, object]]: ...
    def usage_summary(self) -> dict[str, object]: ...
    def list_usage(self, limit: int, offset: int) -> list[dict[str, object]]: ...


class SupabaseObservabilityRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def ping(self) -> None:
        try:
            self._client.table("leads").select("id").limit(1).execute()
        except Exception as exc:
            raise DatabaseUnavailableError("Database readiness check failed") from exc

    def overview(self) -> dict[str, object]:
        rows = self._rows(
            self._client.table("observability_overview").select("*").limit(1),
            "read observability overview",
        )
        if not rows:
            raise DatabaseUnavailableError("Observability overview returned no row")
        return rows[0]

    def recent_failures(self, limit: int) -> list[dict[str, object]]:
        per_source = max(1, min(limit, 20))
        specs = (
            ("agent_runs", "agent", "id,error,created_at", "failed"),
            ("tool_calls", "mcp", "id,error,created_at", "failed"),
            ("external_actions", "external_action", "id,error,updated_at", "failed"),
        )
        failures: list[dict[str, object]] = []
        for table, component, columns, status in specs:
            rows = self._rows(
                self._client.table(table)
                .select(columns)
                .eq("status", status)
                .order("created_at" if table != "external_actions" else "updated_at", desc=True)
                .limit(per_source),
                f"read {component} failures",
            )
            for row in rows:
                failures.append(
                    {
                        "component": component,
                        "error": row.get("error"),
                        "created_at": row.get("created_at") or row.get("updated_at"),
                    }
                )
        return sorted(
            failures,
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        )[:limit]

    def list_agent_runs(self, limit: int, offset: int) -> list[dict[str, object]]:
        return self._rows(
            self._client.table("agent_runs")
            .select("id,lead_id,agent_type,model,status,output,error,latency_ms,created_at,leads(name,company)")
            .order("created_at", desc=True)
            .range(offset, offset + limit),
            "list agent runs",
        )

    def get_agent_run(self, run_id: UUID) -> dict[str, object] | None:
        rows = self._rows(
            self._client.table("agent_runs")
            .select("id,lead_id,agent_type,model,status,output,error,latency_ms,created_at,leads(name,company)")
            .eq("id", str(run_id))
            .limit(1),
            "read agent run",
        )
        return rows[0] if rows else None

    def list_actions(
        self, limit: int, offset: int, status: str | None
    ) -> list[dict[str, object]]:
        query = self._client.table("external_actions").select(
            "id,lead_id,agent_run_id,action_type,payload,status,requires_approval,error,created_at,updated_at,leads(name,company)"
        )
        if status is not None:
            query = query.eq("status", status)
        return self._rows(
            query.order("created_at", desc=True).range(offset, offset + limit),
            "list external actions",
        )

    def get_lead(self, lead_id: UUID) -> dict[str, object] | None:
        rows = self._rows(
            self._client.table("leads")
            .select("id,name,company,classification,score,qualification_reason,next_action,created_at,updated_at")
            .eq("id", str(lead_id))
            .limit(1),
            "read lead timeline header",
        )
        return rows[0] if rows else None

    def list_lead_runs(self, lead_id: UUID) -> list[dict[str, object]]:
        return self._rows(
            self._client.table("agent_runs")
            .select("id,lead_id,agent_type,model,status,output,error,latency_ms,created_at")
            .eq("lead_id", str(lead_id))
            .order("created_at"),
            "read lead runs",
        )

    def list_transitions(self, lead_id: UUID) -> list[dict[str, object]]:
        return self._rows(
            self._client.table("agent_state_transitions")
            .select("id,agent_run_id,from_state,to_state,route,payload,created_at")
            .eq("lead_id", str(lead_id))
            .order("created_at"),
            "read lead transitions",
        )

    def list_rag(
        self, *, lead_id: UUID | None = None, run_id: UUID | None = None
    ) -> list[dict[str, object]]:
        query = self._client.table("rag_retrievals").select(
            "id,agent_run_id,lead_id,chunk_id,similarity,rank,created_at"
        )
        if lead_id is not None:
            query = query.eq("lead_id", str(lead_id))
        if run_id is not None:
            query = query.eq("agent_run_id", str(run_id))
        rows = self._rows(query.order("created_at"), "read RAG evidence")
        if not rows:
            return []
        chunk_ids = [str(row["chunk_id"]) for row in rows]
        chunks = self._rows(
            self._client.table("knowledge_chunks")
            .select("id,document_id")
            .in_("id", chunk_ids),
            "read RAG chunk references",
        )
        document_ids = list({str(row["document_id"]) for row in chunks})
        documents = self._rows(
            self._client.table("knowledge_documents")
            .select("id,title")
            .in_("id", document_ids),
            "read RAG document titles",
        ) if document_ids else []
        chunk_documents = {str(row["id"]): str(row["document_id"]) for row in chunks}
        titles = {str(row["id"]): str(row["title"]) for row in documents}
        for row in rows:
            document_id = chunk_documents.get(str(row["chunk_id"]))
            row["document_title"] = titles.get(document_id or "", "Internal knowledge")
        return rows

    def list_tools(
        self, *, lead_id: UUID | None = None, run_id: UUID | None = None
    ) -> list[dict[str, object]]:
        query = self._client.table("tool_calls").select(
            "id,agent_run_id,lead_id,tool_name,status,error,latency_ms,created_at"
        )
        if lead_id is not None:
            query = query.eq("lead_id", str(lead_id))
        if run_id is not None:
            query = query.eq("agent_run_id", str(run_id))
        return self._rows(query.order("created_at"), "read MCP tool calls")

    def list_run_actions(self, run_id: UUID) -> list[dict[str, object]]:
        return self._rows(
            self._client.table("external_actions")
            .select("id,lead_id,agent_run_id,action_type,payload,status,requires_approval,error,created_at,updated_at")
            .eq("agent_run_id", str(run_id))
            .order("created_at"),
            "read run external actions",
        )

    def list_lead_actions(self, lead_id: UUID) -> list[dict[str, object]]:
        return self._rows(
            self._client.table("external_actions")
            .select("id,lead_id,agent_run_id,action_type,payload,status,requires_approval,error,created_at,updated_at")
            .eq("lead_id", str(lead_id))
            .order("created_at"),
            "read lead external actions",
        )

    def list_action_events(self, action_ids: list[UUID]) -> list[dict[str, object]]:
        if not action_ids:
            return []
        return self._rows(
            self._client.table("external_action_events")
            .select("id,action_id,event_type,metadata,created_at")
            .in_("action_id", [str(value) for value in action_ids])
            .order("created_at"),
            "read external action events",
        )

    def usage_summary(self) -> dict[str, object]:
        rows = self._rows(
            self._client.table("ai_usage_summary").select("*").limit(1),
            "read AI usage summary",
        )
        if not rows:
            raise DatabaseUnavailableError("AI usage summary returned no row")
        return rows[0]

    def list_usage(self, limit: int, offset: int) -> list[dict[str, object]]:
        return self._rows(
            self._client.table("ai_usage_events")
            .select("provider,model,operation,input_tokens,output_tokens,total_tokens,estimated_cost_usd,latency_ms,created_at")
            .order("created_at", desc=True)
            .range(offset, offset + limit),
            "list AI usage events",
        )

    @staticmethod
    def _rows(query: object, operation: str) -> list[dict[str, object]]:
        try:
            response = query.execute()  # type: ignore[attr-defined]
        except Exception as exc:
            raise DatabaseUnavailableError(f"Failed to {operation}") from exc
        if not isinstance(response.data, list):
            raise DatabaseUnavailableError(f"Database returned invalid data for {operation}")
        return response.data
