from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue
from supabase import Client

from app.core.exceptions import DatabaseUnavailableError
from app.models.external_actions import (
    ExternalActionEventRecord,
    ExternalActionRecord,
)
from app.schemas.external_actions import (
    ExternalActionCreate,
    ExternalActionEventType,
    ExternalActionStatus,
)


class ExternalActionRepository(Protocol):
    def create_or_get(
        self, action: ExternalActionCreate
    ) -> tuple[ExternalActionRecord, bool]: ...

    def get(self, action_id: UUID) -> ExternalActionRecord | None: ...

    def mark_approved(self, action_id: UUID) -> ExternalActionRecord | None: ...

    def mark_retry_approved(
        self, action_id: UUID
    ) -> ExternalActionRecord | None: ...

    def mark_rejected(self, action_id: UUID) -> ExternalActionRecord | None: ...

    def mark_executing(self, action_id: UUID) -> ExternalActionRecord | None: ...

    def set_external_reference(
        self, action_id: UUID, external_reference: str
    ) -> ExternalActionRecord | None: ...

    def mark_completed(
        self,
        action_id: UUID,
        *,
        external_reference: str | None,
        result: dict[str, JsonValue],
    ) -> ExternalActionRecord | None: ...

    def mark_failed(
        self,
        action_id: UUID,
        *,
        error: str,
        result: dict[str, JsonValue] | None = None,
    ) -> ExternalActionRecord | None: ...

    def create_event(
        self,
        *,
        action_id: UUID,
        event_type: ExternalActionEventType,
        metadata: dict[str, JsonValue] | None = None,
    ) -> ExternalActionEventRecord: ...


class SupabaseExternalActionRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def create_or_get(
        self, action: ExternalActionCreate
    ) -> tuple[ExternalActionRecord, bool]:
        values = action.model_dump(mode="json")
        values["status"] = ExternalActionStatus.PENDING.value
        try:
            response = (
                self._client.table("external_actions")
                .upsert(
                    values,
                    on_conflict="idempotency_key",
                    ignore_duplicates=True,
                )
                .execute()
            )
            if isinstance(response.data, list) and response.data:
                return ExternalActionRecord.model_validate(response.data[0]), True
            existing = self._get_by_idempotency_key(action.idempotency_key)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            raise DatabaseUnavailableError(
                "Failed to create external action"
            ) from exc
        if existing is None:
            raise DatabaseUnavailableError(
                "Database returned no row for idempotent external action"
            )
        return existing, False

    def get(self, action_id: UUID) -> ExternalActionRecord | None:
        try:
            response = (
                self._client.table("external_actions")
                .select("*")
                .eq("id", str(action_id))
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to read external action") from exc
        if not response.data:
            return None
        return ExternalActionRecord.model_validate(response.data[0])

    def mark_approved(self, action_id: UUID) -> ExternalActionRecord | None:
        return self._update(
            action_id,
            expected_statuses=(ExternalActionStatus.PENDING,),
            values={
                "status": ExternalActionStatus.APPROVED.value,
                "approved_at": self._now(),
                "executed_at": None,
                "error": None,
            },
        )

    def mark_retry_approved(
        self, action_id: UUID
    ) -> ExternalActionRecord | None:
        return self._update(
            action_id,
            expected_statuses=(ExternalActionStatus.FAILED,),
            values={
                "status": ExternalActionStatus.APPROVED.value,
                "executed_at": None,
                "error": None,
                "result": None,
            },
        )

    def mark_rejected(self, action_id: UUID) -> ExternalActionRecord | None:
        return self._update(
            action_id,
            expected_statuses=(ExternalActionStatus.PENDING,),
            values={"status": ExternalActionStatus.REJECTED.value},
        )

    def mark_executing(self, action_id: UUID) -> ExternalActionRecord | None:
        return self._update(
            action_id,
            expected_statuses=(ExternalActionStatus.APPROVED,),
            values={
                "status": ExternalActionStatus.EXECUTING.value,
                "executed_at": self._now(),
                "error": None,
            },
        )

    def set_external_reference(
        self, action_id: UUID, external_reference: str
    ) -> ExternalActionRecord | None:
        return self._update(
            action_id,
            expected_statuses=(ExternalActionStatus.EXECUTING,),
            values={"external_reference": external_reference[:500]},
        )

    def mark_completed(
        self,
        action_id: UUID,
        *,
        external_reference: str | None,
        result: dict[str, JsonValue],
    ) -> ExternalActionRecord | None:
        values: dict[str, object] = {
            "status": ExternalActionStatus.COMPLETED.value,
            "result": result,
            "error": None,
        }
        if external_reference:
            values["external_reference"] = external_reference[:500]
        return self._update(
            action_id,
            expected_statuses=(ExternalActionStatus.EXECUTING,),
            values=values,
        )

    def mark_failed(
        self,
        action_id: UUID,
        *,
        error: str,
        result: dict[str, JsonValue] | None = None,
    ) -> ExternalActionRecord | None:
        return self._update(
            action_id,
            expected_statuses=(
                ExternalActionStatus.APPROVED,
                ExternalActionStatus.EXECUTING,
            ),
            values={
                "status": ExternalActionStatus.FAILED.value,
                "executed_at": self._now(),
                "result": result,
                "error": error[:500],
            },
        )

    def create_event(
        self,
        *,
        action_id: UUID,
        event_type: ExternalActionEventType,
        metadata: dict[str, JsonValue] | None = None,
    ) -> ExternalActionEventRecord:
        values = {
            "action_id": str(action_id),
            "event_type": event_type.value,
            "metadata": metadata or {},
        }
        try:
            response = (
                self._client.table("external_action_events")
                .insert(values)
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError(
                "Failed to audit external action event"
            ) from exc
        if not isinstance(response.data, list) or not response.data:
            raise DatabaseUnavailableError(
                "Database returned no external action event"
            )
        return ExternalActionEventRecord.model_validate(response.data[0])

    def _get_by_idempotency_key(
        self, idempotency_key: str
    ) -> ExternalActionRecord | None:
        try:
            response = (
                self._client.table("external_actions")
                .select("*")
                .eq("idempotency_key", idempotency_key)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError(
                "Failed to read idempotent external action"
            ) from exc
        if not response.data:
            return None
        return ExternalActionRecord.model_validate(response.data[0])

    def _update(
        self,
        action_id: UUID,
        *,
        expected_statuses: tuple[ExternalActionStatus, ...],
        values: dict[str, object],
    ) -> ExternalActionRecord | None:
        update_values = {**values, "updated_at": self._now()}
        try:
            response = (
                self._client.table("external_actions")
                .update(update_values)
                .eq("id", str(action_id))
                .in_("status", [status.value for status in expected_statuses])
                .execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError(
                "Failed to transition external action"
            ) from exc
        if not response.data:
            return None
        return ExternalActionRecord.model_validate(response.data[0])

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
