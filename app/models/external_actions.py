from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.external_actions import (
    ExternalActionEventType,
    ExternalActionStatus,
    ExternalActionType,
)


class ExternalActionRecord(BaseModel):
    id: UUID
    lead_id: UUID
    agent_run_id: UUID | None = None
    action_type: ExternalActionType
    payload: dict[str, object]
    status: ExternalActionStatus
    requires_approval: bool
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    idempotency_key: str
    external_reference: str | None = None
    result: dict[str, object] | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")


class ExternalActionEventRecord(BaseModel):
    id: UUID
    action_id: UUID
    event_type: ExternalActionEventType
    metadata: dict[str, object]
    created_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")
