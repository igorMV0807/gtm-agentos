from functools import lru_cache

from fastapi import Depends, Header, Request
from supabase import Client, create_client

from app.core.ai_pricing import AIPricingCatalog
from app.core.config import Settings, get_settings
from app.core.exceptions import DatabaseUnavailableError, OperatorAuthenticationError
from app.core.operator_auth import (
    OPERATOR_SESSION_COOKIE,
    valid_operator_key,
    valid_operator_session,
)
from app.repositories.agent_run_repository import (
    AgentRunRepository,
    SupabaseAgentRunRepository,
)
from app.repositories.agent_state_transition_repository import (
    AgentStateTransitionRepository,
    SupabaseAgentStateTransitionRepository,
)
from app.repositories.lead_repository import LeadRepository, SupabaseLeadRepository
from app.repositories.knowledge_repository import (
    KnowledgeRepository,
    SupabaseKnowledgeRepository,
)
from app.mcp.execution import ToolExecutionService
from app.mcp.registry import ToolRegistry, build_tool_registry
from app.repositories.mcp_repository import MCPDataRepository, SupabaseMCPDataRepository
from app.repositories.rag_repository import SupabaseRagRepository
from app.repositories.tool_call_repository import (
    SupabaseToolCallRepository,
    ToolCallRepository,
)
from app.integrations.n8n import N8nActionService, WebhookSigner
from app.repositories.external_action_repository import (
    ExternalActionRepository,
    SupabaseExternalActionRepository,
)
from app.services.agent_orchestration_service import AgentOrchestrationService
from app.services.chunking_service import TextChunker
from app.services.embedding_service import EmbeddingProvider, build_embedding_provider
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.llm_service import LLMService, build_llm_service
from app.services.qualification_service import QualificationService
from app.services.retrieval_service import RetrievalService
from app.services.external_action_service import ExternalActionService
from app.repositories.ai_usage_repository import (
    AIUsageRepository,
    SupabaseAIUsageRepository,
)
from app.repositories.demo_observability_repository import (
    DemoObservabilityRepository,
)
from app.repositories.observability_repository import (
    ObservabilityRepository,
    SupabaseObservabilityRepository,
)
from app.services.ai_usage_service import AIUsageService
from app.services.observability_service import ObservabilityService


def require_operator(
    request: Request,
    settings: Settings = Depends(get_settings),
    x_operator_key: str | None = Header(default=None, alias="X-Operator-Key"),
) -> None:
    if valid_operator_key(x_operator_key, settings):
        return
    if valid_operator_session(
        request.cookies.get(OPERATOR_SESSION_COOKIE), settings
    ):
        return
    raise OperatorAuthenticationError()


@lru_cache
def get_supabase_client() -> Client:
    url, key = get_settings().require_database()
    try:
        return create_client(url, key)
    except Exception as exc:
        raise DatabaseUnavailableError("Could not initialize Supabase client") from exc


@lru_cache
def get_lead_repository() -> LeadRepository:
    return SupabaseLeadRepository(get_supabase_client())


@lru_cache
def get_agent_run_repository() -> AgentRunRepository:
    return SupabaseAgentRunRepository(get_supabase_client())


@lru_cache
def get_agent_state_transition_repository() -> AgentStateTransitionRepository:
    return SupabaseAgentStateTransitionRepository(get_supabase_client())


@lru_cache
def get_knowledge_repository() -> KnowledgeRepository:
    return SupabaseKnowledgeRepository(get_supabase_client())


@lru_cache
def get_rag_repository() -> SupabaseRagRepository:
    return SupabaseRagRepository(get_supabase_client())


@lru_cache
def get_mcp_data_repository() -> MCPDataRepository:
    return SupabaseMCPDataRepository(get_supabase_client())


@lru_cache
def get_tool_call_repository() -> ToolCallRepository:
    return SupabaseToolCallRepository(get_supabase_client())


@lru_cache
def get_external_action_repository() -> ExternalActionRepository:
    return SupabaseExternalActionRepository(get_supabase_client())


