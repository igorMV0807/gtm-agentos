from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.agents.state import AgentStep
from app.schemas.orchestration import AgentRoute


class AgentStateTransitionRecord(BaseModel):
    id: UUID
    agent_run_id: UUID
    lead_id: UUID
    from_state: AgentStep
    to_state: AgentStep
    route: AgentRoute | None = None
    payload: dict[str, object]
    created_at: datetime | None = None

    model_config = ConfigDict(extra="ignore")
