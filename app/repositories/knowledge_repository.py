from typing import Protocol
from uuid import UUID

from supabase import Client

from app.core.exceptions import DatabaseUnavailableError
from app.models.knowledge import (
    KnowledgeChunkCreate,
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)
from app.schemas.knowledge import KnowledgeDocumentCreateRequest


class KnowledgeRepository(Protocol):
    def create_document(
        self, document: KnowledgeDocumentCreateRequest
    ) -> KnowledgeDocumentRecord: ...

    def create_chunks(
        self, document_id: UUID, chunks: list[KnowledgeChunkCreate]
    ) -> list[KnowledgeChunkRecord]: ...

    def delete_document(self, document_id: UUID) -> None: ...


class SupabaseKnowledgeRepository:
    def __init__(self, client: Client) -> None:
        self._client = client

    def create_document(
        self, document: KnowledgeDocumentCreateRequest
    ) -> KnowledgeDocumentRecord:
        values = document.model_dump(exclude={"content"}, mode="json")
        try:
            response = (
                self._client.table("knowledge_documents").insert(values).execute()
            )
            return self._one_document(response.data)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to create knowledge document") from exc

    def create_chunks(
        self, document_id: UUID, chunks: list[KnowledgeChunkCreate]
    ) -> list[KnowledgeChunkRecord]:
        if not chunks:
            return []
        values = [
            {
                "document_id": str(document_id),
                **chunk.model_dump(mode="json"),
            }
            for chunk in chunks
        ]
        try:
            response = (
                self._client.table("knowledge_chunks").insert(values).execute()
            )
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to create knowledge chunks") from exc
        if not isinstance(response.data, list) or len(response.data) != len(values):
            raise DatabaseUnavailableError("Database returned incomplete knowledge chunks")
        return [KnowledgeChunkRecord.model_validate(row) for row in response.data]

    def delete_document(self, document_id: UUID) -> None:
        try:
            self._client.table("knowledge_documents").delete().eq(
                "id", str(document_id)
            ).execute()
        except Exception as exc:
            raise DatabaseUnavailableError("Failed to roll back knowledge document") from exc

    @staticmethod
    def _one_document(data: object) -> KnowledgeDocumentRecord:
        if not isinstance(data, list) or not data:
            raise DatabaseUnavailableError(
                "Database returned no row for knowledge document"
            )
        return KnowledgeDocumentRecord.model_validate(data[0])
