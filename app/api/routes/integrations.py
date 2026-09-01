from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from app.api.dependencies import get_external_action_service, get_webhook_signer
from app.core.exceptions import ExternalActionInvalidError
from app.integrations.n8n import WebhookSigner
from app.schemas.external_actions import ExternalActionResponse, N8nCallbackPayload
from app.services.external_action_service import (
    ExternalActionService,
    external_action_response,
)


router = APIRouter(prefix="/api/v1/integrations/n8n", tags=["integrations"])


@router.post(
    "/callback",
    response_model=ExternalActionResponse,
    summary="Receive a signed result for an existing external action",
)
async def receive_n8n_callback(
    request: Request,
    service: Annotated[ExternalActionService, Depends(get_external_action_service)],
    signer: Annotated[WebhookSigner, Depends(get_webhook_signer)],
) -> ExternalActionResponse:
    body = await request.body()
    if len(body) > 16384:
        raise ExternalActionInvalidError("n8n callback body exceeds 16384 bytes")
    signer.verify(
        body,
        request.headers.get("X-GTM-Timestamp"),
        request.headers.get("X-GTM-Signature"),
    )
    try:
        payload = N8nCallbackPayload.model_validate_json(body)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ExternalActionInvalidError("Invalid n8n callback payload") from exc
    return external_action_response(service.process_callback(payload))
