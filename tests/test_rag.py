from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_knowledge_ingestion_service
from app.core.exceptions import (
    DatabaseUnavailableError,
    EmbeddingInvalidResponseError,
    EmbeddingProviderError,
    LLMProviderError,
)
from app.main import app
from app.models.knowledge import (
    KnowledgeChunkCreate,
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)
from app.schemas.knowledge import KnowledgeDocumentCreateRequest, RetrievedChunk
from app.schemas.qualification import (
    LeadClassification,
    NextAction,
    QualificationResult,
)
from app.services.chunking_service import TextChunker
from app.services.embedding_service import VoyageEmbeddingProvider
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.retrieval_service import RetrievalService
from tests.conftest import ScenarioContext


class FakeEmbeddingProvider:
    provider_name = "voyage"
    model = "voyage-test"
    dimension = 3

    def __init__(self) -> None:
        self.batch_calls: list[tuple[list[str], str]] = []
        self.text_calls: list[tuple[str, str]] = []
        self.error: Exception | None = None

    def embed_batch(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if self.error:
            raise self.error
        self.batch_calls.append((texts, input_type))
        return [[float(index), 0.5, 1.0] for index, _ in enumerate(texts)]

    def embed_text(self, text: str, *, input_type: str) -> list[float]:
        if self.error:
            raise self.error
        self.text_calls.append((text, input_type))
        return [0.1, 0.2, 0.3]


class InMemoryKnowledgeRepository:
    def __init__(self) -> None:
        self.document: KnowledgeDocumentRecord | None = None
        self.chunks: list[KnowledgeChunkCreate] = []
        self.deleted: list[UUID] = []

    def create_document(
        self, document: KnowledgeDocumentCreateRequest
    ) -> KnowledgeDocumentRecord:
        self.document = KnowledgeDocumentRecord(
            id=uuid4(),
            title=document.title,
            document_type=document.document_type,
            source=document.source,
            metadata=document.metadata,
        )
        return self.document

    def create_chunks(
        self, document_id: UUID, chunks: list[KnowledgeChunkCreate]
    ) -> list[KnowledgeChunkRecord]:
        self.chunks = chunks
        return [
            KnowledgeChunkRecord(
                id=uuid4(),
                document_id=document_id,
                content=chunk.content,
                chunk_index=chunk.chunk_index,
                metadata=chunk.metadata,
            )
            for chunk in chunks
        ]

    def delete_document(self, document_id: UUID) -> None:
        self.deleted.append(document_id)


class FakeVectorSearchRepository:
    def __init__(self, results: list[RetrievedChunk]) -> None:
        self.results = results
        self.calls: list[tuple[list[float], int, float]] = []

    def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        similarity_threshold: float,
    ) -> list[RetrievedChunk]:
        self.calls.append((query_embedding, top_k, similarity_threshold))
        return self.results


def _ingestion_service() -> tuple[
    KnowledgeIngestionService,
    InMemoryKnowledgeRepository,
    FakeEmbeddingProvider,
]:
    repository = InMemoryKnowledgeRepository()
    embeddings = FakeEmbeddingProvider()
    service = KnowledgeIngestionService(
        repository=repository,
        embedding_provider=embeddings,
        chunker=TextChunker(chunk_size_words=20, overlap_words=4),
    )
    return service, repository, embeddings


def _set_classification(
    context: ScenarioContext, classification: LeadClassification
) -> None:
    values = {
        LeadClassification.HOT: (90, NextAction.PERSONALIZED_OUTREACH),
        LeadClassification.WARM: (60, NextAction.NURTURE),
        LeadClassification.COLD: (20, NextAction.DISCARD),
    }
    score, next_action = values[classification]
    context.llm.result = QualificationResult(
        score=score,
        classification=classification,
        reason=f"Deterministic {classification.value} result.",
        next_action=next_action,
    )


def _chunk(similarity: float, title: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=uuid4(),
        chunk_id=uuid4(),
        title=title,
        content=f"Grounded content for {title}.",
        similarity=similarity,
        metadata={"document_type": "playbook"},
    )


def test_document_ingestion_endpoint() -> None:
    service, repository, embeddings = _ingestion_service()
    app.dependency_overrides[get_knowledge_ingestion_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/knowledge/documents",
                json={
                    "title": "ICP",
                    "document_type": "icp",
                    "content": " ".join(f"word{index}" for index in range(45)),
                    "metadata": {"portfolio": True},
                },
            )
    finally:
        app.dependency_overrides.pop(get_knowledge_ingestion_service, None)

    assert response.status_code == 201
    assert response.json()["chunks_created"] == 3
    assert response.json()["embedding_provider"] == "voyage"
    assert repository.document is not None
    assert embeddings.batch_calls[0][1] == "document"


def test_chunking_is_deterministic_and_overlapping() -> None:
    chunker = TextChunker(chunk_size_words=20, overlap_words=5)
    content = " ".join(f"w{index}" for index in range(41))

    first = chunker.chunk(content)
    second = chunker.chunk(content)

    assert first == second
    assert len(first) == 3
    assert first[0].split()[-5:] == first[1].split()[:5]
    assert all(chunk.strip() for chunk in first)


