from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import (
    ExternalActionConflictError,
    ExternalActionInvalidError,
    ExternalIntegrationError,
)
from app.models.external_actions import ExternalActionRecord
from app.schemas.external_actions import (
    EmailActionPayload,
    EmailDraft,
    ExternalActionStatus,
    ExternalActionType,
)


class EmailDeliveryResult(BaseModel):
    external_reference: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(extra="forbid")


class EmailProvider(Protocol):
    def create_draft(self, draft: EmailDraft) -> EmailDeliveryResult: ...

    def send_email(self, action: ExternalActionRecord) -> EmailDeliveryResult: ...


class ResendEmailProvider:
    """Fixed-host Resend adapter restricted to one validation recipient."""

    _SEND_URL = "https://api.resend.com/emails"

    def __init__(
        self,
        *,
        api_key: str,
        test_recipient: str,
        from_email: str = "GTM AgentOS <onboarding@resend.dev>",
        timeout_seconds: float = 10.0,
        client: object | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Resend API key is required")
        if not test_recipient:
            raise ValueError("Validation email recipient is required")
        if not from_email:
            raise ValueError("Resend sender is required")
        self._authorization = f"Bearer {api_key}"
        self._test_recipient = test_recipient.strip().casefold()
        self._from_email = from_email.strip()
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.Client()

    def create_draft(self, draft: EmailDraft) -> EmailDeliveryResult:
        del draft
        raise ExternalIntegrationError(
            "Email drafts are created by the qualification provider"
        )

    def send_email(self, action: ExternalActionRecord) -> EmailDeliveryResult:
        require_approved_email_action(action)
        payload = EmailActionPayload.model_validate(action.payload)
        recipient = str(payload.to_email)
        if recipient.casefold() != self._test_recipient:
            raise ExternalActionInvalidError(
                "Email recipient is outside the configured validation allowlist"
            )

        try:
            response = self._client.post(
                self._SEND_URL,
                headers={
                    "Authorization": self._authorization,
                    "Content-Type": "application/json",
                    "Idempotency-Key": action.idempotency_key,
                },
                json={
                    "from": self._from_email,
                    "to": [recipient],
                    "subject": payload.subject,
                    "text": payload.body,
                },
                timeout=self._timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise ExternalIntegrationError("Resend request failed") from exc
        if not 200 <= response.status_code < 300:
            raise ExternalIntegrationError("Resend rejected the email action")
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise ExternalIntegrationError("Resend response was invalid") from exc
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            raise ExternalIntegrationError("Resend returned no email reference")
        return EmailDeliveryResult(external_reference=data["id"])


def require_approved_email_action(action: ExternalActionRecord) -> None:
    allowed_statuses = {
        ExternalActionStatus.APPROVED,
        ExternalActionStatus.EXECUTING,
        ExternalActionStatus.COMPLETED,
    }
    if (
        action.action_type != ExternalActionType.SEND_APPROVED_EMAIL
        or action.approved_at is None
        or action.status not in allowed_statuses
    ):
        raise ExternalActionConflictError(
            "Email delivery requires an approved send_approved_email action"
        )
