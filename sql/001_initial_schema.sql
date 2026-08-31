begin;

create extension if not exists pgcrypto;

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create or replace function private.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

revoke all on function private.set_updated_at() from public, anon, authenticated;
grant usage on schema private to service_role;
grant execute on function private.set_updated_at() to service_role;

create table if not exists public.leads (
  id uuid primary key default gen_random_uuid(),
  external_id text,
  name text not null,
  email text not null,
  company text not null,
  job_title text,
  company_size integer check (company_size is null or company_size > 0),
  industry text,
  country text,
  website text,
  score smallint check (score is null or score between 0 and 100),
  classification text check (
    classification is null or classification in ('HOT', 'WARM', 'COLD')
  ),
  qualification_reason text,
  next_action text check (
    next_action is null or next_action in (
      'personalized_outreach',
      'nurture',
      'manual_review',
      'discard'
    )
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists leads_external_id_unique_idx
  on public.leads (external_id)
  where external_id is not null;

create unique index if not exists leads_email_company_unique_idx
  on public.leads (lower(email), company);

create index if not exists leads_classification_idx
  on public.leads (classification)
  where classification is not null;

drop trigger if exists leads_set_updated_at on public.leads;
create trigger leads_set_updated_at
before update on public.leads
for each row execute function private.set_updated_at();

create table if not exists public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references public.leads(id) on delete cascade,
  agent_type text not null,
  model text not null,
  status text not null check (status in ('started', 'completed', 'failed')),
  input jsonb not null,
  output jsonb,
  error text,
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  created_at timestamptz not null default now()
);

create index if not exists agent_runs_lead_created_idx
  on public.agent_runs (lead_id, created_at desc);

create index if not exists agent_runs_status_idx
  on public.agent_runs (status);

alter table public.leads enable row level security;
alter table public.agent_runs enable row level security;

revoke all on table public.leads from anon, authenticated;
revoke all on table public.agent_runs from anon, authenticated;

grant select, insert, update on table public.leads to service_role;
grant select, insert, update on table public.agent_runs to service_role;

comment on table public.leads is 'Phase 1 B2B leads and their latest qualification.';
comment on table public.agent_runs is 'Auditable executions of the lead qualification agent.';

commit;