@lru_cache
def get_ai_usage_repository() -> AIUsageRepository:
    return SupabaseAIUsageRepository(get_supabase_client())


@lru_cache
def get_ai_usage_service() -> AIUsageService:
    settings = get_settings()
    return AIUsageService(
        repository=get_ai_usage_repository(),
        pricing=AIPricingCatalog.from_json(settings.ai_pricing_json),
    )


@lru_cache
def get_observability_repository() -> ObservabilityRepository:
    if get_settings().portfolio_mode:
        return DemoObservabilityRepository()
    return SupabaseObservabilityRepository(get_supabase_client())


@lru_cache
def get_observability_service() -> ObservabilityService:
    return ObservabilityService(
        get_observability_repository(),
        demo_mode=get_settings().portfolio_mode,
    )


@lru_cache
def get_llm_service() -> LLMService:
    return build_llm_service(get_settings(), usage_tracker=get_ai_usage_service())


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    return build_embedding_provider(
        get_settings(), usage_tracker=get_ai_usage_service()
    )


@lru_cache
def get_retrieval_service() -> RetrievalService:
    settings = get_settings()
    return RetrievalService(
        embedding_provider=get_embedding_provider(),
        repository=get_rag_repository(),
        top_k=settings.rag_top_k,
        similarity_threshold=settings.rag_similarity_threshold,
    )


@lru_cache
def get_knowledge_ingestion_service() -> KnowledgeIngestionService:
    settings = get_settings()
    return KnowledgeIngestionService(
        repository=get_knowledge_repository(),
        embedding_provider=get_embedding_provider(),
        chunker=TextChunker(
            chunk_size_words=settings.rag_chunk_size_words,
            overlap_words=settings.rag_chunk_overlap_words,
        ),
    )


@lru_cache
def get_tool_registry() -> ToolRegistry:
    return build_tool_registry(
        repository=get_mcp_data_repository(),
        retrieval_service=get_retrieval_service(),
    )


@lru_cache
def get_tool_execution_service() -> ToolExecutionService:
    return ToolExecutionService(
        registry=get_tool_registry(),
        audit_repository=get_tool_call_repository(),
    )


@lru_cache
def get_webhook_signer() -> WebhookSigner:
    settings = get_settings()
    _, secret = settings.require_n8n()
    return WebhookSigner(
        secret,
        max_age_seconds=settings.n8n_signature_max_age_seconds,
    )


@lru_cache
def get_n8n_action_service() -> N8nActionService:
    settings = get_settings()
    webhook_url, _ = settings.require_n8n()
    return N8nActionService(
        webhook_url=webhook_url,
        signer=get_webhook_signer(),
        timeout_seconds=settings.n8n_timeout_seconds,
    )


@lru_cache
def get_external_action_request_service() -> ExternalActionService:
    return ExternalActionService(repository=get_external_action_repository())


@lru_cache
def get_external_action_service() -> ExternalActionService:
    return ExternalActionService(
        repository=get_external_action_repository(),
        n8n_dispatcher=get_n8n_action_service(),
    )


@lru_cache
def get_qualification_service() -> QualificationService:
    return QualificationService(
        lead_repository=get_lead_repository(),
        agent_run_repository=get_agent_run_repository(),
        llm_service=get_llm_service(),
        ai_usage_service=get_ai_usage_service(),
    )


@lru_cache
def get_agent_orchestration_service() -> AgentOrchestrationService:
    return AgentOrchestrationService(
        lead_repository=get_lead_repository(),
        agent_run_repository=get_agent_run_repository(),
        transition_repository=get_agent_state_transition_repository(),
        qualification_service=get_qualification_service(),
        retrieval_service=get_retrieval_service(),
        rag_retrieval_repository=get_rag_repository(),
        llm_service=get_llm_service(),
        external_action_service=get_external_action_request_service(),
        ai_usage_service=get_ai_usage_service(),
    )
