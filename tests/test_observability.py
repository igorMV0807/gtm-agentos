import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_external_action_service,
    get_observability_service,
    get_webhook_signer,
)
from app.core.ai_pricing import AIPricingCatalog, ModelPrice
from app.core.config import Settings, get_settings
from app.core.exceptions import LeadNotFoundError
from app.core.logging import JsonFormatter
from app.integrations.n8n import WebhookSigner
from app.main import app
from app.models.observability import AIUsageEventCreate, AIUsageEventRecord
from app.repositories.demo_observability_repository import (
    DEMO_LEAD_ID,
    DEMO_RUN_ID,
    DemoObservabilityRepository,
)
from app.services.ai_usage_service import AIUsageService
from app.services.embedding_service import VoyageEmbeddingProvider
from app.services.llm_service import AnthropicLLMService
from app.services.observability_service import ObservabilityService
from app.schemas.lead import LeadQualifyRequest
from app.schemas.qualification import (
    LeadClassification,
    NextAction,
    QualificationResult,
)
from tests.test_external_actions import _create_email_action, _service_pair


OPERATOR_KEY = "phase-six-test-operator-key-0001"


@pytest.fixture
def operator_client() -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        operator_api_key=OPERATOR_KEY,
        portfolio_mode=True,
    )
    service = ObservabilityService(
        DemoObservabilityRepository(), demo_mode=True
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_observability_service] = lambda: service
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _operator_headers() -> dict[str, str]:
    return {"X-Operator-Key": OPERATOR_KEY}


