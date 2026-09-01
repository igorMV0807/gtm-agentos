import re

from app.models.lead import AgentRunRecord, LeadRecord
from app.mcp.schemas import AgentRunSummary, SafeLead
from app.schemas.orchestration import AgentRoute
from app.schemas.qualification import LeadClassification


_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def safe_lead(record: LeadRecord) -> SafeLead:
    return SafeLead(
        id=record.id,
        external_id=record.external_id,
        name=record.name,
        company=record.company,
        job_title=record.job_title,
        company_size=record.company_size,
        industry=record.industry,
        country=record.country,
        score=record.score,
        classification=record.classification,
        next_action=record.next_action,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def safe_agent_run(record: AgentRunRecord) -> AgentRunSummary:
    output = record.output or {}
    classification = _classification(output.get("classification"))
    route = _route(output.get("route"))
    score = output.get("score")
    next_action = output.get("next_action")
    error_code = (
        record.error
        if record.error is not None and _SAFE_ERROR_CODE.fullmatch(record.error)
        else "internal_error" if record.error else None
    )
    return AgentRunSummary(
        id=record.id,
        lead_id=record.lead_id,
        agent_type=record.agent_type,
        model=record.model,
        status=record.status,
        classification=classification,
        score=score if isinstance(score, int) and not isinstance(score, bool) else None,
        route=route,
        next_action=next_action if isinstance(next_action, str) else None,
        latency_ms=record.latency_ms,
        error_code=error_code,
        created_at=record.created_at,
    )


def _classification(value: object) -> LeadClassification | None:
    try:
        return LeadClassification(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _route(value: object) -> AgentRoute | None:
    try:
        return AgentRoute(value) if isinstance(value, str) else None
    except ValueError:
        return None
