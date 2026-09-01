import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from app.agents.state import AgentStep
from app.core.exceptions import (
    LeadNotFoundError,
    ToolInputInvalidError,
    ToolNotFoundError,
)
from app.mcp.execution import ToolExecutionService
from app.mcp.registry import build_tool_registry
from app.mcp.schemas import ToolName
from app.mcp.server import create_mcp_server
from app.models.lead import AgentRunRecord, LeadRecord
from app.models.mcp import PipelineCounts, ToolCallAuditCreate, ToolCallRecord
from app.models.orchestration import AgentStateTransitionRecord
from app.schemas.knowledge import RetrievedChunk
from app.schemas.orchestration import AgentRoute
from app.schemas.qualification import (
    LeadClassification,
    NextAction,
)
from tests.conftest import ScenarioContext


class FakeMCPDataRepository:
    def __init__(self) -> None:
        self.leads: list[LeadRecord] = [_lead()]
        lead_id = self.leads[0].id
        self.runs = [
            AgentRunRecord(
                id=uuid4(),
                lead_id=lead_id,
                agent_type="lead_qualification",
                model="claude-test",
                status="completed",
                input={"email": "private@example.com"},
                output={
                    "score": 88,
                    "classification": "HOT",
                    "next_action": "personalized_outreach",
                },
                latency_ms=120,
                created_at=datetime.now(UTC),
            ),
            AgentRunRecord(
                id=uuid4(),
                lead_id=lead_id,
                agent_type="lead_orchestration",
                model="claude-test",
                status="completed",
                input={"email": "private@example.com"},
                output={
                    "score": 88,
                    "classification": "HOT",
                    "route": "research",
                    "next_action": "research_company",
                },
                latency_ms=250,
                created_at=datetime.now(UTC),
            ),
        ]
        self.transitions = [
            AgentStateTransitionRecord(
                id=uuid4(),
                agent_run_id=self.runs[1].id,
                lead_id=lead_id,
                from_state=AgentStep.START,
                to_state=AgentStep.LOAD_LEAD,
                route=None,
                payload={"status": "started"},
                created_at=datetime.now(UTC),
            ),
            AgentStateTransitionRecord(
                id=uuid4(),
                agent_run_id=self.runs[1].id,
                lead_id=lead_id,
                from_state=AgentStep.PERSIST_AGENT_STATE,
                to_state=AgentStep.END,
                route=AgentRoute.RESEARCH,
                payload={"status": "completed"},
                created_at=datetime.now(UTC),
            ),
        ]
        self.search_calls: list[dict[str, object]] = []
        self.counts = PipelineCounts(
            total_leads=9,
            hot=4,
            warm=3,
            cold=2,
            research=4,
            nurture=3,
            stop=2,
        )

    def get_lead(self, lead_id: UUID) -> LeadRecord | None:
        return next((lead for lead in self.leads if lead.id == lead_id), None)

    def search_leads(
        self,
        *,
        classification: LeadClassification | None,
        industry: str | None,
        country: str | None,
        company: str | None,
        limit: int,
    ) -> list[LeadRecord]:
        self.search_calls.append(
            {
                "classification": classification,
                "industry": industry,
                "country": country,
                "company": company,
                "limit": limit,
            }
        )
        results = self.leads
        if classification is not None:
            results = [lead for lead in results if lead.classification == classification]
        if industry is not None:
            results = [lead for lead in results if lead.industry == industry]
        if country is not None:
            results = [lead for lead in results if lead.country == country]
        if company is not None:
            results = [lead for lead in results if lead.company == company]
        return results[:limit]

    def get_lead_runs(self, lead_id: UUID) -> list[AgentRunRecord]:
        return [run for run in self.runs if run.lead_id == lead_id]

    def get_lead_transitions(
        self, lead_id: UUID
    ) -> list[AgentStateTransitionRecord]:
        return [item for item in self.transitions if item.lead_id == lead_id]

    def get_agent_run(self, agent_run_id: UUID) -> AgentRunRecord | None:
        return next((run for run in self.runs if run.id == agent_run_id), None)

    def get_pipeline_counts(self) -> PipelineCounts:
        return self.counts


class FakeToolRetrievalService:
    def __init__(self) -> None:
        self.results = [
            RetrievedChunk(
                document_id=uuid4(),
                chunk_id=uuid4(),
                title="Sales Playbook",
                content="Use a focused pilot for qualified B2B SaaS leads.",
                similarity=0.91,
                metadata={"source": "demo_knowledge/sales_playbook.md"},
            )
        ]
        self.calls: list[tuple[str, int | None]] = []

    def search(self, query: str, *, top_k: int | None = None) -> list[RetrievedChunk]:
        self.calls.append((query, top_k))
        return self.results[:top_k]


class InMemoryToolCallRepository:
    def __init__(self) -> None:
        self.calls: list[ToolCallRecord] = []

    def create(self, call: ToolCallAuditCreate) -> ToolCallRecord:
        record = ToolCallRecord(
            id=uuid4(),
            created_at=datetime.now(UTC),
            **call.model_dump(),
        )
        self.calls.append(record)
        return record


