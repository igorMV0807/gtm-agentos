from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.agents.state import AgentStateTransition
from app.api.dependencies import (
    get_agent_orchestration_service,
    get_qualification_service,
)
from app.core.exceptions import GTMAgentOSError
from app.main import app
from app.models.lead import AgentRunRecord, LeadRecord
from app.models.orchestration import AgentStateTransitionRecord
from app.schemas.lead import LeadQualifyRequest
from app.schemas.qualification import (
    LeadClassification,
    NextAction,
    QualificationResult,
)
from app.services.agent_orchestration_service import AgentOrchestrationService
from app.services.qualification_service import QualificationService


class InMemoryLeadRepository:
    def __init__(self) -> None:
        self.leads: dict[UUID, LeadRecord] = {}

    def find_existing(self, lead: LeadQualifyRequest) -> LeadRecord | None:
        for stored in self.leads.values():
            if lead.external_id and stored.external_id == lead.external_id:
                return stored
        for stored in self.leads.values():
            if (
                str(stored.email).lower() == str(lead.email).lower()
                and stored.company == lead.company
            ):
                return stored
        return None

    def create(self, lead: LeadQualifyRequest) -> LeadRecord:
        record = LeadRecord(id=uuid4(), **lead.model_dump(mode="json"))
        self.leads[record.id] = record
        return record

    def update(self, lead_id: UUID, lead: LeadQualifyRequest) -> LeadRecord:
        current = self.leads[lead_id].model_dump(mode="json")
        current.update(lead.model_dump(mode="json"))
        current["id"] = str(lead_id)
        record = LeadRecord.model_validate(current)
        self.leads[lead_id] = record
        return record

    def save_qualification(
        self, lead_id: UUID, qualification: QualificationResult
    ) -> LeadRecord:
        current = self.leads[lead_id].model_dump(mode="json")
        current.update(
            {
                "score": qualification.score,
                "classification": qualification.classification.value,
                "qualification_reason": qualification.reason,
                "next_action": qualification.next_action.value,
            }
        )
        record = LeadRecord.model_validate(current)
        self.leads[lead_id] = record
        return record


class InMemoryAgentRunRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, AgentRunRecord] = {}

    def create_started(
        self,
        *,
        lead_id: UUID,
        agent_type: str,
        model: str,
        input_data: dict[str, object],
    ) -> AgentRunRecord:
        run = AgentRunRecord(
            id=uuid4(),
            lead_id=lead_id,
            agent_type=agent_type,
            model=model,
            status="started",
            input=input_data,
        )
        self.runs[run.id] = run
        return run

    def mark_completed(
        self,
        run_id: UUID,
        *,
        output: QualificationResult,
        latency_ms: int,
    ) -> AgentRunRecord:
        current = self.runs[run_id].model_dump(mode="json")
        current.update(
            {
                "status": "completed",
                "output": output.model_dump(mode="json"),
                "error": None,
                "latency_ms": latency_ms,
            }
        )
        run = AgentRunRecord.model_validate(current)
        self.runs[run_id] = run
        return run

    def mark_failed(
        self,
        run_id: UUID,
        *,
        error: str,
        latency_ms: int,
    ) -> AgentRunRecord:
        current = self.runs[run_id].model_dump(mode="json")
        current.update(
            {
                "status": "failed",
                "error": error,
                "latency_ms": latency_ms,
            }
        )
        run = AgentRunRecord.model_validate(current)
        self.runs[run_id] = run
        return run

    def mark_completed_payload(
        self,
        run_id: UUID,
        *,
        output: dict[str, object],
        latency_ms: int,
    ) -> AgentRunRecord:
        current = self.runs[run_id].model_dump(mode="json")
        current.update(
            {
                "status": "completed",
                "output": output,
                "error": None,
                "latency_ms": latency_ms,
            }
        )
        run = AgentRunRecord.model_validate(current)
        self.runs[run_id] = run
        return run


class InMemoryAgentStateTransitionRepository:
    def __init__(self) -> None:
        self.transitions: list[AgentStateTransitionRecord] = []

    def create_many(
        self,
        *,
        agent_run_id: UUID,
        lead_id: UUID,
        transitions: list[AgentStateTransition],
    ) -> list[AgentStateTransitionRecord]:
        records = [
            AgentStateTransitionRecord(
                id=uuid4(),
                agent_run_id=agent_run_id,
                lead_id=lead_id,
                from_state=transition.from_state,
                to_state=transition.to_state,
                route=transition.route,
                payload=transition.payload,
            )
            for transition in transitions
        ]
        self.transitions.extend(records)
        return records


class FakeLLMService:
    provider_name = "anthropic"
    model = "claude-test-model"

    def __init__(self) -> None:
        self.result = QualificationResult(
            score=87,
            classification=LeadClassification.HOT,
            reason="Strong fit and senior buying role.",
            next_action=NextAction.PERSONALIZED_OUTREACH,
        )
        self.error: GTMAgentOSError | None = None
        self.calls = 0

    def qualify(self, lead: LeadQualifyRequest) -> QualificationResult:
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


@dataclass
class ScenarioContext:
    client: TestClient
    leads: InMemoryLeadRepository
    runs: InMemoryAgentRunRepository
    transitions: InMemoryAgentStateTransitionRepository
    llm: FakeLLMService


@pytest.fixture
def context() -> Iterator[ScenarioContext]:
    lead_repository = InMemoryLeadRepository()
    agent_run_repository = InMemoryAgentRunRepository()
    transition_repository = InMemoryAgentStateTransitionRepository()
    llm_service = FakeLLMService()
    qualification_service = QualificationService(
        lead_repository=lead_repository,
        agent_run_repository=agent_run_repository,
        llm_service=llm_service,
    )
    orchestration_service = AgentOrchestrationService(
        lead_repository=lead_repository,
        agent_run_repository=agent_run_repository,
        transition_repository=transition_repository,
        qualification_service=qualification_service,
    )

    app.dependency_overrides[get_qualification_service] = lambda: qualification_service
    app.dependency_overrides[get_agent_orchestration_service] = (
        lambda: orchestration_service
    )
    with TestClient(app) as client:
        yield ScenarioContext(
            client,
            lead_repository,
            agent_run_repository,
            transition_repository,
            llm_service,
        )
    app.dependency_overrides.clear()


@pytest.fixture
def valid_payload() -> dict[str, object]:
    return {
        "external_id": "lead_001",
        "name": "John Smith",
        "email": "john@acme.com",
        "company": "Acme",
        "job_title": "Head of Sales",
        "company_size": 80,
        "industry": "SaaS",
        "country": "United States",
        "website": "https://acme.com",
    }
