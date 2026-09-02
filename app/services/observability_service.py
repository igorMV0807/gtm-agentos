import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.core.exceptions import AgentRunNotFoundError, LeadNotFoundError
from app.repositories.observability_repository import ObservabilityRepository
from app.schemas.observability import (
    AIMetrics,
    AIUsageItem,
    ActionItem,
    ActionMetrics,
    ActionsResponse,
    AgentMetrics,
    AgentRunItem,
    AgentRunsResponse,
    FailureItem,
    LeadMetrics,
    LeadTimelineResponse,
    OverviewResponse,
    RagEvidenceItem,
    RagMetrics,
    TimelineEvent,
    ToolCallItem,
    ToolMetrics,
    UsageResponse,
)


_SAFE_ERROR = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
_ACTION_PREVIEW_KEYS = {
    "send_approved_email": ("subject", "body", "reasoning_summary"),
    "draft_outreach_email": ("subject", "body", "reasoning_summary"),
    "create_or_update_crm_lead": ("name", "company", "job_title", "classification"),
    "create_follow_up_task": ("title", "description", "due_in_days"),
    "mark_lead_status": ("status",),
}
_EVENT_METADATA_KEYS = {
    "adapter",
    "draft_saved",
    "error_code",
    "requires_approval",
    "retry",
    "source",
    "status",
}