@dataclass
class MCPContext:
    service: ToolExecutionService
    data: FakeMCPDataRepository
    retrieval: FakeToolRetrievalService
    audits: InMemoryToolCallRepository


@pytest.fixture
def mcp_context() -> MCPContext:
    data = FakeMCPDataRepository()
    retrieval = FakeToolRetrievalService()
    audits = InMemoryToolCallRepository()
    registry = build_tool_registry(
        repository=data,
        retrieval_service=retrieval,  # type: ignore[arg-type]
    )
    return MCPContext(
        service=ToolExecutionService(
            registry=registry,
            audit_repository=audits,
        ),
        data=data,
        retrieval=retrieval,
        audits=audits,
    )


def _lead(*, index: int = 0) -> LeadRecord:
    return LeadRecord(
        id=uuid4(),
        external_id=f"lead-{index}",
        name=f"Buyer {index}",
        email=f"buyer{index}@example.com",
        company="Acme",
        job_title="Head of Sales",
        company_size=80,
        industry="B2B SaaS",
        country="Brazil",
        website="https://example.com",
        score=88,
        classification=LeadClassification.HOT,
        qualification_reason="Internal reason not needed by the tool.",
        next_action=NextAction.PERSONALIZED_OUTREACH,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_get_lead_returns_safe_data(mcp_context: MCPContext) -> None:
    lead = mcp_context.data.leads[0]
    result = mcp_context.service.execute("get_lead", {"lead_id": str(lead.id)})
    output = result.output.model_dump(mode="json")

    assert output["lead"]["id"] == str(lead.id)
    assert output["lead"]["company"] == "Acme"
    assert "email" not in output["lead"]
    assert "website" not in output["lead"]


def test_get_lead_rejects_missing_lead_and_audits_failure(
    mcp_context: MCPContext,
) -> None:
    with pytest.raises(LeadNotFoundError):
        mcp_context.service.execute("get_lead", {"lead_id": str(uuid4())})

    assert mcp_context.audits.calls[-1].status.value == "failed"
    assert mcp_context.audits.calls[-1].error == "lead_not_found"


def test_search_leads_uses_only_controlled_filters(mcp_context: MCPContext) -> None:
    result = mcp_context.service.execute(
        "search_leads",
        {
            "classification": "HOT",
            "industry": "B2B SaaS",
            "country": "Brazil",
            "company": "Acme",
            "limit": 10,
        },
    )

    assert result.output.model_dump()["count"] == 1
    assert mcp_context.data.search_calls[-1] == {
        "classification": LeadClassification.HOT,
        "industry": "B2B SaaS",
        "country": "Brazil",
        "company": "Acme",
        "limit": 10,
    }


def test_search_leads_rejects_unapproved_filter(mcp_context: MCPContext) -> None:
    with pytest.raises(ToolInputInvalidError):
        mcp_context.service.execute(
            "search_leads",
            {"table": "agent_runs", "limit": 10},
        )

    assert mcp_context.audits.calls[-1].status.value == "rejected"


def test_get_lead_history_returns_runs_routes_and_transitions(
    mcp_context: MCPContext,
) -> None:
    lead_id = mcp_context.data.leads[0].id
    result = mcp_context.service.execute(
        "get_lead_history", {"lead_id": str(lead_id)}
    )
    output = result.output.model_dump(mode="json")

    assert len(output["qualification_runs"]) == 1
    assert output["orchestration_runs"][0]["route"] == "research"
    assert output["transitions"][0]["from_state"] == "START"
    assert output["transitions"][-1]["to_state"] == "END"


def test_search_internal_knowledge_reuses_retrieval_service(
    mcp_context: MCPContext,
) -> None:
    result = mcp_context.service.execute(
        "search_internal_knowledge",
        {"query": "Head of Sales pilot", "top_k": 2},
    )
    output = result.output.model_dump(mode="json")

    assert mcp_context.retrieval.calls == [("Head of Sales pilot", 2)]
    assert output["results"][0]["title"] == "Sales Playbook"
    assert output["count"] == 1


def test_get_agent_run_returns_safe_auditable_summary(
    mcp_context: MCPContext,
) -> None:
    run = mcp_context.data.runs[1]
    result = mcp_context.service.execute(
        "get_agent_run", {"agent_run_id": str(run.id)}
    )
    output = result.output.model_dump(mode="json")["run"]

    assert output["id"] == str(run.id)
    assert output["route"] == "research"
    assert "input" not in output
    assert "output" not in output


def test_get_pipeline_summary_returns_bounded_aggregates(
    mcp_context: MCPContext,
) -> None:
    result = mcp_context.service.execute("get_pipeline_summary", {})

    assert result.output.model_dump() == {
        "total_leads": 9,
        "hot": 4,
        "warm": 3,
        "cold": 2,
        "research": 4,
        "nurture": 3,
        "stop": 2,
    }


def test_unknown_tool_is_rejected_and_audited(
    mcp_context: MCPContext, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        with pytest.raises(ToolNotFoundError):
            mcp_context.service.execute("delete_lead", {})

    assert "unknown_tool_rejected" in caplog.text
    assert mcp_context.audits.calls[-1].status.value == "rejected"
    assert mcp_context.audits.calls[-1].error == "unknown_tool"


def test_invalid_tool_input_is_rejected_before_handler(
    mcp_context: MCPContext,
) -> None:
    with pytest.raises(ToolInputInvalidError):
        mcp_context.service.execute("get_lead", {"lead_id": "not-a-uuid"})

    assert mcp_context.audits.calls[-1].error == "invalid_tool_input"


def test_successful_tool_call_is_audited(mcp_context: MCPContext) -> None:
    lead = mcp_context.data.leads[0]
    result = mcp_context.service.execute("get_lead", {"lead_id": str(lead.id)})
    audit = mcp_context.audits.calls[-1]

    assert result.audit.id == audit.id
    assert audit.tool_name == "get_lead"
    assert audit.status.value == "completed"
    assert audit.lead_id == lead.id
    assert audit.output is not None


def test_tool_failure_is_audited(mcp_context: MCPContext) -> None:
    missing = uuid4()
    with pytest.raises(LeadNotFoundError):
        mcp_context.service.execute("get_lead", {"lead_id": str(missing)})

    audit = mcp_context.audits.calls[-1]
    assert audit.status.value == "failed"
    assert audit.error == "lead_not_found"
    assert audit.output is None


def test_secrets_are_redacted_from_logs_and_audit(
    mcp_context: MCPContext, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "never-store-this-test-credential"
    with caplog.at_level(logging.INFO):
        with pytest.raises(ToolInputInvalidError):
            mcp_context.service.execute(
                "search_leads",
                {"api_key": secret, "limit": 1},
            )

    assert secret not in caplog.text
    assert secret not in str(mcp_context.audits.calls[-1].model_dump())
    assert mcp_context.audits.calls[-1].input["api_key"] == "[REDACTED]"


def test_search_leads_respects_result_limit(mcp_context: MCPContext) -> None:
    mcp_context.data.leads = [_lead(index=index) for index in range(5)]
    result = mcp_context.service.execute("search_leads", {"limit": 2})

    assert result.output.model_dump()["count"] == 2
    assert len(result.output.model_dump()["leads"]) == 2


def test_registry_has_explicit_schemas_for_exact_allowlist(
    mcp_context: MCPContext,
) -> None:
    definitions = mcp_context.service.registry.definitions()

    assert {definition.name for definition in definitions} == set(ToolName)
    assert all(definition.input_schema["type"] == "object" for definition in definitions)
    assert all(definition.output_schema["type"] == "object" for definition in definitions)
    assert all(definition.handler for definition in definitions)


def test_mcp_server_exposes_only_registered_tools(mcp_context: MCPContext) -> None:
    async def discover_server() -> tuple[list[str], set[str]]:
        async with Client(create_mcp_server(mcp_context.service)) as client:
            result = await client.list_tools()
            capabilities = {
                name
                for name, value in client.server_capabilities.model_dump(
                    exclude_none=True
                ).items()
                if value is not None
            }
            return [tool.name for tool in result.tools], capabilities

    tool_names, capabilities = asyncio.run(discover_server())
    assert set(tool_names) == {name.value for name in ToolName}
    assert capabilities == {"tools"}


def test_mcp_client_can_call_get_lead_with_structured_result(
    mcp_context: MCPContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lead = mcp_context.data.leads[0]

    async def call_tool() -> tuple[object, list[str]]:
        async with Client(create_mcp_server(mcp_context.service)) as client:
            valid_result = await client.call_tool(
                "get_lead",
                {"lead_id": str(lead.id)},
            )
            errors = []
            for name, arguments in (
                ("get_lead", {"lead_id": str(lead.id), "sql": "SELECT 1"}),
                ("delete_lead", {"lead_id": str(lead.id)}),
                ("get_lead", {"lead_id": str(uuid4())}),
            ):
                try:
                    await client.call_tool(name, arguments)
                except MCPError as exc:
                    errors.append(exc.message)
            return valid_result, errors

    with caplog.at_level(logging.INFO):
        result, errors = asyncio.run(call_tool())
    assert result.is_error is False
    assert result.structured_content["lead"]["id"] == str(lead.id)
    assert errors == [
        "Invalid tool input",
        "Unknown tool",
        "The requested lead was not found",
    ]
    assert [call.status.value for call in mcp_context.audits.calls[-4:]] == [
        "completed",
        "rejected",
        "rejected",
        "failed",
    ]
    assert "Traceback" not in caplog.text


def test_previous_http_endpoints_remain_compatible(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    qualification = context.client.post("/api/v1/leads/qualify", json=valid_payload)
    orchestration = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert qualification.status_code == 200
    assert orchestration.status_code == 200
