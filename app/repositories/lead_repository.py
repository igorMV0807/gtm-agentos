from typing import Protocol
from uuid import UUID

from supabase import Client

from app.core.exceptions import (
    DatabaseUnavailableError,
    DuplicateLeadConflictError,
)
from app.models.lead import LeadRecord
from app.schemas.lead import LeadQualifyRequest
from app.schemas.qualification import QualificationResult


class LeadRepository(Protocol):
    def find_existing(self, lead: LeadQualifyRequest) -> LeadRecord | None: ...

    def create(self, lead: LeadQualifyRequest) -> LeadRecord: ...

    def update(self, lead_id: UUID, lead: LeadQualifyRequest) -> LeadRecord: ...

    def save_qualification(
        self, lead_id: UUID, qualification: QualificationResult
    ) -> LeadRecord: ...


class SupabaseLeadRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def find_existing(self, lead: LeadQualifyRequest) -> LeadRecord | None:
        try:
            if lead.external_id:
                response = (
                    self._client.table("leads")
                    .select("*")
                    .eq("external_id", lead.external_id)
                    .limit(1)
                    .execute()
                )
                if response.data:
                    return LeadRecord.model_validate(response.data[0])

            response = (
                self._client.table("leads")
                .select("*")
                .eq("email", str(lead.email).lower())
                .eq("company", lead.company)
                .limit(1)
                .execute()
            )
            if response.data:
                return LeadRecord.model_validate(response.data[0])
            return None
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to look up lead") from exc

    def create(self, lead: LeadQualifyRequest) -> LeadRecord:
        try:
            response = (
                self._client.table("leads")
                .insert(lead.to_persistence_dict())
                .execute()
            )
            return self._one(response.data, "create lead")
        except Exception as exc:
            if self._is_unique_violation(exc):
                raise DuplicateLeadConflictError("Concurrent duplicate lead") from exc
            raise DatabaseUnavailableError("Failed to create lead") from exc

    def update(self, lead_id: UUID, lead: LeadQualifyRequest) -> LeadRecord:
        try:
            response = (
                self._client.table("leads")
                .update(lead.to_persistence_dict())
                .eq("id", str(lead_id))
                .execute()
            )
            return self._one(response.data, "update lead")
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to update lead") from exc

    def save_qualification(
        self, lead_id: UUID, qualification: QualificationResult
    ) -> LeadRecord:
        values = {
            "score": qualification.score,
            "classification": qualification.classification.value,
            "qualification_reason": qualification.reason,
            "next_action": qualification.next_action.value,
        }
        try:
            response = (
                self._client.table("leads")
                .update(values)
                .eq("id", str(lead_id))
                .execute()
            )
            return self._one(response.data, "save qualification")
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to save qualification") from exc

    @staticmethod
    def _one(data: object, operation: str) -> LeadRecord:
        if not isinstance(data, list) or not data:
            raise DatabaseUnavailableError(f"Database returned no row for {operation}")
        return LeadRecord.model_validate(data[0])

    @staticmethod
    def _is_unique_violation(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        if code is None and exc.args and isinstance(exc.args[0], dict):
            code = exc.args[0].get("code")
        return str(code) == "23505"

