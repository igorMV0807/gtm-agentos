import logging

from app.repositories.rag_repository import VectorSearchRepository
from app.schemas.knowledge import RetrievedChunk
from app.schemas.lead import LeadQualifyRequest
from app.services.embedding_service import EmbeddingProvider


logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        repository: VectorSearchRepository,
        top_k: int,
        similarity_threshold: float,
    ) -> None:
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between 0 and 1")
        self._embedding_provider = embedding_provider
        self._repository = repository
        self._top_k = top_k
        self._similarity_threshold = similarity_threshold

    def search(self, query: str, *, top_k: int | None = None) -> list[RetrievedChunk]:
        query = query.strip()
        if not query:
            raise ValueError("Retrieval query must not be blank")
        requested_top_k = top_k if top_k is not None else self._top_k
        if not 1 <= requested_top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")

        logger.info(
            "rag_retrieval_started",
            extra={"top_k": requested_top_k},
        )
        embedding = self._embedding_provider.embed_text(query, input_type="query")
        results = self._repository.search(
            embedding,
            top_k=requested_top_k,
            similarity_threshold=self._similarity_threshold,
        )
        relevant = sorted(
            (
                item
                for item in results
                if item.similarity >= self._similarity_threshold
            ),
            key=lambda item: item.similarity,
            reverse=True,
        )[:requested_top_k]
        logger.info(
            "rag_retrieval_completed",
            extra={
                "result_count": len(relevant),
                "top_k": requested_top_k,
            },
        )
        if not relevant:
            logger.info("rag_no_relevant_context")
        return relevant

    @staticmethod
    def build_lead_query(lead: LeadQualifyRequest) -> str:
        attributes = [f"company {lead.company}"]
        if lead.job_title:
            attributes.append(f"role {lead.job_title}")
        if lead.company_size:
            attributes.append(f"company size {lead.company_size} employees")
        if lead.industry:
            attributes.append(f"industry {lead.industry}")
        if lead.country:
            attributes.append(f"market {lead.country}")
        return "Find internal GTM knowledge relevant to " + ", ".join(attributes) + "."
