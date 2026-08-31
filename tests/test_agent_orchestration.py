import pytest

from app.agents.routing import route_to_node
from app.agents.state import AgentStep
from app.core.exceptions import AgentRouteInvalidError, LLMProviderError
from app.schemas.qualification import (
    LeadClassification,
    NextAction,
    QualificationResult,
)
from tests.conftest import ScenarioContext


def _set_qualification(
    context: ScenarioContext,
    *,
    score: int,
    classification: LeadClassification,
    next_action: NextAction,
) -> None:
    context.llm.result = QualificationResult(
        score=score,
        classification=classification,
        reason=f"Deterministic {classification.value} test qualification.",
        next_action=next_action,
    )


def _orchestration_run(context: ScenarioContext):
    return next(
        run
        for run in context.runs.runs.values()
        if run.agent_type == "lead_orchestration"
    )


def test_hot_follows_research(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    _set_qualification(
        context,
        score=90,
        classification=LeadClassification.HOT,
        next_action=NextAction.PERSONALIZED_OUTREACH,
    )

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert response.json()["route"] == "research"
    assert response.json()["next_action"] == "research_company"


def test_warm_follows_nurture(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    _set_qualification(
        context,
        score=60,
        classification=LeadClassification.WARM,
        next_action=NextAction.NURTURE,
    )

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert response.json()["route"] == "nurture"
    assert response.json()["next_action"] == "nurture_sequence"


def test_cold_follows_stop(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    _set_qualification(
        context,
        score=20,
        classification=LeadClassification.COLD,
        next_action=NextAction.DISCARD,
    )

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert response.json()["route"] == "stop"
    assert response.json()["next_action"] == "discard"


def test_invalid_route_is_rejected() -> None:
    with pytest.raises(AgentRouteInvalidError):
        route_to_node("llm_invented_node")


def test_state_transitions_are_persisted(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    transitions = context.transitions.transitions
    assert [(item.from_state, item.to_state) for item in transitions] == [
        (AgentStep.START, AgentStep.LOAD_LEAD),
        (AgentStep.LOAD_LEAD, AgentStep.QUALIFY_LEAD),
        (AgentStep.QUALIFY_LEAD, AgentStep.ROUTE_BY_CLASSIFICATION),
        (AgentStep.ROUTE_BY_CLASSIFICATION, AgentStep.RESEARCH_STATE),
        (AgentStep.RESEARCH_STATE, AgentStep.PERSIST_AGENT_STATE),
        (AgentStep.PERSIST_AGENT_STATE, AgentStep.END),
    ]
    assert {str(item.agent_run_id) for item in transitions} == {
        response.json()["agent_run_id"]
    }


def test_node_failure_marks_orchestration_run_failed(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.llm.error = LLMProviderError("provider detail")

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_provider_error"
    graph_run = _orchestration_run(context)
    assert graph_run.status == "failed"
    assert graph_run.error == "llm_provider_error"
    assert any(
        transition.payload.get("error") == "llm_provider_error"
        for transition in context.transitions.transitions
    )


def test_agent_endpoint_returns_expected_structure(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert set(response.json()) == {
        "lead_id",
        "agent_run_id",
        "score",
        "classification",
        "route",
        "next_action",
        "status",
    }
    assert response.json()["status"] == "completed"
    assert _orchestration_run(context).status == "completed"


def test_phase_one_endpoint_remains_compatible(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    response = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert response.status_code == 200
    assert set(response.json()) == {
        "lead_id",
        "score",
        "classification",
        "reason",
        "next_action",
    }
    assert response.json()["next_action"] == "personalized_outreach"
