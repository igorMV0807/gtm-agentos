from typing import Protocol

from supabase import Client

from app.core.exceptions import DatabaseUnavailableError
from app.models.mcp import ToolCallAuditCreate, ToolCallRecord


class ToolCallRepository(Protocol):
    def create(self, call: ToolCallAuditCreate) -> ToolCallRecord: ...


class SupabaseToolCallRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def create(self, call: ToolCallAuditCreate) -> ToolCallRecord:
        try:
            response = (
                self._client.table("tool_calls")
                .insert(call.model_dump(mode="json"))
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to audit MCP tool call") from exc
        if not isinstance(response.data, list) or not response.data:
            raise DatabaseUnavailableError("Tool audit returned no row")
        return ToolCallRecord.model_validate(response.data[0])
