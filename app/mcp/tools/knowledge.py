from app.mcp.schemas import (
    KnowledgeSearchResult,
    SearchInternalKnowledgeInput,
    SearchInternalKnowledgeOutput,
)
from app.services.retrieval_service import RetrievalService


class KnowledgeTools:
    def __init__(self, retrieval_service: RetrievalService) -> None:
        self._retrieval_service = retrieval_service

    def search_internal_knowledge(
        self, payload: SearchInternalKnowledgeInput
    ) -> SearchInternalKnowledgeOutput:
        chunks = self._retrieval_service.search(payload.query, top_k=payload.top_k)
        results = [
            KnowledgeSearchResult(
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                title=chunk.title,
                content=chunk.content,
                similarity=chunk.similarity,
            )
            for chunk in chunks[: payload.top_k]
        ]
        return SearchInternalKnowledgeOutput(
            query=payload.query,
            results=results,
            count=len(results),
        )
