from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_external_action_service
from app.schemas.external_actions import ExternalActionResponse
from app.services.external_action_service import (
    ExternalActionService,
    external_action_response,
)


router = APIRouter(prefix="/api/v1/actions", tags=["external-actions"])


@router.post(
    "/{action_id}/approve",
    response_model=ExternalActionResponse,
    summary="Approve and dispatch an existing allowlisted external action",
)
def approve_external_action(
    action_id: UUID,
    service: Annotated[ExternalActionService, Depends(get_external_action_service)],
) -> ExternalActionResponse:
    return external_action_response(service.approve(action_id))


@router.post(
    "/{action_id}/reject",
    response_model=ExternalActionResponse,
    summary="Reject an existing pending external action",
)
def reject_external_action(
    action_id: UUID,
    service: Annotated[ExternalActionService, Depends(get_external_action_service)],
) -> ExternalActionResponse:
    return external_action_response(service.reject(action_id))
