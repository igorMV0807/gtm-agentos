begin;

create table if not exists public.tool_calls (
  id uuid primary key default gen_random_uuid(),
  agent_run_id uuid references public.agent_runs(id) on delete set null,
  lead_id uuid references public.leads(id) on delete set null,
  tool_name text not null check (
    char_length(tool_name) between 1 and 64
    and tool_name ~ '^[a-z][a-z0-9_]*$'
  ),
  input jsonb not null default '{}'::jsonb check (
    jsonb_typeof(input) = 'object'
  ),
  output jsonb check (
    output is null or jsonb_typeof(output) = 'object'
  ),
  status text not null check (
    status in ('completed', 'failed', 'rejected')
  ),
  error text check (
    error is null or char_length(error) between 1 and 200
  ),
  latency_ms integer not null check (latency_ms >= 0),
  created_at timestamptz not null default now(),
  constraint tool_calls_result_consistency check (
    (
      status = 'completed'
      and output is not null
      and error is null
    )
    or (
      status in ('failed', 'rejected')
      and output is null
      and error is not null
    )
  )
);

create index if not exists tool_calls_agent_run_created_idx
  on public.tool_calls (agent_run_id, created_at desc)
  where agent_run_id is not null;

create index if not exists tool_calls_lead_created_idx
  on public.tool_calls (lead_id, created_at desc)
  where lead_id is not null;

create index if not exists tool_calls_name_status_created_idx
  on public.tool_calls (tool_name, status, created_at desc);

alter table public.tool_calls enable row level security;

revoke all on table public.tool_calls
  from public, anon, authenticated, service_role;

grant select, insert on table public.tool_calls to service_role;

comment on table public.tool_calls is
  'Immutable sanitized audit trail for Phase 4 MCP and internal tool calls.';

commit;
