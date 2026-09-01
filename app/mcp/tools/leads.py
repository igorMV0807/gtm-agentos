from app.core.exceptions import LeadNotFoundError
from app.mcp.schemas import (
    GetLeadHistoryInput,
    GetLeadHistoryOutput,
    GetLeadInput,
    GetLeadOutput,
    SearchLeadsInput,
    SearchLeadsOutput,
    StateTransitionSummary,
)
from app.mcp.tools.common import safe_agent_run, safe_lead
from app.repositories.mcp_repository import MCPDataRepository


class LeadTools:
    def __init__(self, repository: MCPDataRepository) -> None:
        self._repository = repository

    def get_lead(self, payload: GetLeadInput) -> GetLeadOutput:
        lead = self._repository.get_lead(payload.lead_id)
        if lead is None:
            raise LeadNotFoundError("Lead does not exist")
        return GetLeadOutput(lead=safe_lead(lead))

    def search_leads(self, payload: SearchLeadsInput) -> SearchLeadsOutput:
        leads = self._repository.search_leads(
            classification=payload.classification,
            industry=payload.industry,
            country=payload.country,
            company=payload.company,
            limit=payload.limit,
        )
        safe = [safe_lead(lead) for lead in leads[: payload.limit]]
        return SearchLeadsOutput(leads=safe, count=len(safe))

    def get_lead_history(
        self, payload: GetLeadHistoryInput
    ) -> GetLeadHistoryOutput:
        lead = self._repository.get_lead(payload.lead_id)
        if lead is None:
            raise LeadNotFoundError("Lead does not exist")
        runs = self._repository.get_lead_runs(payload.lead_id)
        transitions = self._repository.get_lead_transitions(payload.lead_id)
        qualification_runs = [
            safe_agent_run(run)
            for run in runs
            if run.agent_type == "lead_qualification"
        ]
        orchestration_runs = [
            safe_agent_run(run)
            for run in runs
            if run.agent_type == "lead_orchestration"
        ]
        safe_transitions = [
            StateTransitionSummary(
                from_state=transition.from_state,
                to_state=transition.to_state,
                route=transition.route,
                status=(
                    transition.payload.get("status")
                    if isinstance(transition.payload.get("status"), str)
                    else None
                ),
                created_at=transition.created_at,
            )
            for transition in transitions
        ]
        return GetLeadHistoryOutput(
            lead_id=lead.id,
            qualification_runs=qualification_runs,
            orchestration_runs=orchestration_runs,
            transitions=safe_transitions,
        )
