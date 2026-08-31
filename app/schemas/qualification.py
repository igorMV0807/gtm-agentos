from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LeadClassification(str, Enum):
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"


class NextAction(str, Enum):
    PERSONALIZED_OUTREACH = "personalized_outreach"
    NURTURE = "nurture"
    MANUAL_REVIEW = "manual_review"
    DISCARD = "discard"


class QualificationResult(BaseModel):
    score: Annotated[int, Field(ge=0, le=100)]
    classification: LeadClassification
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    next_action: NextAction

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LeadQualifyResponse(QualificationResult):
    lead_id: UUID

