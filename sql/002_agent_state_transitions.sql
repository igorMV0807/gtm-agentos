begin;

create table if not exists public.agent_state_transitions (
  id uuid primary key default gen_random_uuid(),
  agent_run_id uuid not null
    references public.agent_runs(id) on delete cascade,
  lead_id uuid not null
    references public.leads(id) on delete cascade,
  from_state text not null check (
    from_state in (
      'START',
      'load_lead',
      'qualify_lead',
      'route_by_classification',
      'research_state',
      'nurture_state',
      'stop_state',
      'persist_agent_state',
      'END'
    )
  ),
  to_state text not null check (
    to_state in (
      'START',
      'load_lead',
      'qualify_lead',
      'route_by_classification',
      'research_state',
      'nurture_state',
      'stop_state',
      'persist_agent_state',
      'END'
    )
  ),
  route text check (route is null or route in ('research', 'nurture', 'stop')),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists agent_state_transitions_run_created_idx
  on public.agent_state_transitions (agent_run_id, created_at);

create index if not exists agent_state_transitions_lead_created_idx
  on public.agent_state_transitions (lead_id, created_at desc);

alter table public.agent_state_transitions enable row level security;

revoke all on table public.agent_state_transitions
  from public, anon, authenticated, service_role;

grant select, insert on table public.agent_state_transitions to service_role;

comment on table public.agent_state_transitions is
  'Immutable Phase 2 audit trail for LangGraph state transitions.';

commit;
