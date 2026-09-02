begin;

create table if not exists public.ai_usage_events (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid references public.leads(id) on delete set null,
  agent_run_id uuid references public.agent_runs(id) on delete set null,
  provider text not null check (
    char_length(provider) between 1 and 40
    and provider ~ '^[a-z][a-z0-9_-]*$'
  ),
  model text not null check (char_length(model) between 1 and 120),
  operation text not null check (
    operation in (
      'qualification',
      'research_context',
      'email_draft',
      'embedding_document',
      'embedding_query'
    )
  ),
  input_tokens integer check (input_tokens is null or input_tokens >= 0),
  output_tokens integer check (output_tokens is null or output_tokens >= 0),
  total_tokens integer check (total_tokens is null or total_tokens >= 0),
  estimated_cost_usd numeric(14, 8) check (
    estimated_cost_usd is null or estimated_cost_usd >= 0
  ),
  latency_ms integer not null check (latency_ms >= 0),
  created_at timestamptz not null default now(),
  constraint ai_usage_events_token_consistency check (
    input_tokens is null
    or output_tokens is null
    or total_tokens is null
    or total_tokens = input_tokens + output_tokens
  )
);

create index if not exists ai_usage_events_created_idx
  on public.ai_usage_events (created_at desc);

create index if not exists ai_usage_events_provider_model_created_idx
  on public.ai_usage_events (provider, model, created_at desc);

create index if not exists ai_usage_events_run_created_idx
  on public.ai_usage_events (agent_run_id, created_at desc)
  where agent_run_id is not null;

create index if not exists ai_usage_events_lead_created_idx
  on public.ai_usage_events (lead_id, created_at desc)
  where lead_id is not null;

alter table public.ai_usage_events enable row level security;

revoke all on table public.ai_usage_events
  from public, anon, authenticated, service_role;

grant select, insert on table public.ai_usage_events to service_role;

create or replace view public.observability_overview
with (security_invoker = true)
as
select
  (select count(*) from public.leads)::bigint as total_leads,
  (select count(*) from public.leads where classification = 'HOT')::bigint
    as hot_leads,
  (select count(*) from public.leads where classification = 'WARM')::bigint
    as warm_leads,
  (select count(*) from public.leads where classification = 'COLD')::bigint
    as cold_leads,
  (select count(*) from public.agent_runs)::bigint as total_agent_runs,
  (select count(*) from public.agent_runs where status = 'completed')::bigint
    as completed_runs,
  (select count(*) from public.agent_runs where status = 'failed')::bigint
    as failed_runs,
  coalesce(
    (
      select count(*) filter (where status = 'completed')::double precision
        / nullif(count(*), 0)
      from public.agent_runs
    ),
    0.0
  ) as success_rate,
  coalesce(
    (select avg(latency_ms)::double precision from public.agent_runs),
    0.0
  ) as average_agent_latency_ms,
  (select count(*) from public.rag_retrievals)::bigint as total_retrievals,
  coalesce(
    (select avg(similarity)::double precision from public.rag_retrievals),
    0.0
  ) as average_similarity,
  (
    select count(*)
    from public.agent_state_transitions
    where to_state = 'build_research_context'
      and payload @> '{"fallback": true}'::jsonb
  )::bigint as no_context_count,
  (select count(*) from public.tool_calls)::bigint as total_tool_calls,
  (select count(*) from public.tool_calls where status = 'completed')::bigint
    as completed_tool_calls,
  (select count(*) from public.tool_calls where status = 'failed')::bigint
    as failed_tool_calls,
  (select count(*) from public.tool_calls where status = 'rejected')::bigint
    as rejected_tool_calls,
  coalesce(
    (select avg(latency_ms)::double precision from public.tool_calls),
    0.0
  ) as average_tool_latency_ms,
  (select count(*) from public.external_actions where status = 'pending')::bigint
    as pending_actions,
  (select count(*) from public.external_actions where status = 'approved')::bigint
    as approved_actions,
  (select count(*) from public.external_actions where status = 'completed')::bigint
    as completed_actions,
  (select count(*) from public.external_actions where status = 'failed')::bigint
    as failed_actions,
  (select count(*) from public.external_actions where status = 'rejected')::bigint
    as rejected_actions,
  (
    select count(*)
    from public.external_actions
    where status = 'pending' and requires_approval
  )::bigint as actions_waiting_approval,
  (select count(*) from public.ai_usage_events)::bigint as ai_usage_events,
  coalesce((select sum(total_tokens) from public.ai_usage_events), 0)::bigint
    as ai_total_tokens,
  coalesce(
    (select sum(estimated_cost_usd) from public.ai_usage_events),
    0::numeric
  )::numeric(16, 8) as ai_estimated_cost_usd,
  coalesce(
    (select avg(latency_ms)::double precision from public.ai_usage_events),
    0.0
  ) as average_ai_latency_ms;

create or replace view public.ai_usage_summary
with (security_invoker = true)
as
select
  count(*)::bigint as events,
  coalesce(sum(total_tokens), 0)::bigint as total_tokens,
  coalesce(sum(estimated_cost_usd), 0::numeric)::numeric(16, 8)
    as estimated_cost_usd,
  coalesce(avg(latency_ms)::double precision, 0.0) as average_latency_ms
from public.ai_usage_events;

revoke all on table public.observability_overview
  from public, anon, authenticated, service_role;
revoke all on table public.ai_usage_summary
  from public, anon, authenticated, service_role;

grant select on table public.observability_overview to service_role;
grant select on table public.ai_usage_summary to service_role;

comment on table public.ai_usage_events is
  'Phase 6 provider-reported AI usage and explicitly estimated cost events.';
comment on view public.observability_overview is
  'Service-role-only aggregate metrics for the Phase 6 operator console.';
comment on view public.ai_usage_summary is
  'Service-role-only aggregate AI usage and estimated cost totals.';

commit;