def _login(client: TestClient) -> None:
    response = client.post(
        "/operator/login",
        data={"operator_key": OPERATOR_KEY},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "gtm_operator_session" in response.cookies


def test_admin_endpoint_without_authentication_is_rejected(
    operator_client: TestClient,
) -> None:
    response = operator_client.get("/api/v1/admin/overview")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "operator_auth_required"


def test_invalid_operator_key_is_rejected(operator_client: TestClient) -> None:
    response = operator_client.get(
        "/api/v1/admin/overview",
        headers={"X-Operator-Key": "x" * 32},
    )

    assert response.status_code == 401


def test_valid_operator_key_is_accepted(operator_client: TestClient) -> None:
    response = operator_client.get(
        "/api/v1/admin/overview", headers=_operator_headers()
    )

    assert response.status_code == 200
    assert response.json()["demo_mode"] is True


def test_n8n_callback_remains_independent_from_operator_auth(
    operator_client: TestClient,
) -> None:
    service, _, _ = _service_pair()
    action = service.approve(_create_email_action(service).id)
    signer = WebhookSigner(
        "test-webhook-secret-123",
        clock=lambda: 1_700_000_000,
    )
    body = json.dumps(
        {"action_id": str(action.id), "status": "completed", "metadata": {}},
        separators=(",", ":"),
    ).encode()
    timestamp = "1700000000"
    app.dependency_overrides[get_external_action_service] = lambda: service
    app.dependency_overrides[get_webhook_signer] = lambda: signer

    response = operator_client.post(
        "/api/v1/integrations/n8n/callback",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GTM-Timestamp": timestamp,
            "X-GTM-Signature": signer.sign(body, timestamp),
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_overview_calculates_core_metrics(operator_client: TestClient) -> None:
    data = operator_client.get(
        "/api/v1/admin/overview", headers=_operator_headers()
    ).json()

    assert data["leads"] == {"total": 12, "hot": 5, "warm": 4, "cold": 3}
    assert data["agents"]["runs"] == 18
    assert data["rag"]["retrievals"] == 11


def test_overview_success_rate_is_bounded_and_correct(
    operator_client: TestClient,
) -> None:
    data = operator_client.get(
        "/api/v1/admin/overview", headers=_operator_headers()
    ).json()

    assert data["agents"]["success_rate"] == pytest.approx(17 / 18)


def test_overview_average_latency(operator_client: TestClient) -> None:
    data = operator_client.get(
        "/api/v1/admin/overview", headers=_operator_headers()
    ).json()

    assert data["agents"]["average_latency_ms"] == pytest.approx(1834.4)
    assert data["tools"]["average_latency_ms"] == pytest.approx(122.5)


def test_pending_approval_queue_has_safe_draft_preview(
    operator_client: TestClient,
) -> None:
    response = operator_client.get(
        "/api/v1/admin/actions?status=pending", headers=_operator_headers()
    )
    action = response.json()["items"][0]

    assert response.status_code == 200
    assert action["status"] == "pending"
    assert set(action["payload_preview"]) == {
        "subject",
        "body",
        "reasoning_summary",
    }
    assert "to_email" not in action["payload_preview"]


def test_timeline_aggregates_existing_audit_sources(
    operator_client: TestClient,
) -> None:
    response = operator_client.get(
        f"/api/v1/admin/leads/{DEMO_LEAD_ID}/timeline",
        headers=_operator_headers(),
    )
    components = {item["component"] for item in response.json()["events"]}

    assert response.status_code == 200
    assert components == {"lead", "agent", "langgraph", "rag", "mcp", "external_action"}


def test_timeline_rejects_missing_lead() -> None:
    service = ObservabilityService(DemoObservabilityRepository(), demo_mode=True)

    with pytest.raises(LeadNotFoundError):
        service.lead_timeline(uuid4())


class InMemoryAIUsageRepository:
    def __init__(self) -> None:
        self.events: list[AIUsageEventRecord] = []

    def create(self, event: AIUsageEventCreate) -> AIUsageEventRecord:
        record = AIUsageEventRecord(
            id=uuid4(),
            created_at=datetime.now(UTC),
            **event.model_dump(),
        )
        self.events.append(record)
        return record


def test_ai_usage_event_is_valid_and_persisted() -> None:
    repository = InMemoryAIUsageRepository()
    service = AIUsageService(
        repository=repository,
        pricing=AIPricingCatalog(),
    )

    event = service.record(
        provider="anthropic",
        model="claude-test",
        operation="qualification",
        input_tokens=100,
        output_tokens=25,
        latency_ms=420,
    )

    assert event is not None
    assert event.total_tokens == 125
    assert repository.events == [event]


def test_ai_usage_context_links_event_to_lead_and_run() -> None:
    repository = InMemoryAIUsageRepository()
    service = AIUsageService(
        repository=repository,
        pricing=AIPricingCatalog(),
    )
    lead_id = uuid4()
    run_id = uuid4()

    with service.context(lead_id=lead_id, agent_run_id=run_id):
        event = service.record(
            provider="anthropic",
            model="claude-test",
            operation="research_context",
            input_tokens=10,
            output_tokens=5,
            latency_ms=15,
        )

    assert event is not None
    assert event.lead_id == lead_id
    assert event.agent_run_id == run_id


def test_ai_cost_uses_isolated_operator_price_catalog() -> None:
    repository = InMemoryAIUsageRepository()
    service = AIUsageService(
        repository=repository,
        pricing=AIPricingCatalog(
            {
                "anthropic:claude-test": ModelPrice(
                    input_per_million_usd=Decimal("3"),
                    output_per_million_usd=Decimal("15"),
                )
            }
        ),
    )

    event = service.record(
        provider="anthropic",
        model="claude-test",
        operation="qualification",
        input_tokens=1000,
        output_tokens=200,
        latency_ms=10,
    )

    assert event is not None
    assert event.estimated_cost_usd == Decimal("0.00600000")


def test_ai_usage_without_provider_tokens_does_not_invent_values() -> None:
    repository = InMemoryAIUsageRepository()
    service = AIUsageService(
        repository=repository,
        pricing=AIPricingCatalog(
            {"voyage:voyage-4": ModelPrice(total_per_million_usd=Decimal("1"))}
        ),
    )

    event = service.record(
        provider="voyage",
        model="voyage-4",
        operation="embedding_query",
        latency_ms=18,
    )

    assert event is not None
    assert event.input_tokens is None
    assert event.output_tokens is None
    assert event.total_tokens is None
    assert event.estimated_cost_usd is None


class RecordingUsageTracker:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record(self, **kwargs):
        self.calls.append(kwargs)
        return None


class FakeAnthropicMessages:
    def parse(self, **kwargs):
        return SimpleNamespace(
            parsed_output=QualificationResult(
                score=88,
                classification=LeadClassification.HOT,
                reason="Strong test fit.",
                next_action=NextAction.PERSONALIZED_OUTREACH,
            ),
            usage=SimpleNamespace(input_tokens=321, output_tokens=45),
            model="claude-test",
        )


def test_anthropic_adapter_records_provider_reported_usage(
    valid_payload: dict[str, object],
) -> None:
    tracker = RecordingUsageTracker()
    service = AnthropicLLMService(
        api_key="fake",
        model="claude-test",
        timeout_seconds=1,
        client=SimpleNamespace(messages=FakeAnthropicMessages()),
        usage_tracker=tracker,
    )

    service.qualify(LeadQualifyRequest.model_validate(valid_payload))

    assert tracker.calls[0]["operation"] == "qualification"
    assert tracker.calls[0]["input_tokens"] == 321
    assert tracker.calls[0]["output_tokens"] == 45
    assert tracker.calls[0]["total_tokens"] == 366


class FakeVoyageResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "data": [{"index": 0, "embedding": [0.0] * 1024}],
            "usage": {"total_tokens": 37},
        }


class FakeVoyageClient:
    def post(self, *args, **kwargs) -> FakeVoyageResponse:
        return FakeVoyageResponse()


def test_voyage_adapter_records_only_reported_total_tokens() -> None:
    tracker = RecordingUsageTracker()
    provider = VoyageEmbeddingProvider(
        api_key="fake",
        model="voyage-4",
        dimension=1024,
        timeout_seconds=1,
        client=FakeVoyageClient(),  # type: ignore[arg-type]
        usage_tracker=tracker,
    )

    provider.embed_text("safe demo query", input_type="query")

    assert tracker.calls[0]["operation"] == "embedding_query"
    assert tracker.calls[0]["total_tokens"] == 37
    assert "input_tokens" not in tracker.calls[0]
    assert "output_tokens" not in tracker.calls[0]


def test_run_inspector_exposes_ranked_rag_evidence(
    operator_client: TestClient,
) -> None:
    data = operator_client.get(
        f"/api/v1/admin/agent-runs/{DEMO_RUN_ID}",
        headers=_operator_headers(),
    ).json()

    assert data["rag_evidence"][0] == {
        "document_title": "Sales Playbook",
        "similarity": 0.89,
        "rank": 1,
        "timestamp": data["rag_evidence"][0]["timestamp"],
    }
    assert data["reasoning_summary"].startswith("Strong demo ICP fit")


def test_mcp_rejected_call_appears_in_metrics_and_inspector(
    operator_client: TestClient,
) -> None:
    overview = operator_client.get(
        "/api/v1/admin/overview", headers=_operator_headers()
    ).json()
    run = operator_client.get(
        f"/api/v1/admin/agent-runs/{DEMO_RUN_ID}", headers=_operator_headers()
    ).json()

    assert overview["tools"]["rejected"] == 1
    assert any(item["status"] == "rejected" for item in run["tool_calls"])


class UnsafeFailureDemoRepository(DemoObservabilityRepository):
    def recent_failures(self, limit: int) -> list[dict[str, object]]:
        return [
            {
                "component": "provider",
                "error": "Traceback: token=should-not-appear",
                "created_at": datetime.now(UTC),
            }
        ][:limit]


def test_failures_are_sanitized_without_stack_trace() -> None:
    data = ObservabilityService(
        UnsafeFailureDemoRepository(), demo_mode=True
    ).overview().model_dump(mode="json")
    serialized = json.dumps(data)

    assert data["recent_failures"][0]["error_code"] == "internal_error"
    assert "should-not-appear" not in serialized
    assert "Traceback" not in serialized


def test_operator_dashboard_loads_after_server_side_login(
    operator_client: TestClient,
) -> None:
    _login(operator_client)

    response = operator_client.get("/operator")

    assert response.status_code == 200
    assert "Operations Console" in response.text
    assert "Pending Approvals" in response.text
    assert OPERATOR_KEY not in response.text


def test_approval_via_dashboard_session_uses_existing_flow(
    operator_client: TestClient,
) -> None:
    service, _, dispatcher = _service_pair()
    action = _create_email_action(service, key="phase-six-dashboard-approve")
    app.dependency_overrides[get_external_action_service] = lambda: service
    _login(operator_client)

    response = operator_client.post(f"/api/v1/actions/{action.id}/approve", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "executing"
    assert len(dispatcher.calls) == 1


def test_rejection_via_dashboard_session_uses_existing_flow(
    operator_client: TestClient,
) -> None:
    service, repository, dispatcher = _service_pair()
    action = _create_email_action(service, key="phase-six-dashboard-reject")
    app.dependency_overrides[get_external_action_service] = lambda: service
    _login(operator_client)

    response = operator_client.post(f"/api/v1/actions/{action.id}/reject", json={})

    assert response.status_code == 200
    assert repository.get(action.id).status.value == "rejected"
    assert dispatcher.calls == []


def test_secrets_do_not_appear_in_html_api_or_structured_log(
    operator_client: TestClient,
) -> None:
    _login(operator_client)
    html = operator_client.get("/operator").text
    api_payload = operator_client.get(
        "/api/v1/admin/actions?status=pending", headers=_operator_headers()
    ).text
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "safe_event", (), None)
    record.authorization = "Bearer should-not-appear"
    record.email = "private@example.com"
    formatted = JsonFormatter().format(record)

    assert OPERATOR_KEY not in html
    assert "hidden@example.invalid" not in api_payload
    assert "should-not-appear" not in formatted
    assert "private@example.com" not in formatted


def test_health_remains_public(operator_client: TestClient) -> None:
    response = operator_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_checks_safe_database_dependency(
    operator_client: TestClient,
) -> None:
    response = operator_client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "demo"}


def test_existing_public_lead_routes_do_not_require_operator_key(
    context,
    valid_payload: dict[str, object],
) -> None:
    qualification = context.client.post("/api/v1/leads/qualify", json=valid_payload)
    orchestration = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert qualification.status_code == 200
    assert orchestration.status_code == 200


def test_phase_six_migration_is_incremental_and_service_role_only() -> None:
    sql = (Path(__file__).parents[1] / "sql" / "006_observability.sql").read_text(
        encoding="utf-8"
    ).lower()

    assert "create table if not exists public.ai_usage_events" in sql
    assert "with (security_invoker = true)" in sql
    assert "alter table public.ai_usage_events enable row level security" in sql
    assert "grant select, insert on table public.ai_usage_events to service_role" in sql
    assert "revoke all on table public.ai_usage_events" in sql
    assert "drop table" not in sql
    assert "truncate" not in sql
    assert "delete from" not in sql


def test_delivered_javascript_contains_no_operator_key() -> None:
    root = Path(__file__).parents[1] / "app" / "static"
    javascript = (root / "operator-console.js").read_text(encoding="utf-8")

    assert "OPERATOR_API_KEY" not in javascript
    assert OPERATOR_KEY not in javascript
    assert "X-Operator-Key" not in javascript
