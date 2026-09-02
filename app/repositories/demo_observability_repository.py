from datetime import UTC, datetime, timedelta
from uuid import UUID


DEMO_LEAD_ID = UUID("11111111-1111-4111-8111-111111111111")
DEMO_RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
DEMO_ACTION_ID = UUID("33333333-3333-4333-8333-333333333333")


class DemoObservabilityRepository:
    """Explicitly synthetic, network-free portfolio data."""

    def __init__(self) -> None:
        self._now = datetime.now(UTC).replace(microsecond=0)

    def ping(self) -> None:
        return None

    def overview(self) -> dict[str, object]:
        return {
            "total_leads": 12,
            "hot_leads": 5,
            "warm_leads": 4,
            "cold_leads": 3,
            "total_agent_runs": 18,
            "completed_runs": 17,
            "failed_runs": 1,
            "success_rate": 17 / 18,
            "average_agent_latency_ms": 1834.4,
            "total_retrievals": 11,
            "average_similarity": 0.74,
            "no_context_count": 2,
            "total_tool_calls": 9,
            "completed_tool_calls": 7,
            "failed_tool_calls": 1,
            "rejected_tool_calls": 1,
            "average_tool_latency_ms": 122.5,
            "pending_actions": 1,
            "approved_actions": 0,
            "completed_actions": 6,
            "failed_actions": 1,
            "rejected_actions": 1,
            "actions_waiting_approval": 1,
            "ai_usage_events": 23,
            "ai_total_tokens": 18420,
            "ai_estimated_cost_usd": "0.18420000",
            "average_ai_latency_ms": 842.3,
        }

    def recent_failures(self, limit: int) -> list[dict[str, object]]:
        return [
            {
                "component": "external_action",
                "error": "provider_timeout",
                "created_at": self._now - timedelta(minutes=24),
            },
            {
                "component": "mcp",
                "error": "invalid_tool_input",
                "created_at": self._now - timedelta(hours=2),
            },
        ][:limit]

    def list_agent_runs(self, limit: int, offset: int) -> list[dict[str, object]]:
        rows = [self._run()]
        return rows[offset : offset + limit + 1]

    def get_agent_run(self, run_id: UUID) -> dict[str, object] | None:
        return self._run() if run_id == DEMO_RUN_ID else None

    def list_actions(
        self, limit: int, offset: int, status: str | None
    ) -> list[dict[str, object]]:
        rows = [self._action()]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        return rows[offset : offset + limit + 1]

    def get_lead(self, lead_id: UUID) -> dict[str, object] | None:
        if lead_id != DEMO_LEAD_ID:
            return None
        return {
            "id": str(DEMO_LEAD_ID),
            "name": "Jordan Lee (Demo)",
            "company": "Northstar SaaS (Demo)",
            "classification": "HOT",
            "score": 91,
            "qualification_reason": "Strong demo ICP fit.",
            "next_action": "personalized_outreach",
            "created_at": self._now - timedelta(days=1),
            "updated_at": self._now - timedelta(hours=20),
        }

    def list_lead_runs(self, lead_id: UUID) -> list[dict[str, object]]:
        return [self._run()] if lead_id == DEMO_LEAD_ID else []

    def list_transitions(self, lead_id: UUID) -> list[dict[str, object]]:
        if lead_id != DEMO_LEAD_ID:
            return []
        states = [
            ("START", "load_lead"),
            ("load_lead", "qualify_lead"),
            ("qualify_lead", "route_by_classification"),
            ("route_by_classification", "research_state"),
            ("research_state", "retrieve_gtm_knowledge"),
            ("retrieve_gtm_knowledge", "build_research_context"),
            ("build_research_context", "persist_agent_state"),
            ("persist_agent_state", "END"),
        ]
        return [
            {
                "id": f"transition-{index}",
                "agent_run_id": str(DEMO_RUN_ID),
                "from_state": source,
                "to_state": target,
                "route": "research",
                "payload": {"status": "completed"} if target == "END" else {},
                "created_at": self._now - timedelta(hours=22) + timedelta(seconds=index),
            }
            for index, (source, target) in enumerate(states)
        ]

    def list_rag(
        self, *, lead_id: UUID | None = None, run_id: UUID | None = None
    ) -> list[dict[str, object]]:
        if lead_id not in (None, DEMO_LEAD_ID) or run_id not in (None, DEMO_RUN_ID):
            return []
        return [
            {
                "id": "rag-1",
                "agent_run_id": str(DEMO_RUN_ID),
                "lead_id": str(DEMO_LEAD_ID),
                "document_title": "Sales Playbook",
                "similarity": 0.89,
                "rank": 1,
                "created_at": self._now - timedelta(hours=22),
            },
            {
                "id": "rag-2",
                "agent_run_id": str(DEMO_RUN_ID),
                "lead_id": str(DEMO_LEAD_ID),
                "document_title": "Ideal Customer Profile",
                "similarity": 0.82,
                "rank": 2,
                "created_at": self._now - timedelta(hours=22),
            },
        ]

    def list_tools(
        self, *, lead_id: UUID | None = None, run_id: UUID | None = None
    ) -> list[dict[str, object]]:
        if lead_id not in (None, DEMO_LEAD_ID) or run_id not in (None, DEMO_RUN_ID):
            return []
        return [
            {
                "id": "tool-1",
                "agent_run_id": str(DEMO_RUN_ID),
                "lead_id": str(DEMO_LEAD_ID),
                "tool_name": "search_internal_knowledge",
                "status": "completed",
                "error": None,
                "latency_ms": 146,
                "created_at": self._now - timedelta(hours=22),
            },
            {
                "id": "tool-2",
                "agent_run_id": str(DEMO_RUN_ID),
                "lead_id": str(DEMO_LEAD_ID),
                "tool_name": "delete_lead",
                "status": "rejected",
                "error": "unknown_tool",
                "latency_ms": 2,
                "created_at": self._now - timedelta(hours=21),
            },
        ]

    def list_run_actions(self, run_id: UUID) -> list[dict[str, object]]:
        return [self._action()] if run_id == DEMO_RUN_ID else []

    def list_lead_actions(self, lead_id: UUID) -> list[dict[str, object]]:
        return [self._action()] if lead_id == DEMO_LEAD_ID else []

    def list_action_events(self, action_ids: list[UUID]) -> list[dict[str, object]]:
        if DEMO_ACTION_ID not in action_ids:
            return []
        return [
            {
                "id": "event-1",
                "action_id": str(DEMO_ACTION_ID),
                "event_type": "action_requested",
                "metadata": {"requires_approval": True},
                "created_at": self._now - timedelta(hours=21),
            },
            {
                "id": "event-2",
                "action_id": str(DEMO_ACTION_ID),
                "event_type": "email_draft_created",
                "metadata": {"draft_saved": True},
                "created_at": self._now - timedelta(hours=21) + timedelta(seconds=1),
            },
        ]

    def usage_summary(self) -> dict[str, object]:
        return {
            "events": 23,
            "total_tokens": 18420,
            "estimated_cost_usd": "0.18420000",
            "average_latency_ms": 842.3,
        }

    def list_usage(self, limit: int, offset: int) -> list[dict[str, object]]:
        rows = [
            {
                "provider": "anthropic",
                "model": "demo-claude-model",
                "operation": "qualification",
                "input_tokens": 540,
                "output_tokens": 82,
                "total_tokens": 622,
                "estimated_cost_usd": "0.00740000",
                "latency_ms": 914,
                "created_at": self._now - timedelta(hours=22),
            },
            {
                "provider": "voyage",
                "model": "voyage-4",
                "operation": "embedding_query",
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": 37,
                "estimated_cost_usd": None,
                "latency_ms": 164,
                "created_at": self._now - timedelta(hours=22),
            },
        ]
        return rows[offset : offset + limit + 1]

    def _run(self) -> dict[str, object]:
        return {
            "id": str(DEMO_RUN_ID),
            "lead_id": str(DEMO_LEAD_ID),
            "agent_type": "lead_orchestration",
            "model": "demo-claude-model",
            "status": "completed",
            "output": {
                "classification": "HOT",
                "score": 91,
                "route": "research",
                "reason": "Strong demo ICP fit; no private chain-of-thought is stored.",
            },
            "error": None,
            "latency_ms": 1940,
            "created_at": self._now - timedelta(hours=22),
            "leads": {"name": "Jordan Lee (Demo)", "company": "Northstar SaaS (Demo)"},
        }

    def _action(self) -> dict[str, object]:
        return {
            "id": str(DEMO_ACTION_ID),
            "lead_id": str(DEMO_LEAD_ID),
            "agent_run_id": str(DEMO_RUN_ID),
            "action_type": "send_approved_email",
            "payload": {
                "to_email": "hidden@example.invalid",
                "subject": "A focused GTM pilot",
                "body": "Hi Jordan, this clearly marked demo draft uses grounded internal knowledge.",
                "reasoning_summary": "Uses demo ICP and sales playbook evidence.",
            },
            "status": "pending",
            "requires_approval": True,
            "error": None,
            "created_at": self._now - timedelta(hours=21),
            "updated_at": self._now - timedelta(hours=21),
            "leads": {"name": "Jordan Lee (Demo)", "company": "Northstar SaaS (Demo)"},
        }
