from typing import Protocol
from uuid import UUID

from supabase import Client

from app.core.exceptions import DatabaseUnavailableError, VectorSearchError
from app.models.knowledge import RagRetrievalRecord
from app.schemas.knowledge import RetrievedChunk


class VectorSearchRepository(Protocol):
    def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        similarity_threshold: float,
    ) -> list[RetrievedChunk]: ...


class RagRetrievalRepository(Protocol):
    def create_many(
        self,
        *,
        agent_run_id: UUID,
        lead_id: UUID,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> list[RagRetrievalRecord]: ...


class SupabaseRagRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        similarity_threshold: float,
    ) -> list[RetrievedChunk]:
        try:
            response = self._client.rpc(
                "match_knowledge_chunks",
                {
                    "query_embedding": query_embedding,
                    "match_threshold": similarity_threshold,
                    "match_count": top_k,
                },
            ).execute()
        except Exception as exc:
            raise VectorSearchError("pgvector knowledge search failed") from exc
        if not isinstance(response.data, list):
            raise VectorSearchError("pgvector knowledge search returned invalid data")
        try:
            return [RetrievedChunk.model_validate(row) for row in response.data]
        except Exception as exc:
            raise VectorSearchError("pgvector knowledge result was invalid") from exc

    def create_many(
        self,
        *,
        agent_run_id: UUID,
        lead_id: UUID,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> list[RagRetrievalRecord]:
        if not chunks:
            return []
        values = [
            {
                "agent_run_id": str(agent_run_id),
                "lead_id": str(lead_id),
                "query": query,
                "chunk_id": str(chunk.chunk_id),
                "similarity": chunk.similarity,
                "rank": rank,
            }
            for rank, chunk in enumerate(chunks, start=1)
        ]
        try:
            response = self._client.table("rag_retrievals").insert(values).execute()
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to persist RAG evidence") from exc
        if not isinstance(response.data, list) or len(response.data) != len(values):
            raise DatabaseUnavailableError("Database returned incomplete RAG evidence")
        return [RagRetrievalRecord.model_validate(row) for row in response.data]
