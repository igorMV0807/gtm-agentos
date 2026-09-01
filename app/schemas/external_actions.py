import json
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    JsonValue,
    field_validator,
)


class ExternalActionType(str, Enum):
    CREATE_OR_UPDATE_CRM_LEAD = "create_or_update_crm_lead"
    CREATE_FOLLOW_UP_TASK = "create_follow_up_task"
    DRAFT_OUTREACH_EMAIL = "draft_outreach_email"
    SEND_APPROVED_EMAIL = "send_approved_email"
    MARK_LEAD_STATUS = "mark_lead_status"


class ExternalActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class ExternalActionEventType(str, Enum):
    ACTION_REQUESTED = "action_requested"
    EMAIL_DRAFT_CREATED = "email_draft_created"
    APPROVAL_GRANTED = "approval_granted"
    ACTION_REJECTED = "action_rejected"
    EXECUTION_STARTED = "execution_started"
    CALLBACK_RECEIVED = "callback_received"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmailDraft(StrictPayload):
    subject: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(min_length=1, max_length=5000)]
    reasoning_summary: Annotated[str, Field(min_length=1, max_length=500)]


class CreateOrUpdateCRMLeadPayload(StrictPayload):
    lead_id: UUID
    external_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    name: Annotated[str, Field(min_length=1, max_length=200)]
    email: EmailStr
    company: Annotated[str, Field(min_length=1, max_length=200)]
    job_title: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    classification: Literal["HOT", "WARM", "COLD"]


class CreateFollowUpTaskPayload(StrictPayload):
    lead_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    due_in_days: Annotated[int, Field(ge=1, le=90)] = 3


class EmailActionPayload(EmailDraft):
    lead_id: UUID
    to_email: EmailStr


class MarkLeadStatusPayload(StrictPayload):
    lead_id: UUID
    status: Literal[
        "new",
        "qualified",
        "nurture_pending",
        "contacted",
        "closed",
        "discard",
    ]


ACTION_PAYLOAD_MODELS: dict[ExternalActionType, type[BaseModel]] = {
    ExternalActionType.CREATE_OR_UPDATE_CRM_LEAD: CreateOrUpdateCRMLeadPayload,
    ExternalActionType.CREATE_FOLLOW_UP_TASK: CreateFollowUpTaskPayload,
    ExternalActionType.DRAFT_OUTREACH_EMAIL: EmailActionPayload,
    ExternalActionType.SEND_APPROVED_EMAIL: EmailActionPayload,
    ExternalActionType.MARK_LEAD_STATUS: MarkLeadStatusPayload,
}


class ExternalActionCreate(StrictPayload):
    lead_id: UUID
    agent_run_id: UUID | None = None
    action_type: ExternalActionType
    payload: dict[str, JsonValue]
    requires_approval: bool
    idempotency_key: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            pattern=r"^[A-Za-z0-9:_-]+$",
        ),
    ]


class ExternalActionResponse(StrictPayload):
    id: UUID
    lead_id: UUID
    agent_run_id: UUID | None = None
    action_type: ExternalActionType
    status: ExternalActionStatus
    requires_approval: bool
    idempotency_key: str
    external_reference: str | None = None
    error: str | None = None


class N8nCallbackPayload(StrictPayload):
    action_id: UUID
    status: Literal["completed", "failed"]
    external_reference: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def bound_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(value) > 30:
            raise ValueError("metadata may contain at most 30 fields")
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 8192:
            raise ValueError("metadata exceeds 8192 bytes")
        return value


def validate_action_payload(
    action_type: ExternalActionType,
    payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    model = ACTION_PAYLOAD_MODELS[action_type]
    return model.model_validate(payload).model_dump(mode="json")
