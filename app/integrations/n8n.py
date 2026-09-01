import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import (
    ExternalIntegrationError,
    WebhookReplayError,
    WebhookSignatureInvalidError,
)
from app.models.external_actions import ExternalActionRecord


logger = logging.getLogger(__name__)


class N8nDispatchResult(BaseModel):
    accepted: bool = True
    external_reference: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


class N8nActionDispatcher(Protocol):
    def execute_action(self, action: ExternalActionRecord) -> N8nDispatchResult: ...


class WebhookSigner:
    def __init__(
        self,
        secret: str,
        *,
        max_age_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(secret.encode("utf-8")) < 16:
            raise ValueError("Webhook secret must contain at least 16 bytes")
        if not 30 <= max_age_seconds <= 900:
            raise ValueError("Webhook signature window must be between 30 and 900 seconds")
        self._secret = secret.encode("utf-8")
        self._max_age_seconds = max_age_seconds
        self._clock = clock

    def sign(self, body: bytes, timestamp: str) -> str:
        message = timestamp.encode("ascii") + b"." + body
        digest = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def verify(self, body: bytes, timestamp: str | None, signature: str | None) -> None:
        if not timestamp or not signature:
            raise WebhookSignatureInvalidError("Missing webhook signature headers")
        if (
            len(timestamp) > 20
            or not timestamp.isascii()
            or not timestamp.isdigit()
        ):
            raise WebhookSignatureInvalidError("Invalid webhook timestamp")
        try:
            sent_at = int(timestamp)
        except ValueError as exc:
            raise WebhookSignatureInvalidError("Invalid webhook timestamp") from exc
        if abs(int(self._clock()) - sent_at) > self._max_age_seconds:
            raise WebhookReplayError("Webhook timestamp is outside the accepted window")
        expected = self.sign(body, timestamp)
        if not hmac.compare_digest(expected, signature):
            raise WebhookSignatureInvalidError("Webhook HMAC mismatch")


class N8nActionService:
    def __init__(
        self,
        *,
        webhook_url: str,
        signer: WebhookSigner,
        timeout_seconds: float = 10.0,
        client: object | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._webhook_url = webhook_url
        self._signer = signer
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.Client()
        self._clock = clock

    def execute_action(self, action: ExternalActionRecord) -> N8nDispatchResult:
        body = json.dumps(
            {
                "action_id": str(action.id),
                "action_type": action.action_type.value,
                "payload": action.payload,
                "idempotency_key": action.idempotency_key,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        timestamp = str(int(self._clock()))
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": action.idempotency_key,
            "X-GTM-Timestamp": timestamp,
            "X-GTM-Signature": self._signer.sign(body, timestamp),
        }

        response = None
        for attempt in range(2):
            try:
                response = self._client.post(  # type: ignore[union-attr]
                    self._webhook_url,
                    content=body,
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
                if response.status_code < 500:
                    break
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt == 1:
                    raise ExternalIntegrationError("n8n request failed") from exc

        if response is None or not 200 <= response.status_code < 300:
            raise ExternalIntegrationError("n8n rejected the external action")

        external_reference = None
        try:
            data = response.json()
        except (ValueError, TypeError):
            data = {}
        if isinstance(data, dict) and isinstance(data.get("external_reference"), str):
            external_reference = data["external_reference"][:500]

        logger.info(
            "n8n_request_sent",
            extra={
                "action_id": str(action.id),
                "action_type": action.action_type.value,
                "status_code": response.status_code,
            },
        )
        return N8nDispatchResult(
            accepted=True,
            external_reference=external_reference,
        )
