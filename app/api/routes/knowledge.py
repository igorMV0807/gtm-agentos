from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_knowledge_ingestion_service
from app.schemas.knowledge import (
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentCreateResponse,
)
from app.services.knowledge_ingestion_service import KnowledgeIngestionService


router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


@router.post(
    "/documents",
    response_model=KnowledgeDocumentCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest an internal GTM knowledge document",
)
def ingest_knowledge_document(
    payload: KnowledgeDocumentCreateRequest,
    service: Annotated[
        KnowledgeIngestionService,
        Depends(get_knowledge_ingestion_service),
    ],
) -> KnowledgeDocumentCreateResponse:
    return service.ingest(payload)
