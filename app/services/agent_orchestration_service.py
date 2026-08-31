import logging
from time import perf_counter
from typing import Literal
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agents.routing import route_to_node, select_route
from app.agents.state import AgentState, AgentStateTransition, AgentStep
from app.core.exceptions import (
    AgentGraphExecutionError,
    AgentRouteInvalidError,
    AgentStateInvalidError,
    DatabaseUnavailableError,
    GTMAgentOSError,
    LLMInvalidResponseError,
    LLMProviderError,
    LLMTimeoutError,
)
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.agent_state_transition_repository import (
    AgentStateTransitionRepository,
)
from app.repositories.lead_repository import LeadRepository
from app.schemas.lead import LeadQualifyRequest
from app.schemas.orchestration import (
    AgentNextAction,
    AgentOrchestrationResponse,
    AgentStatus,
)
from app.schemas.qualification import QualificationResult
from app.services.lead_service import LeadService
from app.services.qualification_service import QualificationService


logger = logging.getLogger(__name__)

NextGraphNode = Literal[
    "qualify_lead",
    "route_by_classification",
    "research_state",
    "nurture_state",
    "stop_state",
    "persist_agent_state",
]


class AgentOrchestrationService:
    def __init__(
        self,
        *,
        lead_repository: LeadRepository,
        agent_run_repository: AgentRunRepository,
        transition_repository: AgentStateTransitionRepository,
        qualification_service: QualificationService,
    ) -> None:
        self._lead_service = LeadService(lead_repository)
        self._agent_run_repository = agent_run_repository
        self._transition_repository = transition_repository
        self._qualification_service = qualification_service
        self._graph = self._build_graph()

    def orchestrate(
        self, payload: LeadQualifyRequest
    ) -> AgentOrchestrationResponse:
        logger.info("agent_graph_started")
        initial_state = AgentState(payload=payload)

        try:
            result = self._graph.invoke(initial_state)
            final_state = AgentState.model_validate(result)
        except GTMAgentOSError:
            raise
        except ValidationError as exc:
            logger.exception("agent_graph_failed", extra={"error_code": "agent_state_invalid"})
            raise AgentStateInvalidError("LangGraph returned an invalid state") from exc
        except Exception as exc:
            logger.exception("agent_graph_failed", extra={"error_code": "agent_graph_error"})
            raise AgentGraphExecutionError("Unexpected LangGraph failure") from exc

        if final_state.error:
            logger.warning(
                "agent_graph_failed",
                extra={
                    "lead_id": self._safe_uuid(final_state.lead_id),
                    "agent_run_id": self._safe_uuid(final_state.agent_run_id),
                    "error_code": final_state.error,
                },
            )
            self._raise_for_error(final_state.error)

        if not self._has_complete_response(final_state):
            raise AgentStateInvalidError("Completed graph state is incomplete")

        logger.info(
            "agent_graph_completed",
            extra={
                "lead_id": str(final_state.lead_id),
                "agent_run_id": str(final_state.agent_run_id),
                "classification": final_state.classification.value,
                "route": final_state.route.value,
            },
        )
        return AgentOrchestrationResponse(
            lead_id=final_state.lead_id,
            agent_run_id=final_state.agent_run_id,
            score=final_state.score,
            classification=final_state.classification,
            route=final_state.route,
            next_action=final_state.next_action,
            status=final_state.status,
        )

    def load_lead_node(self, state: AgentState) -> dict[str, object]:
        self._log_node(AgentStep.LOAD_LEAD)
        lead = None
        try:
            ingestion = self._lead_service.ingest(state.payload)
            lead = ingestion.lead
            run = self._agent_run_repository.create_started(
                lead_id=lead.id,
                agent_type="lead_orchestration",
                model=self._qualification_service.model,
                input_data=state.payload.model_dump(mode="json"),
            )
            transition = self._transition(
                state,
                AgentStep.LOAD_LEAD,
                payload={"status": AgentStatus.STARTED.value},
            )
            return {
                "lead_id": lead.id,
                "lead": lead,
                "agent_run_id": run.id,
                "current_step": AgentStep.LOAD_LEAD,
                "transitions": [*state.transitions, transition],
            }
        except GTMAgentOSError as exc:
            return self._failed_node_update(
                state,
                AgentStep.LOAD_LEAD,
                exc.code,
                lead_id=lead.id if lead else None,
                lead=lead,
            )
        except Exception:
            logger.exception("agent_graph_failed", extra={"node": "load_lead"})
            return self._failed_node_update(
                state,
                AgentStep.LOAD_LEAD,
                AgentGraphExecutionError.code,
                lead_id=lead.id if lead else None,
                lead=lead,
            )

    def qualify_lead_node(self, state: AgentState) -> dict[str, object]:
        self._log_node(AgentStep.QUALIFY_LEAD, state)
        try:
            response = self._qualification_service.qualify(state.payload)
            qualification = QualificationResult(
                score=response.score,
                classification=response.classification,
                reason=response.reason,
                next_action=response.next_action,
            )
            transition = self._transition(
                state,
                AgentStep.QUALIFY_LEAD,
                payload={
                    "score": qualification.score,
                    "classification": qualification.classification.value,
                },
            )
            return {
                "lead_id": response.lead_id,
                "qualification": qualification,
                "classification": qualification.classification,
                "score": qualification.score,
                "reason": qualification.reason,
                "current_step": AgentStep.QUALIFY_LEAD,
                "transitions": [*state.transitions, transition],
            }
        except GTMAgentOSError as exc:
            return self._failed_node_update(
                state, AgentStep.QUALIFY_LEAD, exc.code
            )
        except Exception:
            logger.exception("agent_graph_failed", extra={"node": "qualify_lead"})
            return self._failed_node_update(
                state, AgentStep.QUALIFY_LEAD, AgentGraphExecutionError.code
            )

    def route_by_classification_node(
        self, state: AgentState
    ) -> dict[str, object]:
        self._log_node(AgentStep.ROUTE_BY_CLASSIFICATION, state)
        try:
            if state.classification is None:
                raise AgentStateInvalidError("Classification is missing")
            route = select_route(state.classification)
            route_to_node(route)
            logger.info(
                "agent_route_selected",
                extra={
                    "lead_id": self._safe_uuid(state.lead_id),
                    "agent_run_id": self._safe_uuid(state.agent_run_id),
                    "classification": state.classification.value,
                    "route": route.value,
                },
            )
            transition = self._transition(
                state,
                AgentStep.ROUTE_BY_CLASSIFICATION,
                route=route,
                payload={
                    "classification": state.classification.value,
                    "route": route.value,
                },
            )
            return {
                "route": route,
                "current_step": AgentStep.ROUTE_BY_CLASSIFICATION,
                "transitions": [*state.transitions, transition],
            }
        except GTMAgentOSError as exc:
            return self._failed_node_update(
                state, AgentStep.ROUTE_BY_CLASSIFICATION, exc.code
            )
        except Exception:
            logger.exception(
                "agent_graph_failed", extra={"node": "route_by_classification"}
            )
            return self._failed_node_update(
                state,
                AgentStep.ROUTE_BY_CLASSIFICATION,
                AgentGraphExecutionError.code,
            )

    def research_node(self, state: AgentState) -> dict[str, object]:
        return self._branch_node(
            state,
            step=AgentStep.RESEARCH_STATE,
            next_action=AgentNextAction.RESEARCH_COMPANY,
        )

    def nurture_node(self, state: AgentState) -> dict[str, object]:
        return self._branch_node(
            state,
            step=AgentStep.NURTURE_STATE,
            next_action=AgentNextAction.NURTURE_SEQUENCE,
        )

    def stop_node(self, state: AgentState) -> dict[str, object]:
        return self._branch_node(
            state,
            step=AgentStep.STOP_STATE,
            next_action=AgentNextAction.DISCARD,
        )

    def persist_state_node(self, state: AgentState) -> dict[str, object]:
        self._log_node(AgentStep.PERSIST_AGENT_STATE, state)
        error = state.error
        if not error and not self._has_complete_result(state):
            error = AgentStateInvalidError.code

        final_status = AgentStatus.FAILED if error else AgentStatus.COMPLETED
        persist_transition = self._transition(
            state,
            AgentStep.PERSIST_AGENT_STATE,
            payload={"status": final_status.value, "error": error},
        )
        end_transition = AgentStateTransition(
            from_state=AgentStep.PERSIST_AGENT_STATE,
            to_state=AgentStep.END,
            route=state.route,
            payload={"status": final_status.value, "error": error},
        )
        transitions = [
            *state.transitions,
            persist_transition,
            end_transition,
        ]

        if state.agent_run_id is None or state.lead_id is None:
            return {
                "current_step": AgentStep.END,
                "status": AgentStatus.FAILED,
                "error": error or AgentStateInvalidError.code,
                "transitions": transitions,
            }

        latency_ms = self._latency_ms(state.started_at)
        try:
            persisted = self._transition_repository.create_many(
                agent_run_id=state.agent_run_id,
                lead_id=state.lead_id,
                transitions=transitions,
            )
            logger.info(
                "agent_state_persisted",
                extra={
                    "lead_id": str(state.lead_id),
                    "agent_run_id": str(state.agent_run_id),
                    "transition_count": len(persisted),
                },
            )
            if error:
                self._agent_run_repository.mark_failed(
                    state.agent_run_id,
                    error=error,
                    latency_ms=latency_ms,
                )
            else:
                self._agent_run_repository.mark_completed_payload(
                    state.agent_run_id,
                    output=self._run_output(state),
                    latency_ms=latency_ms,
                )
        except GTMAgentOSError as exc:
            self._mark_graph_run_failed(
                state.agent_run_id,
                error_code=exc.code,
                latency_ms=latency_ms,
            )
            return {
                "current_step": AgentStep.END,
                "status": AgentStatus.FAILED,
                "error": exc.code,
                "transitions": transitions,
            }
        except Exception:
            logger.exception(
                "agent_graph_failed", extra={"node": "persist_agent_state"}
            )
            self._mark_graph_run_failed(
                state.agent_run_id,
                error_code=AgentGraphExecutionError.code,
                latency_ms=latency_ms,
            )
            return {
                "current_step": AgentStep.END,
                "status": AgentStatus.FAILED,
                "error": AgentGraphExecutionError.code,
                "transitions": transitions,
            }

        return {
            "current_step": AgentStep.END,
            "status": final_status,
            "error": error,
            "transitions": transitions,
        }

    def _branch_node(
        self,
        state: AgentState,
        *,
        step: AgentStep,
        next_action: AgentNextAction,
    ) -> dict[str, object]:
        self._log_node(step, state)
        transition = self._transition(
            state,
            step,
            route=state.route,
            payload={"next_action": next_action.value},
        )
        return {
            "next_action": next_action,
            "current_step": step,
            "transitions": [*state.transitions, transition],
        }

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("load_lead", self.load_lead_node)
        builder.add_node("qualify_lead", self.qualify_lead_node)
        builder.add_node(
            "route_by_classification", self.route_by_classification_node
        )
        builder.add_node("research_state", self.research_node)
        builder.add_node("nurture_state", self.nurture_node)
        builder.add_node("stop_state", self.stop_node)
        builder.add_node("persist_agent_state", self.persist_state_node)

        builder.add_edge(START, "load_lead")
        builder.add_conditional_edges("load_lead", self._after_load)
        builder.add_conditional_edges("qualify_lead", self._after_qualification)
        builder.add_conditional_edges(
            "route_by_classification", self._after_routing
        )
        builder.add_edge("research_state", "persist_agent_state")
        builder.add_edge("nurture_state", "persist_agent_state")
        builder.add_edge("stop_state", "persist_agent_state")
        builder.add_edge("persist_agent_state", END)
        return builder.compile()

    @staticmethod
    def _after_load(state: AgentState) -> NextGraphNode:
        return "persist_agent_state" if state.error else "qualify_lead"

    @staticmethod
    def _after_qualification(state: AgentState) -> NextGraphNode:
        return (
            "persist_agent_state"
            if state.error
            else "route_by_classification"
        )

    @staticmethod
    def _after_routing(state: AgentState) -> NextGraphNode:
        if state.error:
            return "persist_agent_state"
        if state.route is None:
            raise AgentRouteInvalidError("Agent route is missing")
        return route_to_node(state.route)

    @staticmethod
    def _transition(
        state: AgentState,
        to_state: AgentStep,
        *,
        route=None,
        payload: dict[str, object] | None = None,
    ) -> AgentStateTransition:
        return AgentStateTransition(
            from_state=state.current_step,
            to_state=to_state,
            route=route if route is not None else state.route,
            payload=payload or {},
        )

    @classmethod
    def _failed_node_update(
        cls,
        state: AgentState,
        step: AgentStep,
        error_code: str,
        *,
        lead_id=None,
        lead=None,
    ) -> dict[str, object]:
        transition = cls._transition(
            state,
            step,
            payload={"status": AgentStatus.FAILED.value, "error": error_code},
        )
        update: dict[str, object] = {
            "current_step": step,
            "status": AgentStatus.FAILED,
            "error": error_code,
            "transitions": [*state.transitions, transition],
        }
        if lead_id is not None:
            update["lead_id"] = lead_id
        if lead is not None:
            update["lead"] = lead
        return update

    def _mark_graph_run_failed(
        self, run_id: UUID, *, error_code: str, latency_ms: int
    ) -> None:
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
    def _run_output(state: AgentState) -> dict[str, object]:
        return {
            "score": state.score,
            "classification": state.classification.value,
            "reason": state.reason,
            "route": state.route.value,
            "next_action": state.next_action.value,
            "status": AgentStatus.COMPLETED.value,
        }

    @staticmethod
    def _has_complete_result(state: AgentState) -> bool:
        return all(
            value is not None
            for value in (
                state.lead_id,
                state.agent_run_id,
                state.qualification,
                state.classification,
                state.score,
                state.reason,
                state.route,
                state.next_action,
            )
        )

    @classmethod
    def _has_complete_response(cls, state: AgentState) -> bool:
        return (
            state.status == AgentStatus.COMPLETED
            and cls._has_complete_result(state)
        )

    @staticmethod
    def _raise_for_error(error_code: str) -> None:
        error_types: dict[str, type[GTMAgentOSError]] = {
            DatabaseUnavailableError.code: DatabaseUnavailableError,
            LLMTimeoutError.code: LLMTimeoutError,
            LLMInvalidResponseError.code: LLMInvalidResponseError,
            LLMProviderError.code: LLMProviderError,
            AgentStateInvalidError.code: AgentStateInvalidError,
            AgentRouteInvalidError.code: AgentRouteInvalidError,
            AgentGraphExecutionError.code: AgentGraphExecutionError,
        }
        error_type = error_types.get(error_code, AgentGraphExecutionError)
        raise error_type()

    @staticmethod
    def _latency_ms(started_at: float) -> int:
        return max(0, round((perf_counter() - started_at) * 1000))

    @staticmethod
    def _safe_uuid(value: UUID | None) -> str | None:
        return str(value) if value else None

    @staticmethod
    def _log_node(step: AgentStep, state: AgentState | None = None) -> None:
        extra: dict[str, object] = {"node": step.value}
        if state:
            extra.update(
                {
                    "lead_id": AgentOrchestrationService._safe_uuid(
                        state.lead_id
                    ),
                    "agent_run_id": AgentOrchestrationService._safe_uuid(
                        state.agent_run_id
                    ),
                }
            )
        logger.info("agent_node_entered", extra=extra)
