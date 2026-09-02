import logging
from typing import cast
from uuid import UUID

from pydantic import JsonValue, ValidationError

from app.core.exceptions import (
    ApplicationConfigurationError,
    ExternalActionConflictError,
    ExternalActionInvalidError,
    ExternalActionNotFoundError,
    ExternalIntegrationError,
    GTMAgentOSError,
)
from app.integrations.n8n import N8nActionDispatcher
from app.models.external_actions import ExternalActionRecord
from app.repositories.external_action_repository import ExternalActionRepository
from app.schemas.external_actions import (
    ExternalActionCreate,
    ExternalActionEventType,
    ExternalActionResponse,
    ExternalActionStatus,
    ExternalActionType,
    N8nCallbackPayload,
    validate_action_payload,
)


logger = logging.getLogger(__name__)

_APPROVAL_POLICY = {
    ExternalActionType.CREATE_OR_UPDATE_CRM_LEAD: True,
    ExternalActionType.CREATE_FOLLOW_UP_TASK: True,
    ExternalActionType.DRAFT_OUTREACH_EMAIL: False,
    ExternalActionType.SEND_APPROVED_EMAIL: True,
    ExternalActionType.MARK_LEAD_STATUS: True,
}
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
_REDACTED = "[REDACTED]"


class ExternalActionService:
    def __init__(
        self,
        *,
        repository: ExternalActionRepository,
        n8n_dispatcher: N8nActionDispatcher | None = None,
    ) -> None:
        self._repository = repository
        self._n8n_dispatcher = n8n_dispatcher

    def request_action(
        self,
        *,
        lead_id: UUID,
        agent_run_id: UUID | None,
        action_type: ExternalActionType,
        payload: dict[str, JsonValue],
        idempotency_key: str,
    ) -> ExternalActionRecord:
        try:
            normalized_payload = validate_action_payload(action_type, payload)
            safe_payload = cast(
                dict[str, JsonValue], sanitize_external_data(normalized_payload)
            )
            create = ExternalActionCreate(
                lead_id=lead_id,
                agent_run_id=agent_run_id,
                action_type=action_type,
                payload=safe_payload,
                requires_approval=_APPROVAL_POLICY[action_type],
                idempotency_key=idempotency_key,
            )
        except (KeyError, ValidationError, ValueError, TypeError) as exc:
            raise ExternalActionInvalidError(
                "External action payload violated the allowlisted schema"
            ) from exc

        action, created = self._repository.create_or_get(create)
        if action.lead_id != lead_id or action.action_type != action_type:
            raise ExternalActionConflictError(
                "Idempotency key belongs to a different action"
            )
        if not created:
            return action

        self._repository.create_event(
            action_id=action.id,
            event_type=ExternalActionEventType.ACTION_REQUESTED,
            metadata={"requires_approval": action.requires_approval},
        )
        if action_type == ExternalActionType.SEND_APPROVED_EMAIL:
            self._repository.create_event(
                action_id=action.id,
                event_type=ExternalActionEventType.EMAIL_DRAFT_CREATED,
                metadata={"draft_saved": True},
            )
            logger.info(
                "email_draft_created",
                extra={"action_id": str(action.id), "lead_id": str(lead_id)},
            )
        logger.info(
            "external_action_created",
            extra={
                "action_id": str(action.id),
                "lead_id": str(lead_id),
                "action_type": action.action_type.value,
                "requires_approval": action.requires_approval,
            },
        )
        logger.info(
            "external_action_pending",
            extra={
                "action_id": str(action.id),
                "lead_id": str(lead_id),
                "status": action.status.value,
            },
        )
        return action

    def approve(self, action_id: UUID) -> ExternalActionRecord:
        action = self._require_action(action_id)
        if action.status in {
            ExternalActionStatus.EXECUTING,
            ExternalActionStatus.COMPLETED,
        }:
            return action
        if action.status == ExternalActionStatus.REJECTED:
            raise ExternalActionConflictError("Rejected actions cannot be approved")

        retry = action.status == ExternalActionStatus.FAILED
        if action.status == ExternalActionStatus.PENDING:
            approved = self._repository.mark_approved(action.id)
        elif retry:
            approved = self._repository.mark_retry_approved(action.id)
        elif action.status == ExternalActionStatus.APPROVED:
            approved = action
        else:
            approved = None

        if approved is None:
            current = self._require_action(action.id)
            if current.status in {
                ExternalActionStatus.EXECUTING,
                ExternalActionStatus.COMPLETED,
            }:
                return current
            raise ExternalActionConflictError("Action approval lost a state race")

        if action.status != ExternalActionStatus.APPROVED:
            self._repository.create_event(
                action_id=approved.id,
                event_type=ExternalActionEventType.APPROVAL_GRANTED,
                metadata={"retry": retry},
            )
            logger.info(
                "external_action_approved",
                extra={"action_id": str(approved.id), "retry": retry},
            )
        return self._dispatch(approved)

    def reject(self, action_id: UUID) -> ExternalActionRecord:
        action = self._require_action(action_id)
        if action.status == ExternalActionStatus.REJECTED:
            return action
        if action.status != ExternalActionStatus.PENDING:
            raise ExternalActionConflictError("Only pending actions can be rejected")
        rejected = self._repository.mark_rejected(action.id)
        if rejected is None:
            raise ExternalActionConflictError("Action rejection lost a state race")
        self._repository.create_event(
            action_id=rejected.id,
            event_type=ExternalActionEventType.ACTION_REJECTED,
            metadata={},
        )
        logger.info(
            "external_action_rejected",
            extra={"action_id": str(rejected.id)},
        )
        return rejected

    def process_callback(self, payload: N8nCallbackPayload) -> ExternalActionRecord:
        action = self._require_action(payload.action_id)
        safe_metadata = cast(
            dict[str, JsonValue], sanitize_external_data(payload.metadata)
        )
        for reserved_key in (
            "action_id",
            "action_type",
            "external_reference",
            "status",
        ):
            safe_metadata.pop(reserved_key, None)
        self._repository.create_event(
            action_id=action.id,
            event_type=ExternalActionEventType.CALLBACK_RECEIVED,
            metadata={**safe_metadata, "status": payload.status},
        )
        logger.info(
            "n8n_callback_received",
            extra={"action_id": str(action.id), "callback_status": payload.status},
        )

        if (
            action.status == ExternalActionStatus.COMPLETED
            and payload.status == "completed"
        ) or (
            action.status == ExternalActionStatus.FAILED
            and payload.status == "failed"
        ):
            return action
        if action.status != ExternalActionStatus.EXECUTING:
            raise ExternalActionConflictError(
                "Callback requires an executing external action"
            )

        if payload.status == "completed":
            updated = self._repository.mark_completed(
                action.id,
                external_reference=payload.external_reference,
                result=safe_metadata,
            )
            event_type = ExternalActionEventType.EXECUTION_COMPLETED
            log_event = "external_action_completed"
        else:
            updated = self._repository.mark_failed(
                action.id,
                error="n8n_callback_failed",
                result=safe_metadata,
            )
            event_type = ExternalActionEventType.EXECUTION_FAILED
            log_event = "external_action_failed"
        if updated is None:
            current = self._require_action(action.id)
            expected_terminal = (
                ExternalActionStatus.COMPLETED
                if payload.status == "completed"
                else ExternalActionStatus.FAILED
            )
            if current.status == expected_terminal:
                return current
            raise ExternalActionConflictError("Callback lost a state race")
        self._repository.create_event(
            action_id=updated.id,
            event_type=event_type,
            metadata={"source": "n8n_callback"},
        )
        logger.info(log_event, extra={"action_id": str(updated.id)})
        if payload.status == "completed":
            if action.action_type == ExternalActionType.SEND_APPROVED_EMAIL:
                logger.info(
                    "email_sent",
                    extra={"action_id": str(updated.id), "lead_id": str(action.lead_id)},
                )
            elif action.action_type in {
                ExternalActionType.CREATE_OR_UPDATE_CRM_LEAD,
                ExternalActionType.CREATE_FOLLOW_UP_TASK,
                ExternalActionType.MARK_LEAD_STATUS,
            }:
                logger.info(
                    "crm_action_completed",
                    extra={"action_id": str(updated.id), "lead_id": str(action.lead_id)},
                )
        return updated

    def _dispatch(self, action: ExternalActionRecord) -> ExternalActionRecord:
        if self._n8n_dispatcher is None:
            raise ApplicationConfigurationError("n8n dispatcher is not configured")
        executing = self._repository.mark_executing(action.id)
        if executing is None:
            current = self._require_action(action.id)
            if current.status in {
                ExternalActionStatus.EXECUTING,
                ExternalActionStatus.COMPLETED,
            }:
                return current
            raise ExternalActionConflictError("Action execution lost a state race")
        self._repository.create_event(
            action_id=executing.id,
            event_type=ExternalActionEventType.EXECUTION_STARTED,
            metadata={"adapter": "n8n"},
        )
        try:
            dispatch = self._n8n_dispatcher.execute_action(executing)
            if not dispatch.accepted:
                raise ExternalIntegrationError("n8n did not accept the action")
        except GTMAgentOSError as exc:
            self._record_dispatch_failure(executing.id, exc.code)
            raise
        except Exception as exc:
            error = ExternalIntegrationError("Unexpected n8n adapter failure")
            self._record_dispatch_failure(executing.id, error.code)
            raise error from exc

        if dispatch.external_reference:
            updated = self._repository.set_external_reference(
                executing.id, dispatch.external_reference
            )
            if updated is not None:
                executing = updated
        return executing

    def _record_dispatch_failure(self, action_id: UUID, error_code: str) -> None:
        failed = self._repository.mark_failed(action_id, error=error_code)
        if failed is not None:
            self._repository.create_event(
                action_id=failed.id,
                event_type=ExternalActionEventType.EXECUTION_FAILED,
                metadata={"error_code": error_code},
            )
        logger.warning(
            "external_action_failed",
            extra={"action_id": str(action_id), "error_code": error_code},
        )

    def _require_action(self, action_id: UUID) -> ExternalActionRecord:
        action = self._repository.get(action_id)
        if action is None:
            raise ExternalActionNotFoundError("External action does not exist")
        return action


def external_action_response(action: ExternalActionRecord) -> ExternalActionResponse:
    return ExternalActionResponse(
        id=action.id,
        lead_id=action.lead_id,
        agent_run_id=action.agent_run_id,
        action_type=action.action_type,
        status=action.status,
        requires_approval=action.requires_approval,
        idempotency_key=action.idempotency_key,
        external_reference=action.external_reference,
        error=action.error,
    )


def sanitize_external_data(value: object, *, depth: int = 0) -> JsonValue:
    if depth >= 6:
        return "[TRUNCATED]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:5000]
    if isinstance(value, dict):
        safe: dict[str, JsonValue] = {}
        for raw_key, item in list(value.items())[:50]:
            key = str(raw_key)[:100]
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                safe[key] = _REDACTED
            else:
                safe[key] = sanitize_external_data(item, depth=depth + 1)
        return safe
    if isinstance(value, list | tuple):
        return [
            sanitize_external_data(item, depth=depth + 1)
            for item in value[:50]
        ]
    return str(value)[:5000]
