from typing import Protocol

from supabase import Client

from app.core.exceptions import DatabaseUnavailableError
from app.models.observability import AIUsageEventCreate, AIUsageEventRecord


class AIUsageRepository(Protocol):
    def create(self, event: AIUsageEventCreate) -> AIUsageEventRecord: ...


class SupabaseAIUsageRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def create(self, event: AIUsageEventCreate) -> AIUsageEventRecord:
        try:
            response = (
                self._client.table("ai_usage_events")
                .insert(event.model_dump(mode="json"))
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to record AI usage") from exc
        if not isinstance(response.data, list) or not response.data:
            raise DatabaseUnavailableError("AI usage insert returned no row")
        return AIUsageEventRecord.model_validate(response.data[0])
