begin;

create table if not exists public.external_actions (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null
    references public.leads(id) on delete restrict,
  agent_run_id uuid
    references public.agent_runs(id) on delete restrict,
  action_type text not null check (
    action_type in (
      'create_or_update_crm_lead',
      'create_follow_up_task',
      'draft_outreach_email',
      'send_approved_email',
      'mark_lead_status'
    )
  ),
  payload jsonb not null check (
    jsonb_typeof(payload) = 'object'
    and octet_length(payload::text) <= 16384
  ),
  status text not null default 'pending' check (
    status in (
      'pending',
      'approved',
      'executing',
      'completed',
      'failed',
      'rejected'
    )
  ),
  requires_approval boolean not null default true,
  approved_at timestamptz,
  executed_at timestamptz,
  idempotency_key text not null unique check (
    char_length(idempotency_key) between 1 and 200
    and idempotency_key ~ '^[A-Za-z0-9:_-]+$'
  ),
  external_reference text check (
    external_reference is null
    or char_length(external_reference) between 1 and 500
  ),
  result jsonb check (
    result is null
    or (
      jsonb_typeof(result) = 'object'
      and octet_length(result::text) <= 16384
    )
  ),
  error text check (
    error is null
    or char_length(error) between 1 and 500
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint external_actions_required_approval check (
    action_type <> 'send_approved_email'
    or requires_approval
  ),
  constraint external_actions_state_consistency check (
    (status = 'pending' and approved_at is null and executed_at is null)
    or (status = 'approved' and approved_at is not null and executed_at is null)
    or (status in ('executing', 'completed', 'failed')
        and approved_at is not null and executed_at is not null)
    or (status = 'rejected' and executed_at is null)
  )
);

create table if not exists public.external_action_events (
  id uuid primary key default gen_random_uuid(),
  action_id uuid not null
    references public.external_actions(id) on delete restrict,
  event_type text not null check (
    event_type in (
      'action_requested',
      'email_draft_created',
      'approval_granted',
      'action_rejected',
      'execution_started',
      'callback_received',
      'execution_completed',
      'execution_failed'
    )
  ),
  metadata jsonb not null default '{}'::jsonb check (
    jsonb_typeof(metadata) = 'object'
    and octet_length(metadata::text) <= 8192
  ),
  created_at timestamptz not null default now()
);

create index if not exists external_actions_lead_created_idx
  on public.external_actions (lead_id, created_at desc);

create index if not exists external_actions_agent_run_created_idx
  on public.external_actions (agent_run_id, created_at desc)
  where agent_run_id is not null;

create index if not exists external_actions_active_status_idx
  on public.external_actions (status, created_at)
  where status in ('pending', 'approved', 'executing', 'failed');

create index if not exists external_action_events_action_created_idx
  on public.external_action_events (action_id, created_at);

alter table public.agent_state_transitions
  drop constraint if exists agent_state_transitions_from_state_check;
alter table public.agent_state_transitions
  add constraint agent_state_transitions_from_state_check check (
    from_state in (
      'START',
      'load_lead',
      'qualify_lead',
      'route_by_classification',
      'research_state',
      'retrieve_gtm_knowledge',
      'build_research_context',
      'draft_outreach_email',
      'request_external_action',
      'nurture_state',
      'stop_state',
      'persist_agent_state',
      'END'
    )
  );

alter table public.agent_state_transitions
  drop constraint if exists agent_state_transitions_to_state_check;
alter table public.agent_state_transitions
  add constraint agent_state_transitions_to_state_check check (
    to_state in (
      'START',
      'load_lead',
      'qualify_lead',
      'route_by_classification',
      'research_state',
      'retrieve_gtm_knowledge',
      'build_research_context',
      'draft_outreach_email',
      'request_external_action',
      'nurture_state',
      'stop_state',
      'persist_agent_state',
      'END'
    )
  );

alter table public.external_actions enable row level security;
alter table public.external_action_events enable row level security;

revoke all on table public.external_actions
  from public, anon, authenticated, service_role;
revoke all on table public.external_action_events
  from public, anon, authenticated, service_role;

grant select, insert, update on table public.external_actions to service_role;
grant select, insert on table public.external_action_events to service_role;

comment on table public.external_actions is
  'Allowlisted, idempotent Phase 5 external actions with controlled approval.';
comment on table public.external_action_events is
  'Immutable Phase 5 audit trail for external action lifecycle events.';

commit;