def test_ingestion_stores_embeddings_and_chunk_metadata() -> None:
    service, repository, _ = _ingestion_service()
    response = service.ingest(
        KnowledgeDocumentCreateRequest(
            title="Sales Playbook",
            document_type="sales_playbook",
            content=" ".join(f"word{index}" for index in range(30)),
            source="demo_knowledge/sales_playbook.md",
            metadata={"audience": "sales"},
        )
    )

    assert response.chunks_created == 2
    assert repository.chunks[0].embedding == [0.0, 0.5, 1.0]
    assert repository.chunks[1].embedding == [1.0, 0.5, 1.0]
    assert repository.chunks[0].metadata["document_type"] == "sales_playbook"
    assert repository.chunks[0].metadata["audience"] == "sales"


def test_ingestion_rolls_back_document_on_embedding_failure() -> None:
    service, repository, embeddings = _ingestion_service()
    embeddings.error = EmbeddingProviderError("provider unavailable")

    with pytest.raises(EmbeddingProviderError):
        service.ingest(
            KnowledgeDocumentCreateRequest(
                title="ICP",
                document_type="icp",
                content="Approved internal knowledge.",
            )
        )

    assert repository.document is not None
    assert repository.deleted == [repository.document.id]


def test_retrieval_returns_sorted_relevant_top_k() -> None:
    embeddings = FakeEmbeddingProvider()
    repository = FakeVectorSearchRepository(
        [_chunk(0.71, "B"), _chunk(0.95, "A"), _chunk(0.40, "Ignored")]
    )
    service = RetrievalService(
        embedding_provider=embeddings,
        repository=repository,
        top_k=2,
        similarity_threshold=0.65,
    )

    results = service.search("qualified sales lead")

    assert [item.title for item in results] == ["A", "B"]
    assert embeddings.text_calls == [("qualified sales lead", "query")]
    assert repository.calls[0][1:] == (2, 0.65)


def test_hot_executes_retrieval(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert len(context.retrieval.calls) == 1
    assert "Head of Sales" in context.retrieval.calls[0]
    assert response.json()["research_context"] == context.llm.research_context


def test_warm_does_not_execute_retrieval(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    _set_classification(context, LeadClassification.WARM)

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert context.retrieval.calls == []
    assert context.llm.research_calls == 0
    assert "research_context" not in response.json()


def test_cold_does_not_execute_retrieval(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    _set_classification(context, LeadClassification.COLD)

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert context.retrieval.calls == []
    assert context.llm.research_calls == 0


def test_research_context_uses_retrieved_chunks(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.retrieval.results = [_chunk(0.92, "ICP"), _chunk(0.86, "Objections")]

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert context.llm.research_chunks == context.retrieval.results
    assert [source["title"] for source in response.json()["sources"]] == [
        "ICP",
        "Objections",
    ]


def test_no_context_uses_controlled_fallback_without_claude(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.retrieval.results = []

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert response.json()["research_context"] == "insufficient_internal_knowledge"
    assert response.json()["sources"] == []
    assert context.llm.research_calls == 0


def test_retrieval_evidence_is_persisted(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.retrieval.results = [_chunk(0.93, "ICP"), _chunk(0.82, "Playbook")]

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 200
    assert [record.rank for record in context.rag_evidence.retrievals] == [1, 2]
    assert {str(record.agent_run_id) for record in context.rag_evidence.retrievals} == {
        response.json()["agent_run_id"]
    }
    assert all(record.query == context.retrieval.calls[0] for record in context.rag_evidence.retrievals)


def test_evidence_failure_marks_run_and_transitions_failed(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.rag_evidence.error = DatabaseUnavailableError("database detail")

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 503
    graph_run = next(
        run
        for run in context.runs.runs.values()
        if run.agent_type == "lead_orchestration"
    )
    assert graph_run.status == "failed"
    assert context.transitions.transitions[-1].payload == {
        "status": "failed",
        "error": "database_unavailable",
    }


def test_embedding_provider_failure_is_safe(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.retrieval.error = EmbeddingProviderError("secret provider detail")

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "embedding_provider_error"
    assert "secret provider detail" not in response.text


def test_research_provider_failure_is_safe(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    context.llm.research_error = LLMProviderError("private Claude detail")

    response = context.client.post("/api/v1/leads/agent", json=valid_payload)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_provider_error"
    assert "private Claude detail" not in response.text


def test_existing_endpoint_contracts_remain_backward_compatible(
    context: ScenarioContext, valid_payload: dict[str, object]
) -> None:
    _set_classification(context, LeadClassification.WARM)
    agent = context.client.post("/api/v1/leads/agent", json=valid_payload)
    qualification = context.client.post("/api/v1/leads/qualify", json=valid_payload)

    assert set(agent.json()) == {
        "lead_id",
        "agent_run_id",
        "score",
        "classification",
        "route",
        "next_action",
        "status",
    }
    assert set(qualification.json()) == {
        "lead_id",
        "score",
        "classification",
        "reason",
        "next_action",
    }


def test_voyage_adapter_uses_document_input_without_paid_api() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = VoyageEmbeddingProvider(
        api_key="test-key",
        model="voyage-4",
        dimension=3,
        timeout_seconds=1,
        client=client,
    )

    embeddings = provider.embed_batch(["one", "two"], input_type="document")

    assert embeddings == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert '"input_type":"document"' in str(captured["body"])
    assert '"output_dimension":3' in str(captured["body"])
    assert captured["authorization"] == "Bearer test-key"


def test_voyage_adapter_rejects_wrong_embedding_dimension() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
            )
        )
    )
    provider = VoyageEmbeddingProvider(
        api_key="test-key",
        model="voyage-4",
        dimension=3,
        timeout_seconds=1,
        client=client,
    )

    with pytest.raises(EmbeddingInvalidResponseError):
        provider.embed_text("query", input_type="query")
