from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings
from app.core.exceptions import DatabaseUnavailableError
from app.repositories.agent_run_repository import (
    AgentRunRepository,
    SupabaseAgentRunRepository,
)
from app.repositories.agent_state_transition_repository import (
    AgentStateTransitionRepository,
    SupabaseAgentStateTransitionRepository,
)
from app.repositories.lead_repository import LeadRepository, SupabaseLeadRepository
from app.services.agent_orchestration_service import AgentOrchestrationService
from app.services.llm_service import LLMService, build_llm_service
from app.services.qualification_service import QualificationService


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
def get_llm_service() -> LLMService:
    return build_llm_service(get_settings())


@lru_cache
def get_qualification_service() -> QualificationService:
    return QualificationService(
        lead_repository=get_lead_repository(),
        agent_run_repository=get_agent_run_repository(),
        llm_service=get_llm_service(),
    )


@lru_cache
def get_agent_orchestration_service() -> AgentOrchestrationService:
    return AgentOrchestrationService(
        lead_repository=get_lead_repository(),
        agent_run_repository=get_agent_run_repository(),
        transition_repository=get_agent_state_transition_repository(),
        qualification_service=get_qualification_service(),
    )
