from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    get_agent_orchestration_service,
    get_qualification_service,
)
from app.schemas.lead import LeadQualifyRequest
from app.schemas.orchestration import AgentOrchestrationResponse
from app.schemas.qualification import LeadQualifyResponse
from app.services.agent_orchestration_service import AgentOrchestrationService
from app.services.qualification_service import QualificationService


router = APIRouter(prefix="/api/v1/leads", tags=["leads"])


@router.post(
    "/qualify",
    response_model=LeadQualifyResponse,
    summary="Create or update and qualify a B2B lead",
)
def qualify_lead(
    payload: LeadQualifyRequest,
    service: Annotated[QualificationService, Depends(get_qualification_service)],
) -> LeadQualifyResponse:
    return service.qualify(payload)


@router.post(
    "/agent",
    response_model=AgentOrchestrationResponse,
    summary="Run the deterministic lead orchestration graph",
)
def orchestrate_lead(
    payload: LeadQualifyRequest,
    service: Annotated[
        AgentOrchestrationService,
        Depends(get_agent_orchestration_service),
    ],
) -> AgentOrchestrationResponse:
    return service.orchestrate(payload)
