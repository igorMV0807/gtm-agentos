import logging
from dataclasses import dataclass

from app.core.exceptions import (
    DatabaseUnavailableError,
    DuplicateLeadConflictError,
)
from app.models.lead import LeadRecord
from app.repositories.lead_repository import LeadRepository
from app.schemas.lead import LeadQualifyRequest


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeadIngestionResult:
    lead: LeadRecord
    duplicate: bool


class LeadService:
    def __init__(self, repository: LeadRepository) -> None:
        self._repository = repository

    def ingest(self, payload: LeadQualifyRequest) -> LeadIngestionResult:
        existing = self._repository.find_existing(payload)
        if existing is not None:
            updated = self._repository.update(existing.id, payload)
            logger.info(
                "lead_duplicate_detected",
                extra={"lead_id": str(updated.id)},
            )
            return LeadIngestionResult(lead=updated, duplicate=True)

        try:
            created = self._repository.create(payload)
        except DuplicateLeadConflictError:
            # A unique index can win the race after the initial lookup. Re-read the
            # winning record and update it instead of creating a second lead.
            existing = self._repository.find_existing(payload)
            if existing is None:
                raise DatabaseUnavailableError(
                    "Duplicate lead existed but could not be retrieved"
                )
            updated = self._repository.update(existing.id, payload)
            logger.info(
                "lead_duplicate_detected",
                extra={"lead_id": str(updated.id)},
            )
            return LeadIngestionResult(lead=updated, duplicate=True)

        logger.info("lead_created", extra={"lead_id": str(created.id)})
        return LeadIngestionResult(lead=created, duplicate=False)