class ObservabilityService:
    def __init__(
        self, repository: ObservabilityRepository, *, demo_mode: bool = False
    ) -> None:
        self._repository = repository
        self._demo_mode = demo_mode

    def readiness(self) -> dict[str, str]:
        self._repository.ping()
        return {
            "status": "ready",
            "database": "demo" if self._demo_mode else "ok",
        }

    def overview(self) -> OverviewResponse:
        row = self._repository.overview()
        return OverviewResponse(
            leads=LeadMetrics(
                total=_integer(row, "total_leads"),
                hot=_integer(row, "hot_leads"),
                warm=_integer(row, "warm_leads"),
                cold=_integer(row, "cold_leads"),
            ),
            agents=AgentMetrics(
                runs=_integer(row, "total_agent_runs"),
                completed=_integer(row, "completed_runs"),
                failed=_integer(row, "failed_runs"),
                success_rate=_float(row, "success_rate"),
                average_latency_ms=_float(row, "average_agent_latency_ms"),
            ),
            rag=RagMetrics(
                retrievals=_integer(row, "total_retrievals"),
                average_similarity=_float(row, "average_similarity"),
                no_context=_integer(row, "no_context_count"),
            ),
            tools=ToolMetrics(
                calls=_integer(row, "total_tool_calls"),
                completed=_integer(row, "completed_tool_calls"),
                failed=_integer(row, "failed_tool_calls"),
                rejected=_integer(row, "rejected_tool_calls"),
                average_latency_ms=_float(row, "average_tool_latency_ms"),
            ),
            actions=ActionMetrics(
                pending=_integer(row, "pending_actions"),
                approved=_integer(row, "approved_actions"),
                completed=_integer(row, "completed_actions"),
                failed=_integer(row, "failed_actions"),
                rejected=_integer(row, "rejected_actions"),
                waiting_approval=_integer(row, "actions_waiting_approval"),
            ),
            ai=AIMetrics(
                events=_integer(row, "ai_usage_events"),
                total_tokens=_integer(row, "ai_total_tokens"),
                estimated_cost_usd=_decimal(row, "ai_estimated_cost_usd"),
                average_latency_ms=_float(row, "average_ai_latency_ms"),
            ),
            recent_failures=[
                FailureItem(
                    component=str(item.get("component") or "system")[:40],
                    error_code=_safe_error(item.get("error")),
                    timestamp=_datetime(item.get("created_at")),
                )
                for item in self._repository.recent_failures(8)
            ],
            demo_mode=self._demo_mode,
        )

    def list_agent_runs(self, *, limit: int, offset: int) -> AgentRunsResponse:
        rows = self._repository.list_agent_runs(limit, offset)
        has_more = len(rows) > limit
        return AgentRunsResponse(
            items=[self._run_item(row) for row in rows[:limit]],
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    def inspect_agent_run(self, run_id: UUID) -> AgentRunItem:
        row = self._repository.get_agent_run(run_id)
        if row is None:
            raise AgentRunNotFoundError("Agent run does not exist")
        return self._run_item(
            row,
            rag=self._repository.list_rag(run_id=run_id),
            tools=self._repository.list_tools(run_id=run_id),
            actions=self._repository.list_run_actions(run_id),
        )

    def list_actions(
        self, *, limit: int, offset: int, status: str | None
    ) -> ActionsResponse:
        rows = self._repository.list_actions(limit, offset, status)
        has_more = len(rows) > limit
        return ActionsResponse(
            items=[self._action_item(row) for row in rows[:limit]],
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    def lead_timeline(self, lead_id: UUID) -> LeadTimelineResponse:
        lead = self._repository.get_lead(lead_id)
        if lead is None:
            raise LeadNotFoundError("Lead does not exist")
        events = [
            TimelineEvent(
                component="lead",
                event="lead_created",
                status=str(lead.get("classification") or "new"),
                timestamp=_datetime(lead.get("created_at")),
                details={
                    "classification": lead.get("classification"),
                    "score": lead.get("score"),
                    "next_action": lead.get("next_action"),
                },
            )
        ]
        for row in self._repository.list_lead_runs(lead_id):
            output = row.get("output") if isinstance(row.get("output"), dict) else {}
            events.append(
                TimelineEvent(
                    component="agent",
                    event="agent_run",
                    status=str(row.get("status") or "unknown"),
                    timestamp=_datetime(row.get("created_at")),
                    details={
                        "agent_run_id": str(row.get("id")),
                        "agent_type": row.get("agent_type"),
                        "model": row.get("model"),
                        "classification": output.get("classification"),
                        "route": output.get("route"),
                        "latency_ms": row.get("latency_ms"),
                        "error_code": _safe_error(row.get("error")) if row.get("error") else None,
                    },
                )
            )
        for row in self._repository.list_transitions(lead_id):
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            events.append(
                TimelineEvent(
                    component="langgraph",
                    event=f"{row.get('from_state')} → {row.get('to_state')}",
                    status=str(payload.get("status")) if payload.get("status") else None,
                    timestamp=_datetime(row.get("created_at")),
                    details={
                        "route": row.get("route"),
                        "error_code": _safe_error(payload.get("error")) if payload.get("error") else None,
                    },
                )
            )
        for row in self._repository.list_rag(lead_id=lead_id):
            events.append(
                TimelineEvent(
                    component="rag",
                    event="rag_evidence_retrieved",
                    status="completed",
                    timestamp=_datetime(row.get("created_at")),
                    details={
                        "document_title": row.get("document_title"),
                        "similarity": row.get("similarity"),
                        "rank": row.get("rank"),
                    },
                )
            )
        for row in self._repository.list_tools(lead_id=lead_id):
            events.append(
                TimelineEvent(
                    component="mcp",
                    event=str(row.get("tool_name") or "tool_call"),
                    status=str(row.get("status") or "unknown"),
                    timestamp=_datetime(row.get("created_at")),
                    details={
                        "latency_ms": row.get("latency_ms"),
                        "error_code": _safe_error(row.get("error")) if row.get("error") else None,
                    },
                )
            )
        actions = self._repository.list_lead_actions(lead_id)
        for row in actions:
            events.append(
                TimelineEvent(
                    component="external_action",
                    event=str(row.get("action_type") or "action"),
                    status=str(row.get("status") or "unknown"),
                    timestamp=_datetime(row.get("created_at")),
                    details={
                        "action_id": str(row.get("id")),
                        "requires_approval": bool(row.get("requires_approval")),
                        "error_code": _safe_error(row.get("error")) if row.get("error") else None,
                    },
                )
            )
        action_ids = [UUID(str(row["id"])) for row in actions]
        for row in self._repository.list_action_events(action_ids):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            safe_metadata = {
                str(key): value
                for key, value in metadata.items()
                if str(key) in _EVENT_METADATA_KEYS
            }
            events.append(
                TimelineEvent(
                    component="external_action",
                    event=str(row.get("event_type") or "action_event"),
                    timestamp=_datetime(row.get("created_at")),
                    details={"action_id": str(row.get("action_id")), **safe_metadata},
                )
            )
        events.sort(
            key=lambda item: item.timestamp.isoformat() if item.timestamp else ""
        )
        return LeadTimelineResponse(
            lead_id=lead_id,
            lead_name=str(lead.get("name") or "Unknown lead"),
            company=str(lead.get("company") or "Unknown company"),
            events=events,
        )

    def usage(self, *, limit: int, offset: int) -> UsageResponse:
        summary = self._repository.usage_summary()
        rows = self._repository.list_usage(limit, offset)
        has_more = len(rows) > limit
        return UsageResponse(
            summary=AIMetrics(
                events=_integer(summary, "events"),
                total_tokens=_integer(summary, "total_tokens"),
                estimated_cost_usd=_decimal(summary, "estimated_cost_usd"),
                average_latency_ms=_float(summary, "average_latency_ms"),
            ),
            events=[
                AIUsageItem(
                    provider=str(row.get("provider") or "unknown"),
                    model=str(row.get("model") or "unknown"),
                    operation=str(row.get("operation") or "unknown"),
                    input_tokens=_optional_int(row.get("input_tokens")),
                    output_tokens=_optional_int(row.get("output_tokens")),
                    total_tokens=_optional_int(row.get("total_tokens")),
                    estimated_cost_usd=(
                        Decimal(str(row["estimated_cost_usd"]))
                        if row.get("estimated_cost_usd") is not None
                        else None
                    ),
                    latency_ms=max(0, int(row.get("latency_ms") or 0)),
                    timestamp=_datetime(row.get("created_at")),
                )
                for row in rows[:limit]
            ],
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    def _run_item(
        self,
        row: dict[str, object],
        *,
        rag: list[dict[str, object]] | None = None,
        tools: list[dict[str, object]] | None = None,
        actions: list[dict[str, object]] | None = None,
    ) -> AgentRunItem:
        output = row.get("output") if isinstance(row.get("output"), dict) else {}
        lead = row.get("leads") if isinstance(row.get("leads"), dict) else {}
        reason = output.get("reasoning_summary") or output.get("reason")
        return AgentRunItem(
            id=UUID(str(row["id"])),
            lead_id=UUID(str(row["lead_id"])),
            lead_name=str(lead.get("name")) if lead.get("name") else None,
            company=str(lead.get("company")) if lead.get("company") else None,
            agent_type=str(row.get("agent_type") or "unknown"),
            model=str(row.get("model") or "unknown"),
            status=str(row.get("status") or "unknown"),
            classification=str(output.get("classification")) if output.get("classification") else None,
            score=_optional_int(output.get("score")),
            route=str(output.get("route")) if output.get("route") else None,
            reasoning_summary=str(reason)[:500] if reason else None,
            latency_ms=_optional_int(row.get("latency_ms")),
            created_at=_datetime(row.get("created_at")),
            rag_evidence=[self._rag_item(item) for item in (rag or [])],
            tool_calls=[self._tool_item(item) for item in (tools or [])],
            external_actions=[self._action_item(item) for item in (actions or [])],
        )

    def _action_item(self, row: dict[str, object]) -> ActionItem:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        action_type = str(row.get("action_type") or "unknown")
        lead = row.get("leads") if isinstance(row.get("leads"), dict) else {}
        preview = {
            key: payload[key]
            for key in _ACTION_PREVIEW_KEYS.get(action_type, ())
            if key in payload and isinstance(payload[key], str | int | float | bool)
        }
        return ActionItem(
            id=UUID(str(row["id"])),
            lead_id=UUID(str(row["lead_id"])),
            agent_run_id=UUID(str(row["agent_run_id"])) if row.get("agent_run_id") else None,
            lead_name=str(lead.get("name")) if lead.get("name") else None,
            company=str(lead.get("company")) if lead.get("company") else None,
            action_type=action_type,
            status=str(row.get("status") or "unknown"),
            requires_approval=bool(row.get("requires_approval")),
            payload_preview=preview,
            created_at=_datetime(row.get("created_at")),
            updated_at=_datetime(row.get("updated_at")),
            error_code=_safe_error(row.get("error")) if row.get("error") else None,
            demo=self._demo_mode,
        )

    @staticmethod
    def _rag_item(row: dict[str, object]) -> RagEvidenceItem:
        return RagEvidenceItem(
            document_title=str(row.get("document_title") or "Internal knowledge"),
            similarity=max(0.0, min(1.0, float(row.get("similarity") or 0))),
            rank=max(1, int(row.get("rank") or 1)),
            timestamp=_datetime(row.get("created_at")),
        )

    @staticmethod
    def _tool_item(row: dict[str, object]) -> ToolCallItem:
        return ToolCallItem(
            tool_name=str(row.get("tool_name") or "unknown"),
            status=str(row.get("status") or "unknown"),
            latency_ms=max(0, int(row.get("latency_ms") or 0)),
            timestamp=_datetime(row.get("created_at")),
            error_code=_safe_error(row.get("error")) if row.get("error") else None,
        )


def _integer(row: dict[str, object], key: str) -> int:
    return max(0, int(row.get(key) or 0))


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _float(row: dict[str, object], key: str) -> float:
    return max(0.0, float(row.get(key) or 0))


def _decimal(row: dict[str, object], key: str) -> Decimal:
    return max(Decimal("0"), Decimal(str(row.get(key) or 0)))


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _safe_error(value: object) -> str:
    candidate = str(value or "unknown_error").strip().lower()
    return candidate if _SAFE_ERROR.fullmatch(candidate) else "internal_error"
