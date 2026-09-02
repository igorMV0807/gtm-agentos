import logging
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
from typing import Protocol
from uuid import UUID

from app.core.ai_pricing import AIPricingCatalog
from app.models.observability import AIUsageEventCreate, AIUsageEventRecord
from app.repositories.ai_usage_repository import AIUsageRepository


logger = logging.getLogger(__name__)
_USAGE_CONTEXT: ContextVar[tuple[UUID | None, UUID | None]] = ContextVar(
    "ai_usage_context", default=(None, None)
)


class AIUsageTracker(Protocol):
    def record(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        latency_ms: int,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        lead_id: UUID | None = None,
        agent_run_id: UUID | None = None,
    ) -> AIUsageEventRecord | None: ...


class AIUsageService:
    def __init__(
        self,
        *,
        repository: AIUsageRepository,
        pricing: AIPricingCatalog,
    ) -> None:
        self._repository = repository
        self._pricing = pricing

    @contextmanager
    def context(
        self, *, lead_id: UUID | None, agent_run_id: UUID | None
    ) -> Iterator[None]:
        token = _USAGE_CONTEXT.set((lead_id, agent_run_id))
        try:
            yield
        finally:
            _USAGE_CONTEXT.reset(token)

    def record(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        latency_ms: int,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        lead_id: UUID | None = None,
        agent_run_id: UUID | None = None,
    ) -> AIUsageEventRecord | None:
        context_lead_id, context_run_id = _USAGE_CONTEXT.get()
        lead_id = lead_id or context_lead_id
        agent_run_id = agent_run_id or context_run_id
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        event = AIUsageEventCreate(
            lead_id=lead_id,
            agent_run_id=agent_run_id,
            provider=provider,
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=self._pricing.estimate(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
            latency_ms=latency_ms,
        )
        try:
            created = self._repository.create(event)
        except Exception:
            logger.warning(
                "ai_usage_record_failed",
                extra={"provider": provider, "model": model, "operation": operation},
            )
            return None
        logger.info(
            "ai_usage_recorded",
            extra={
                "provider": provider,
                "model": model,
                "operation": operation,
                "latency_ms": latency_ms,
            },
        )
        return created
