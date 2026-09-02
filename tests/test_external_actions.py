import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import (
    get_external_action_service,
    get_webhook_signer,
    require_operator,
)
from app.core.config import Settings
from app.core.exceptions import (
    ApplicationConfigurationError,
    ExternalActionConflictError,
    ExternalActionInvalidError,
    ExternalIntegrationError,
    WebhookReplayError,
    WebhookSignatureInvalidError,
)
from app.integrations.crm import HubSpotCRMProvider
from app.integrations.email import ResendEmailProvider, require_approved_email_action
from app.integrations.n8n import N8nActionService, N8nDispatchResult, WebhookSigner
from app.main import app
from app.models.external_actions import (
    ExternalActionEventRecord,
    ExternalActionRecord,
)
from app.schemas.external_actions import (
    CreateOrUpdateCRMLeadPayload,
    EmailDraft,
    ExternalActionCreate,
    ExternalActionEventType,
    ExternalActionStatus,
    ExternalActionType,
    MarkLeadStatusPayload,
    N8nCallbackPayload,
)
from app.schemas.lead import LeadQualifyRequest
from app.schemas.qualification import (
    LeadClassification,
    NextAction,
    QualificationResult,
)
from app.services.agent_orchestration_service import AgentOrchestrationService
from app.services.external_action_service import (
    ExternalActionService,
    sanitize_external_data,
)
from app.services.qualification_service import QualificationService
from tests.conftest import (
    FakeLLMService,
    FakeRetrievalService,
    InMemoryAgentRunRepository,
    InMemoryAgentStateTransitionRepository,
    InMemoryLeadRepository,
    InMemoryRagRetrievalRepository,
    ScenarioContext,
)


