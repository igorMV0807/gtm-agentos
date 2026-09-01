import logging

from app.core.exceptions import (
    EmbeddingInvalidResponseError,
    GTMAgentOSError,
    KnowledgeIngestionError,
)
from app.models.knowledge import KnowledgeChunkCreate
from app.repositories.knowledge_repository import KnowledgeRepository
from app.schemas.knowledge import (
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentCreateResponse,
)
from app.services.chunking_service import TextChunker
from app.services.embedding_service import EmbeddingProvider


logger = logging.getLogger(__name__)


class KnowledgeIngestionService:
    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        embedding_provider: EmbeddingProvider,
        chunker: TextChunker,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._chunker = chunker

    def ingest(
        self, payload: KnowledgeDocumentCreateRequest
    ) -> KnowledgeDocumentCreateResponse:
        logger.info(
            "knowledge_ingestion_started",
            extra={"document_type": payload.document_type},
        )
        document = self._repository.create_document(payload)
        try:
            contents = self._chunker.chunk(payload.content)
            if not contents:
                raise EmbeddingInvalidResponseError("Document produced no chunks")
            embeddings = self._embedding_provider.embed_batch(
                contents,
                input_type="document",
            )
            if len(embeddings) != len(contents):
                raise EmbeddingInvalidResponseError(
                    "Embedding count did not match chunk count"
                )

            chunk_metadata = {
                **payload.metadata,
                "document_title": payload.title,
                "document_type": payload.document_type,
                "source": payload.source,
            }
            chunks = [
                KnowledgeChunkCreate(
                    content=content,
                    chunk_index=index,
                    metadata=chunk_metadata,
                    embedding=embedding,
                )
                for index, (content, embedding) in enumerate(
                    zip(contents, embeddings, strict=True)
                )
            ]
            persisted = self._repository.create_chunks(document.id, chunks)
            if len(persisted) != len(chunks):
                raise EmbeddingInvalidResponseError(
                    "Persisted chunk count did not match input"
                )
        except Exception as exc:
            try:
                self._repository.delete_document(document.id)
            except GTMAgentOSError:
                logger.exception(
                    "knowledge_ingestion_rollback_failed",
                    extra={"document_id": str(document.id)},
                )
            logger.exception(
                "rag_failed",
                extra={"operation": "knowledge_ingestion"},
            )
            if isinstance(exc, GTMAgentOSError):
                raise
            raise KnowledgeIngestionError(
                "Unexpected knowledge ingestion failure"
            ) from exc

        logger.info(
            "knowledge_ingestion_completed",
            extra={
                "document_id": str(document.id),
                "chunk_count": len(persisted),
                "embedding_provider": self._embedding_provider.provider_name,
                "embedding_model": self._embedding_provider.model,
            },
        )
        return KnowledgeDocumentCreateResponse(
            document_id=document.id,
            chunks_created=len(persisted),
            embedding_provider=self._embedding_provider.provider_name,
            embedding_model=self._embedding_provider.model,
        )
