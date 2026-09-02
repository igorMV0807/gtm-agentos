import logging
from dataclasses import dataclass
from time import perf_counter
from typing import cast
from uuid import UUID

from pydantic import BaseModel, JsonValue, ValidationError

from app.core.exceptions import (
    GTMAgentOSError,
    ToolExecutionError,
    ToolInputInvalidError,
    ToolNotFoundError,
    ToolOutputInvalidError,
)
from app.mcp.registry import ToolRegistry
from app.models.mcp import ToolCallAuditCreate, ToolCallRecord, ToolCallStatus
from app.repositories.tool_call_repository import ToolCallRepository


logger = logging.getLogger(__name__)

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
_MAX_AUDIT_STRING = 4000
_MAX_AUDIT_LIST = 50
_MAX_AUDIT_DEPTH = 6


@dataclass(frozen=True)
class ToolExecutionResult:
    audit: ToolCallRecord
    output: BaseModel


class ToolExecutionService:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        audit_repository: ToolCallRepository,
    ) -> None:
        self._registry = registry
        self._audit_repository = audit_repository

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
        *,
        agent_run_id: UUID | None = None,
        lead_id: UUID | None = None,
    ) -> ToolExecutionResult:
        started = perf_counter()
        event_context = self._event_context(
            tool_name=tool_name,
            agent_run_id=agent_run_id,
            lead_id=lead_id,
        )
        logger.info("tool_call_started", extra=event_context)
        try:
            definition = self._registry.get(tool_name)
        except ToolNotFoundError as exc:
            latency_ms = self._elapsed_ms(started)
            logger.warning(
                "unknown_tool_rejected",
                extra={**event_context, "status": "rejected", "latency_ms": latency_ms},
            )
            logger.warning(
                "mcp_tool_rejected",
                extra={**event_context, "status": "rejected", "latency_ms": latency_ms},
            )
            self._audit(
                tool_name=tool_name,
                arguments=arguments,
                output=None,
                status=ToolCallStatus.REJECTED,
                error=exc.code,
                latency_ms=latency_ms,
                agent_run_id=agent_run_id,
                lead_id=lead_id,
            )
            raise

        try:
            validated_input = definition.input_model.model_validate(arguments)
        except ValidationError as exc:
            latency_ms = self._elapsed_ms(started)
            logger.warning(
                "tool_input_rejected",
                extra={**event_context, "status": "rejected", "latency_ms": latency_ms},
            )
            logger.warning(
                "mcp_tool_rejected",
                extra={**event_context, "status": "rejected", "latency_ms": latency_ms},
            )
            error = ToolInputInvalidError("Tool arguments violated the input schema")
            self._audit(
                tool_name=tool_name,
                arguments=arguments,
                output=None,
                status=ToolCallStatus.REJECTED,
                error=error.code,
                latency_ms=latency_ms,
                agent_run_id=agent_run_id,
                lead_id=lead_id,
            )
            raise error from exc

        try:
            raw_output = definition.handler(validated_input)
            try:
                validated_output = definition.output_model.model_validate(raw_output)
            except ValidationError as exc:
                raise ToolOutputInvalidError(
                    "Tool handler violated the output schema"
                ) from exc
        except GTMAgentOSError as exc:
            latency_ms = self._elapsed_ms(started)
            audit = self._audit(
                tool_name=tool_name,
                arguments=validated_input.model_dump(mode="json"),
                output=None,
                status=ToolCallStatus.FAILED,
                error=exc.code,
                latency_ms=latency_ms,
                agent_run_id=agent_run_id,
                lead_id=lead_id,
            )
            logger.warning(
                "tool_call_failed",
                extra={
                    **event_context,
                    "status": audit.status.value,
                    "latency_ms": latency_ms,
                    "error_code": exc.code,
                },
            )
            logger.warning(
                "mcp_tool_failed",
                extra={**event_context, "status": "failed", "latency_ms": latency_ms, "error_code": exc.code},
            )
            raise
        except Exception as exc:
            latency_ms = self._elapsed_ms(started)
            error = ToolExecutionError("Unexpected tool handler failure")
            audit = self._audit(
                tool_name=tool_name,
                arguments=validated_input.model_dump(mode="json"),
                output=None,
                status=ToolCallStatus.FAILED,
                error=error.code,
                latency_ms=latency_ms,
                agent_run_id=agent_run_id,
                lead_id=lead_id,
            )
            logger.error(
                "tool_call_failed",
                extra={
                    **event_context,
                    "status": audit.status.value,
                    "latency_ms": latency_ms,
                    "error_code": error.code,
                },
            )
            raise error from exc

        agent_run_id, lead_id = self._infer_context(
            validated_input,
            agent_run_id=agent_run_id,
            lead_id=lead_id,
        )
        event_context = self._event_context(
            tool_name=tool_name,
            agent_run_id=agent_run_id,
            lead_id=lead_id,
        )
        latency_ms = self._elapsed_ms(started)
        audit = self._audit(
            tool_name=tool_name,
            arguments=validated_input.model_dump(mode="json"),
            output=validated_output.model_dump(mode="json"),
            status=ToolCallStatus.COMPLETED,
            error=None,
            latency_ms=latency_ms,
            agent_run_id=agent_run_id,
            lead_id=lead_id,
        )
        logger.info(
            "tool_call_completed",
            extra={
                **event_context,
                "status": audit.status.value,
                "latency_ms": latency_ms,
            },
        )
        logger.info(
            "mcp_tool_completed",
            extra={
                **event_context,
                "status": audit.status.value,
                "latency_ms": latency_ms,
            },
        )
        return ToolExecutionResult(audit=audit, output=validated_output)

    def _audit(
        self,
        *,
        tool_name: str,
        arguments: dict[str, JsonValue],
        output: dict[str, JsonValue] | None,
        status: ToolCallStatus,
        error: str | None,
        latency_ms: int,
        agent_run_id: UUID | None,
        lead_id: UUID | None,
    ) -> ToolCallRecord:
        safe_input = cast(dict[str, JsonValue], sanitize_for_audit(arguments))
        safe_output = (
            cast(dict[str, JsonValue], sanitize_for_audit(output))
            if output is not None
            else None
        )
        return self._audit_repository.create(
            ToolCallAuditCreate(
                agent_run_id=agent_run_id,
                lead_id=lead_id,
                tool_name=tool_name,
                input=safe_input,
                output=safe_output,
                status=status,
                error=error,
                latency_ms=latency_ms,
            )
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((perf_counter() - started) * 1000))

    @staticmethod
    def _event_context(
        *,
        tool_name: str,
        agent_run_id: UUID | None,
        lead_id: UUID | None,
    ) -> dict[str, str]:
        context = {"tool": tool_name}
        if agent_run_id is not None:
            context["run_id"] = str(agent_run_id)
        if lead_id is not None:
            context["lead_id"] = str(lead_id)
        return context

    @staticmethod
    def _infer_context(
        payload: BaseModel,
        *,
        agent_run_id: UUID | None,
        lead_id: UUID | None,
    ) -> tuple[UUID | None, UUID | None]:
        if agent_run_id is None:
            candidate_run_id = getattr(payload, "agent_run_id", None)
            if isinstance(candidate_run_id, UUID):
                agent_run_id = candidate_run_id
        if lead_id is None:
            candidate_lead_id = getattr(payload, "lead_id", None)
            if isinstance(candidate_lead_id, UUID):
                lead_id = candidate_lead_id
        return agent_run_id, lead_id


def sanitize_for_audit(value: object, *, _depth: int = 0) -> JsonValue:
    if _depth >= _MAX_AUDIT_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:_MAX_AUDIT_STRING]
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for raw_key, item in list(value.items())[:_MAX_AUDIT_LIST]:
            key = str(raw_key)[:100]
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                result[key] = _REDACTED
            else:
                result[key] = sanitize_for_audit(item, _depth=_depth + 1)
        return result
    if isinstance(value, list | tuple):
        return [
            sanitize_for_audit(item, _depth=_depth + 1)
            for item in value[:_MAX_AUDIT_LIST]
        ]
    return str(value)[:_MAX_AUDIT_STRING]