class InMemoryExternalActionRepository:
    def __init__(self) -> None:
        self.actions: dict[UUID, ExternalActionRecord] = {}
        self.by_key: dict[str, UUID] = {}
        self.events: list[ExternalActionEventRecord] = []

    def create_or_get(
        self, action: ExternalActionCreate
    ) -> tuple[ExternalActionRecord, bool]:
        existing_id = self.by_key.get(action.idempotency_key)
        if existing_id is not None:
            return self.actions[existing_id], False
        now = datetime.now(UTC)
        record = ExternalActionRecord(
            id=uuid4(),
            lead_id=action.lead_id,
            agent_run_id=action.agent_run_id,
            action_type=action.action_type,
            payload=action.payload,
            status=ExternalActionStatus.PENDING,
            requires_approval=action.requires_approval,
            idempotency_key=action.idempotency_key,
            created_at=now,
            updated_at=now,
        )
        self.actions[record.id] = record
        self.by_key[record.idempotency_key] = record.id
        return record, True

    def get(self, action_id: UUID) -> ExternalActionRecord | None:
        return self.actions.get(action_id)

    def mark_approved(self, action_id: UUID) -> ExternalActionRecord | None:
        return self._transition(
            action_id,
            {ExternalActionStatus.PENDING},
            status=ExternalActionStatus.APPROVED,
            approved_at=datetime.now(UTC),
            executed_at=None,
            error=None,
        )

    def mark_retry_approved(self, action_id: UUID) -> ExternalActionRecord | None:
        return self._transition(
            action_id,
            {ExternalActionStatus.FAILED},
            status=ExternalActionStatus.APPROVED,
            executed_at=None,
            error=None,
            result=None,
        )

    def mark_rejected(self, action_id: UUID) -> ExternalActionRecord | None:
        return self._transition(
            action_id,
            {ExternalActionStatus.PENDING},
            status=ExternalActionStatus.REJECTED,
        )

    def mark_executing(self, action_id: UUID) -> ExternalActionRecord | None:
        return self._transition(
            action_id,
            {ExternalActionStatus.APPROVED},
            status=ExternalActionStatus.EXECUTING,
            executed_at=datetime.now(UTC),
            error=None,
        )

    def set_external_reference(
        self, action_id: UUID, external_reference: str
    ) -> ExternalActionRecord | None:
        return self._transition(
            action_id,
            {ExternalActionStatus.EXECUTING},
            external_reference=external_reference[:500],
        )

    def mark_completed(
        self,
        action_id: UUID,
        *,
        external_reference: str | None,
        result: dict[str, Any],
    ) -> ExternalActionRecord | None:
        updates: dict[str, Any] = {
            "status": ExternalActionStatus.COMPLETED,
            "result": result,
            "error": None,
        }
        if external_reference:
            updates["external_reference"] = external_reference
        return self._transition(
            action_id,
            {ExternalActionStatus.EXECUTING},
            **updates,
        )

    def mark_failed(
        self,
        action_id: UUID,
        *,
        error: str,
        result: dict[str, Any] | None = None,
    ) -> ExternalActionRecord | None:
        return self._transition(
            action_id,
            {ExternalActionStatus.APPROVED, ExternalActionStatus.EXECUTING},
            status=ExternalActionStatus.FAILED,
            executed_at=datetime.now(UTC),
            error=error,
            result=result,
        )

    def create_event(
        self,
        *,
        action_id: UUID,
        event_type: ExternalActionEventType,
        metadata: dict[str, Any] | None = None,
    ) -> ExternalActionEventRecord:
        event = ExternalActionEventRecord(
            id=uuid4(),
            action_id=action_id,
            event_type=event_type,
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        self.events.append(event)
        return event

    def _transition(
        self,
        action_id: UUID,
        allowed: set[ExternalActionStatus],
        **updates: Any,
    ) -> ExternalActionRecord | None:
        current = self.actions[action_id]
        if current.status not in allowed:
            return None
        updated = current.model_copy(
            update={**updates, "updated_at": datetime.now(UTC)}
        )
        self.actions[action_id] = updated
        return updated


class FakeN8nDispatcher:
    def __init__(self) -> None:
        self.calls: list[ExternalActionRecord] = []
        self.error: Exception | None = None

    def execute_action(self, action: ExternalActionRecord) -> N8nDispatchResult:
        self.calls.append(action)
        if self.error:
            raise self.error
        return N8nDispatchResult(
            accepted=True,
            external_reference="n8n-execution-001",
        )


class FakeHTTPResponse:
    def __init__(self, status_code: int, data: object) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> object:
        return self._data


class RecordingHTTPClient:
    def __init__(self, responses: list[FakeHTTPResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> FakeHTTPResponse:
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def patch(self, url: str, **kwargs: object) -> FakeHTTPResponse:
        self.calls.append(("PATCH", url, kwargs))
        return self.responses.pop(0)


def _email_payload(lead_id: UUID) -> dict[str, object]:
    return {
        "lead_id": str(lead_id),
        "to_email": "buyer@example.com",
        "subject": "A focused pilot",
        "body": "A grounded outreach message.",
        "reasoning_summary": "Based on approved ICP guidance.",
    }


def _create_email_action(
    service: ExternalActionService,
    *,
    lead_id: UUID | None = None,
    key: str = "lead:send_approved_email:initial",
) -> ExternalActionRecord:
    target_lead_id = lead_id or uuid4()
    return service.request_action(
        lead_id=target_lead_id,
        agent_run_id=uuid4(),
        action_type=ExternalActionType.SEND_APPROVED_EMAIL,
        payload=_email_payload(target_lead_id),
        idempotency_key=key,
    )


def _service_pair() -> tuple[
    ExternalActionService,
    InMemoryExternalActionRepository,
    FakeN8nDispatcher,
]:
    repository = InMemoryExternalActionRepository()
    dispatcher = FakeN8nDispatcher()
    service = ExternalActionService(
        repository=repository,
        n8n_dispatcher=dispatcher,
    )
    return service, repository, dispatcher


def _phase_five_orchestrator(
    classification: LeadClassification,
) -> tuple[
    AgentOrchestrationService,
    InMemoryExternalActionRepository,
    FakeLLMService,
    InMemoryAgentStateTransitionRepository,
]:
    leads = InMemoryLeadRepository()
    runs = InMemoryAgentRunRepository()
    transitions = InMemoryAgentStateTransitionRepository()
    llm = FakeLLMService()
    next_actions = {
        LeadClassification.HOT: NextAction.PERSONALIZED_OUTREACH,
        LeadClassification.WARM: NextAction.NURTURE,
        LeadClassification.COLD: NextAction.DISCARD,
    }
    scores = {
        LeadClassification.HOT: 91,
        LeadClassification.WARM: 61,
        LeadClassification.COLD: 20,
    }
    llm.result = QualificationResult(
        score=scores[classification],
        classification=classification,
        reason=f"Controlled {classification.value} scenario.",
        next_action=next_actions[classification],
    )
    qualification = QualificationService(
        lead_repository=leads,
        agent_run_repository=runs,
        llm_service=llm,
    )
    actions = InMemoryExternalActionRepository()
    action_service = ExternalActionService(repository=actions)
    orchestrator = AgentOrchestrationService(
        lead_repository=leads,
        agent_run_repository=runs,
        transition_repository=transitions,
        qualification_service=qualification,
        retrieval_service=FakeRetrievalService(),
        rag_retrieval_repository=InMemoryRagRetrievalRepository(),
        llm_service=llm,
        external_action_service=action_service,
    )
    return orchestrator, actions, llm, transitions


def test_creates_allowlisted_external_action_and_audit_event() -> None:
    service, repository, _ = _service_pair()

    action = _create_email_action(service)

    assert len(repository.actions) == 1
    assert action.action_type == ExternalActionType.SEND_APPROVED_EMAIL
    assert [event.event_type for event in repository.events] == [
        ExternalActionEventType.ACTION_REQUESTED,
        ExternalActionEventType.EMAIL_DRAFT_CREATED,
    ]


def test_email_action_requires_human_approval() -> None:
    service, _, dispatcher = _service_pair()

    action = _create_email_action(service)

    assert action.requires_approval is True
    assert action.status == ExternalActionStatus.PENDING
    assert dispatcher.calls == []


def test_rejected_action_never_executes() -> None:
    service, repository, dispatcher = _service_pair()
    action = _create_email_action(service)

    rejected = service.reject(action.id)

    assert rejected.status == ExternalActionStatus.REJECTED
    assert dispatcher.calls == []
    assert repository.events[-1].event_type == ExternalActionEventType.ACTION_REJECTED
    with pytest.raises(ExternalActionConflictError):
        service.approve(action.id)


def test_approved_action_dispatches_exactly_once() -> None:
    service, repository, dispatcher = _service_pair()
    action = _create_email_action(service)

    executing = service.approve(action.id)
    duplicate = service.approve(action.id)

    assert executing.status == ExternalActionStatus.EXECUTING
    assert duplicate.status == ExternalActionStatus.EXECUTING
    assert executing.approved_at is not None
    assert len(dispatcher.calls) == 1
    assert repository.get(action.id).external_reference == "n8n-execution-001"


def test_idempotency_key_prevents_duplicate_actions_and_audit() -> None:
    service, repository, _ = _service_pair()
    lead_id = uuid4()

    first = _create_email_action(service, lead_id=lead_id, key="stable-key")
    second = _create_email_action(service, lead_id=lead_id, key="stable-key")

    assert first.id == second.id
    assert len(repository.actions) == 1
    assert len(repository.events) == 2


def test_idempotency_key_cannot_be_reused_for_another_lead() -> None:
    service, _, _ = _service_pair()
    _create_email_action(service, lead_id=uuid4(), key="shared-key")

    with pytest.raises(ExternalActionConflictError):
        _create_email_action(service, lead_id=uuid4(), key="shared-key")


def test_valid_callback_completes_existing_action() -> None:
    service, repository, _ = _service_pair()
    action = service.approve(_create_email_action(service).id)

    completed = service.process_callback(
        N8nCallbackPayload(
            action_id=action.id,
            status="completed",
            external_reference="provider-message-001",
            metadata={"provider": "fake"},
        )
    )

    assert completed.status == ExternalActionStatus.COMPLETED
    assert completed.external_reference == "provider-message-001"
    assert [event.event_type for event in repository.events][-2:] == [
        ExternalActionEventType.CALLBACK_RECEIVED,
        ExternalActionEventType.EXECUTION_COMPLETED,
    ]


def test_callback_cannot_complete_pending_action() -> None:
    service, _, _ = _service_pair()
    action = _create_email_action(service)

    with pytest.raises(ExternalActionConflictError):
        service.process_callback(
            N8nCallbackPayload(action_id=action.id, status="completed")
        )


def test_valid_hmac_signature_is_accepted() -> None:
    signer = WebhookSigner(
        "test-webhook-secret-123",
        clock=lambda: 1_700_000_000,
    )
    body = b'{"action_id":"safe"}'
    timestamp = "1700000000"

    signer.verify(body, timestamp, signer.sign(body, timestamp))


def test_invalid_hmac_signature_is_rejected() -> None:
    signer = WebhookSigner(
        "test-webhook-secret-123",
        clock=lambda: 1_700_000_000,
    )

    with pytest.raises(WebhookSignatureInvalidError):
        signer.verify(b"{}", "1700000000", "sha256=bad")


def test_stale_hmac_timestamp_is_rejected() -> None:
    signer = WebhookSigner(
        "test-webhook-secret-123",
        max_age_seconds=60,
        clock=lambda: 1_700_000_100,
    )
    body = b"{}"

    with pytest.raises(WebhookReplayError):
        signer.verify(body, "1700000000", signer.sign(body, "1700000000"))


def test_non_ascii_hmac_timestamp_is_safely_rejected() -> None:
    signer = WebhookSigner(
        "test-webhook-secret-123",
        clock=lambda: 1_700_000_000,
    )

    with pytest.raises(WebhookSignatureInvalidError):
        signer.verify(b"{}", "１７００００００００", "sha256=bad")


@pytest.mark.parametrize(
    "url",
    [
        "http://untrusted.example/webhook",
        "https://user:password@n8n.example/webhook",
        "https://n8n.example/webhook?token=unsafe",
    ],
)
def test_n8n_configuration_rejects_unsafe_urls(url: str) -> None:
    settings = Settings(
        _env_file=None,
        n8n_webhook_url=url,
        n8n_webhook_secret="test-webhook-secret-123",
    )

    with pytest.raises(ApplicationConfigurationError):
        settings.require_n8n()


def test_email_draft_is_strict_structured_output() -> None:
    draft = EmailDraft(
        subject="Pilot proposal",
        body="A concise, grounded email.",
        reasoning_summary="Uses approved internal guidance.",
    )

    assert set(draft.model_dump()) == {"subject", "body", "reasoning_summary"}
    with pytest.raises(ValidationError):
        EmailDraft.model_validate({**draft.model_dump(), "private_reasoning": "no"})


def test_hot_flow_creates_draft_and_pending_email_action(
    valid_payload: dict[str, object],
) -> None:
    orchestrator, repository, llm, transitions = _phase_five_orchestrator(
        LeadClassification.HOT
    )

    response = orchestrator.orchestrate(LeadQualifyRequest.model_validate(valid_payload))

    action = next(iter(repository.actions.values()))
    assert response.route.value == "research"
    assert response.external_action_id == action.id
    assert action.action_type == ExternalActionType.SEND_APPROVED_EMAIL
    assert action.status == ExternalActionStatus.PENDING
    assert llm.draft_calls == 1
    assert "draft_outreach_email" in {item.to_state.value for item in transitions.transitions}
    assert "request_external_action" in {
        item.to_state.value for item in transitions.transitions
    }


def test_warm_flow_creates_task_but_never_email(
    valid_payload: dict[str, object],
) -> None:
    orchestrator, repository, llm, _ = _phase_five_orchestrator(
        LeadClassification.WARM
    )

    response = orchestrator.orchestrate(LeadQualifyRequest.model_validate(valid_payload))

    action = next(iter(repository.actions.values()))
    assert response.route.value == "nurture"
    assert action.action_type == ExternalActionType.CREATE_FOLLOW_UP_TASK
    assert llm.draft_calls == 0


def test_cold_flow_creates_no_external_action(
    valid_payload: dict[str, object],
) -> None:
    orchestrator, repository, llm, _ = _phase_five_orchestrator(
        LeadClassification.COLD
    )

    response = orchestrator.orchestrate(LeadQualifyRequest.model_validate(valid_payload))

    assert response.route.value == "stop"
    assert response.external_action_id is None
    assert repository.actions == {}
    assert llm.draft_calls == 0


def test_email_provider_guard_rejects_unapproved_action() -> None:
    service, _, _ = _service_pair()
    action = _create_email_action(service)

    with pytest.raises(ExternalActionConflictError):
        require_approved_email_action(action)


def test_resend_adapter_sends_only_approved_email_to_validation_recipient() -> None:
    service, _, _ = _service_pair()
    action = service.approve(_create_email_action(service).id)
    client = RecordingHTTPClient([FakeHTTPResponse(200, {"id": "email-42"})])
    provider = ResendEmailProvider(
        api_key="fake-test-key",
        test_recipient="buyer@example.com",
        client=client,
    )

    result = provider.send_email(action)

    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url == "https://api.resend.com/emails"
    assert kwargs["headers"]["Idempotency-Key"] == action.idempotency_key  # type: ignore[index]
    assert kwargs["json"]["to"] == ["buyer@example.com"]  # type: ignore[index]
    assert result.external_reference == "email-42"


def test_resend_adapter_rejects_other_recipient_before_network() -> None:
    service, _, _ = _service_pair()
    action = service.approve(_create_email_action(service).id)
    client = RecordingHTTPClient([])
    provider = ResendEmailProvider(
        api_key="fake-test-key",
        test_recipient="allowed@example.com",
        client=client,
    )

    with pytest.raises(ExternalActionInvalidError):
        provider.send_email(action)

    assert client.calls == []


def test_hubspot_upsert_uses_fixed_url_and_typed_payload() -> None:
    client = RecordingHTTPClient([FakeHTTPResponse(200, {"results": [{"id": "42"}]})])
    provider = HubSpotCRMProvider(access_token="fake-test-token", client=client)
    lead_id = uuid4()

    result = provider.create_or_update_lead(
        CreateOrUpdateCRMLeadPayload(
            lead_id=lead_id,
            external_id="ext-42",
            name="Ada Buyer",
            email="ada@example.com",
            company="Example Co",
            job_title="Head of Sales",
            classification="HOT",
        )
    )

    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url == "https://api.hubapi.com/crm/objects/2026-03/contacts/batch/upsert"
    assert kwargs["json"]["inputs"][0]["id"] == "ada@example.com"  # type: ignore[index]
    assert "hs_lead_status" not in kwargs["json"]["inputs"][0]["properties"]  # type: ignore[index]
    assert result.external_reference == "42"


def test_hubspot_status_adapter_searches_then_updates_contact() -> None:
    client = RecordingHTTPClient(
        [
            FakeHTTPResponse(200, {"results": [{"id": "contact-7"}]}),
            FakeHTTPResponse(200, {"id": "contact-7"}),
        ]
    )
    provider = HubSpotCRMProvider(access_token="fake-test-token", client=client)

    result = provider.update_lead_status(
        MarkLeadStatusPayload(lead_id=uuid4(), status="contacted")
    )

    assert [call[0] for call in client.calls] == ["POST", "PATCH"]
    assert client.calls[1][1].endswith("/contacts/contact-7")
    assert client.calls[1][2]["json"] == {
        "properties": {"hs_lead_status": "contacted"}
    }
    assert result.external_reference == "contact-7"


def test_n8n_adapter_uses_fixed_url_idempotency_and_hmac() -> None:
    client = RecordingHTTPClient(
        [FakeHTTPResponse(202, {"external_reference": "workflow-99"})]
    )
    signer = WebhookSigner(
        "test-webhook-secret-123",
        clock=lambda: 1_700_000_000,
    )
    service = N8nActionService(
        webhook_url="https://n8n.example.test/webhook/gtm",
        signer=signer,
        client=client,
        clock=lambda: 1_700_000_000,
    )
    action_service, _, _ = _service_pair()
    action = _create_email_action(action_service)

    result = service.execute_action(action)

    method, url, kwargs = client.calls[0]
    headers = kwargs["headers"]
    body = kwargs["content"]
    assert method == "POST"
    assert url == "https://n8n.example.test/webhook/gtm"
    assert headers["Idempotency-Key"] == action.idempotency_key  # type: ignore[index]
    signer.verify(  # type: ignore[arg-type]
        body,
        headers["X-GTM-Timestamp"],
        headers["X-GTM-Signature"],
    )
    assert result.external_reference == "workflow-99"


def test_external_dispatch_failure_is_recorded() -> None:
    service, repository, dispatcher = _service_pair()
    dispatcher.error = ExternalIntegrationError("simulated failure")
    action = _create_email_action(service)

    with pytest.raises(ExternalIntegrationError):
        service.approve(action.id)

    failed = repository.get(action.id)
    assert failed.status == ExternalActionStatus.FAILED
    assert failed.error == "external_integration_failed"
    assert repository.events[-1].event_type == ExternalActionEventType.EXECUTION_FAILED


def test_failed_action_retry_reuses_same_action_and_idempotency_key() -> None:
    service, repository, dispatcher = _service_pair()
    dispatcher.error = ExternalIntegrationError("simulated failure")
    action = _create_email_action(service, key="retry-safe-key")
    with pytest.raises(ExternalIntegrationError):
        service.approve(action.id)
    dispatcher.error = None

    retried = service.approve(action.id)

    assert retried.id == action.id
    assert retried.idempotency_key == "retry-safe-key"
    assert retried.status == ExternalActionStatus.EXECUTING
    assert len(repository.actions) == 1
    assert len(dispatcher.calls) == 2


def test_callback_metadata_secrets_are_sanitized() -> None:
    service, repository, _ = _service_pair()
    action = service.approve(_create_email_action(service).id)

    completed = service.process_callback(
        N8nCallbackPayload(
            action_id=action.id,
            status="completed",
            metadata={
                "access_token": "should-never-be-stored",
                "authorization": "Bearer should-never-be-stored",
                "provider": "fake",
                "status": "failed",
            },
        )
    )

    assert completed.result["access_token"] == "[REDACTED]"
    assert completed.result["authorization"] == "[REDACTED]"
    callback_event = next(
        event
        for event in repository.events
        if event.event_type == ExternalActionEventType.CALLBACK_RECEIVED
    )
    assert callback_event.metadata["status"] == "completed"
    assert "should-never-be-stored" not in json.dumps(callback_event.metadata)


def test_nested_secret_sanitizer_is_bounded() -> None:
    sanitized = sanitize_external_data(
        {
            "nested": {"api-key": "secret-value", "safe": "ok"},
            "long": "x" * 6000,
        }
    )

    assert sanitized["nested"]["api-key"] == "[REDACTED]"  # type: ignore[index]
    assert len(sanitized["long"]) == 5000  # type: ignore[arg-type]


def test_signed_callback_endpoint_accepts_only_valid_existing_action() -> None:
    service, _, _ = _service_pair()
    action = service.approve(_create_email_action(service).id)
    signer = WebhookSigner(
        "test-webhook-secret-123",
        clock=lambda: 1_700_000_000,
    )
    body = json.dumps(
        {
            "action_id": str(action.id),
            "status": "completed",
            "external_reference": "provider-1",
            "metadata": {},
        },
        separators=(",", ":"),
    ).encode()
    timestamp = "1700000000"
    app.dependency_overrides[get_external_action_service] = lambda: service
    app.dependency_overrides[get_webhook_signer] = lambda: signer
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/integrations/n8n/callback",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GTM-Timestamp": timestamp,
                    "X-GTM-Signature": signer.sign(body, timestamp),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_callback_endpoint_rejects_untrusted_action_type_before_handler() -> None:
    service, _, _ = _service_pair()
    action = service.approve(_create_email_action(service).id)
    signer = WebhookSigner(
        "test-webhook-secret-123",
        clock=lambda: 1_700_000_000,
    )
    body = json.dumps(
        {
            "action_id": str(action.id),
            "action_type": "send_approved_email",
            "status": "completed",
            "metadata": {},
        },
        separators=(",", ":"),
    ).encode()
    timestamp = "1700000000"
    app.dependency_overrides[get_external_action_service] = lambda: service
    app.dependency_overrides[get_webhook_signer] = lambda: signer
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/integrations/n8n/callback",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GTM-Timestamp": timestamp,
                    "X-GTM-Signature": signer.sign(body, timestamp),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_external_action"
    assert service._repository.get(action.id).status == ExternalActionStatus.EXECUTING


def test_approval_and_rejection_endpoints_only_target_existing_actions() -> None:
    service, _, dispatcher = _service_pair()
    approved_action = _create_email_action(service, key="approve-endpoint")
    rejected_action = _create_email_action(service, key="reject-endpoint")
    app.dependency_overrides[get_external_action_service] = lambda: service
    app.dependency_overrides[require_operator] = lambda: None
    try:
        with TestClient(app) as client:
            approved = client.post(f"/api/v1/actions/{approved_action.id}/approve")
            rejected = client.post(f"/api/v1/actions/{rejected_action.id}/reject")
            missing = client.post(f"/api/v1/actions/{uuid4()}/approve")
    finally:
        app.dependency_overrides.clear()

    assert approved.status_code == 200
    assert approved.json()["status"] == "executing"
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert missing.status_code == 404
    assert len(dispatcher.calls) == 1


def test_unknown_action_type_is_not_executable() -> None:
    service, repository, _ = _service_pair()

    with pytest.raises(ExternalActionInvalidError):
        service.request_action(
            lead_id=uuid4(),
            agent_run_id=None,
            action_type="generic_http_request",  # type: ignore[arg-type]
            payload={"url": "https://untrusted.example"},
            idempotency_key="unknown-action",
        )

    assert repository.actions == {}


def test_phase_five_migration_is_incremental_and_service_role_only() -> None:
    sql = (Path(__file__).parents[1] / "sql" / "005_external_actions.sql").read_text(
        encoding="utf-8"
    )
    lowered = sql.lower()

    assert "create table if not exists public.external_actions" in lowered
    assert "create table if not exists public.external_action_events" in lowered
    assert "enable row level security" in lowered
    assert "grant select, insert, update on table public.external_actions to service_role" in lowered
    assert "grant select, insert on table public.external_action_events to service_role" in lowered
    assert "grant delete" not in lowered
    assert "drop table" not in lowered
    assert "truncate" not in lowered
    assert " on delete restrict" in lowered
    assert "external_actions_required_approval" in lowered


def test_demo_workflow_is_valid_json_without_credentials() -> None:
    path = Path(__file__).parents[1] / "n8n" / "gtm-agentos-actions.workflow.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(data).lower()
    nodes = {node["name"]: node for node in data["nodes"]}
    switch_outputs = data["connections"]["Switch Allowlisted Action"]["main"]

    assert data["id"] == "5a7e5c0de36b4f21"
    assert data["name"] == "GTM AgentOS - Controlled External Actions"
    for action_type in ExternalActionType:
        assert action_type.value in serialized
    assert nodes["Webhook Trigger"]["webhookId"]
    assert nodes["Webhook Trigger"]["parameters"]["responseMode"] == "responseNode"
    assert "require('crypto')" in nodes["Validate Signature"]["parameters"]["jsCode"]
    assert "require('url')" in nodes["Build Signed Callback"]["parameters"]["jsCode"]
    assert switch_outputs[5][0]["node"] == "Reject Disallowed Action"
    assert nodes["Reject Disallowed Action"]["parameters"]["options"]["responseCode"] == 400
    assert "action_type_not_allowed" in serialized
    assert data["connections"]["Callback GTM AgentOS"]["main"][0][0]["node"] == "Return Success"
    assert "n8n_webhook_secret" in serialized
    assert "gtm_agentos_callback_url" in serialized
    assert all("credentials" not in node for node in data["nodes"])
    assert "$env.hubspot_access_token" in serialized
    assert "$env.resend_api_key" in serialized
    assert "$env.email_test_recipient" in serialized
    assert "pat-" not in serialized


def test_existing_phase_one_and_phase_two_endpoints_remain_compatible(
    context: ScenarioContext,
    valid_payload: dict[str, object],
) -> None:
    qualification = context.client.post("/api/v1/leads/qualify", json=valid_payload)
    orchestration = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert qualification.status_code == 200
    assert orchestration.status_code == 200
    assert "external_action_id" not in orchestration.json()
    assert "external_action_status" not in orchestration.json()
