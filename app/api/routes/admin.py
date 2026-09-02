from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_observability_service, require_operator
from app.schemas.observability import (
    ActionsResponse,
    AgentRunItem,
    AgentRunsResponse,
    LeadTimelineResponse,
    OverviewResponse,
    UsageResponse,
)
from app.services.observability_service import ObservabilityService


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["operator-observability"],
    dependencies=[Depends(require_operator)],
)


@router.get("/overview", response_model=OverviewResponse)
def overview(
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
) -> OverviewResponse:
    return service.overview()


@router.get("/agent-runs", response_model=AgentRunsResponse)
def agent_runs(
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
) -> AgentRunsResponse:
    return service.list_agent_runs(limit=limit, offset=offset)


@router.get("/agent-runs/{run_id}", response_model=AgentRunItem)
def inspect_agent_run(
    run_id: UUID,
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
) -> AgentRunItem:
    return service.inspect_agent_run(run_id)


@router.get("/actions", response_model=ActionsResponse)
def actions(
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
    status: str | None = Query(
        default=None,
        pattern=r"^(pending|approved|executing|completed|failed|rejected)$",
    ),
) -> ActionsResponse:
    return service.list_actions(limit=limit, offset=offset, status=status)


@router.get("/leads/{lead_id}/timeline", response_model=LeadTimelineResponse)
def lead_timeline(
    lead_id: UUID,
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
) -> LeadTimelineResponse:
    return service.lead_timeline(lead_id)


@router.get("/usage", response_model=UsageResponse)
def usage(
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=100000),
) -> UsageResponse:
    return service.usage(limit=limit, offset=offset)
