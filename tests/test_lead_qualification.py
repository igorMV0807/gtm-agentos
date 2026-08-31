import pytest

from app.core.exceptions import (
    DatabaseUnavailableError,
    LLMInvalidResponseError,
    LLMProviderError,
    LLMTimeoutError,
)
from app.schemas.qualification import (
    LeadClassification,
    NextAction,
    QualificationResult,
)
from tests.conftest import ScenarioContext


def test_valid_lead_is_qualified_and_persisted(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    response = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 87
    assert body["classification"] == "HOT"
    assert body["next_action"] == "personalized_outreach"
    assert len(context.leads.leads) == 1
    stored = next(iter(context.leads.leads.values()))
    assert str(stored.id) == body["lead_id"]
    assert stored.score == 87
    run = next(iter(context.runs.runs.values()))
    assert run.status == "completed"
    assert run.output is not None
    assert run.latency_ms is not None


def test_invalid_payload_returns_422(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    valid_payload.pop("email")

    response = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert response.status_code == 422
    assert context.llm.calls == 0
    assert not context.leads.leads


def test_duplicate_lead_reuses_record(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    first = context.client.post("/api/v1/leads/qualify", json=valid_payload)
    valid_payload["name"] = "John A. Smith"
    second = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["lead_id"] == second.json()["lead_id"]
    assert len(context.leads.leads) == 1
    assert len(context.runs.runs) == 2
    assert next(iter(context.leads.leads.values())).name == "John A. Smith"


def test_duplicate_without_external_id_uses_email_and_company(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    valid_payload.pop("external_id")
    first = context.client.post("/api/v1/leads/qualify", json=valid_payload)
    valid_payload["job_title"] = "VP of Sales"
    second = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["lead_id"] == second.json()["lead_id"]
    assert len(context.leads.leads) == 1
    assert next(iter(context.leads.leads.values())).job_title == "VP of Sales"


@pytest.mark.parametrize(
    ("score", "classification", "next_action"),
    [
        (90, LeadClassification.HOT, NextAction.PERSONALIZED_OUTREACH),
        (60, LeadClassification.WARM, NextAction.NURTURE),
        (20, LeadClassification.COLD, NextAction.DISCARD),
    ],
    ids=["HOT", "WARM", "COLD"],
)
def test_classifications_are_returned(
    context: ScenarioContext,
    valid_payload: dict[str, object],
    score: int,
    classification: LeadClassification,
    next_action: NextAction,
) -> None:
    context.llm.result = QualificationResult(
        score=score,
        classification=classification,
        reason="Test qualification.",
        next_action=next_action,
    )

    response = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert response.status_code == 200
    assert response.json()["classification"] == classification.value
    assert response.json()["score"] == score


def test_invalid_llm_response_is_safe_and_run_is_failed(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.llm.error = LLMInvalidResponseError("bad model output")

    response = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_invalid_response"
    assert "bad model output" not in response.text
    run = next(iter(context.runs.runs.values()))
    assert run.status == "failed"
    assert run.error == "llm_invalid_response"


def test_provider_failure_is_safe_and_run_is_failed(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.llm.error = LLMProviderError("provider leaked detail")

    response = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_provider_error"
    assert "provider leaked detail" not in response.text
    assert next(iter(context.runs.runs.values())).status == "failed"


def test_llm_timeout_returns_504(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.llm.error = LLMTimeoutError("timeout detail")

    response = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "llm_timeout"
    assert next(iter(context.runs.runs.values())).status == "failed"


def test_database_failure_returns_503_without_calling_llm(
    context: ScenarioContext, valid_payload: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_lookup(payload: object) -> None:
        raise DatabaseUnavailableError("database detail")

    monkeypatch.setattr(context.leads, "find_existing", fail_lookup)

    response = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "database detail" not in response.text
    assert context.llm.calls == 0
