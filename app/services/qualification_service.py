import logging
from time import perf_counter
from uuid import UUID

from app.core.exceptions import GTMAgentOSError
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.lead_repository import LeadRepository
from app.schemas.lead import LeadQualifyRequest
from app.schemas.qualification import LeadQualifyResponse
from app.services.lead_service import LeadService
from app.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class QualificationService:
    def __init__(
        self,
        *,
        lead_repository: LeadRepository,
        agent_run_repository: AgentRunRepository,
        llm_service: LLMService,
    ) -> None:
        self._lead_repository = lead_repository
        self._agent_run_repository = agent_run_repository
        self._llm_service = llm_service
        self._lead_service = LeadService(lead_repository)

    @property
    def model(self) -> str:
        return self._llm_service.model

    def qualify(self, payload: LeadQualifyRequest) -> LeadQualifyResponse:
        logger.info("lead_received")
        ingestion = self._lead_service.ingest(payload)
        lead = ingestion.lead

        run = self._agent_run_repository.create_started(
            lead_id=lead.id,
            agent_type="lead_qualification",
            model=self._llm_service.model,
            input_data=payload.model_dump(mode="json"),
        )

        logger.info(
            "qualification_started",
            extra={"lead_id": str(lead.id), "agent_run_id": str(run.id)},
        )
        started_at = perf_counter()

        try:
            qualification = self._llm_service.qualify(payload)
            self._lead_repository.save_qualification(lead.id, qualification)
            latency_ms = self._latency_ms(started_at)
            self._agent_run_repository.mark_completed(
                run.id,
                output=qualification,
                latency_ms=latency_ms,
            )
        except GTMAgentOSError as exc:
            latency_ms = self._latency_ms(started_at)
            self._mark_failed(run.id, exc.code, latency_ms)
            logger.warning(
                "qualification_failed",
                extra={
                    "lead_id": str(lead.id),
                    "agent_run_id": str(run.id),
                    "error_code": exc.code,
                },
            )
            raise

        logger.info(
            "qualification_completed",
            extra={
                "lead_id": str(lead.id),
                "agent_run_id": str(run.id),
                "latency_ms": latency_ms,
                "classification": qualification.classification.value,
            },
        )

        return LeadQualifyResponse(
            lead_id=lead.id,
            **qualification.model_dump(),
        )

    def _mark_failed(self, run_id: UUID, error_code: str, latency_ms: int) -> None:
        try:
            self._agent_run_repository.mark_failed(
                run_id,
                error=error_code,
                latency_ms=latency_ms,
            )
        except GTMAgentOSError:
            logger.exception(
                "agent_run_persist_failed",
                extra={"agent_run_id": str(run_id)},
            )

    @staticmethod
    def _latency_ms(started_at: float) -> int:
        return max(0, round((perf_counter() - started_at) * 1000))
