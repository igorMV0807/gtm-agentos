from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import ExternalActionConflictError
from app.models.external_actions import ExternalActionRecord
from app.schemas.external_actions import (
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
