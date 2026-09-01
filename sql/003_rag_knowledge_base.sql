begin;

create extension if not exists vector with schema extensions;

create table if not exists public.knowledge_documents (
  id uuid primary key default gen_random_uuid(),
  title text not null check (char_length(title) between 1 and 200),
  document_type text not null check (
    document_type ~ '^[a-z][a-z0-9_-]{0,63}$'
  ),
  source text check (source is null or char_length(source) between 1 and 500),
  metadata jsonb not null default '{}'::jsonb check (
    jsonb_typeof(metadata) = 'object'
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

drop trigger if exists knowledge_documents_set_updated_at
  on public.knowledge_documents;
create trigger knowledge_documents_set_updated_at
before update on public.knowledge_documents
for each row execute function private.set_updated_at();

create table if not exists public.knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null
    references public.knowledge_documents(id) on delete cascade,
  content text not null check (char_length(content) > 0),
  chunk_index integer not null check (chunk_index >= 0),
  metadata jsonb not null default '{}'::jsonb check (
    jsonb_typeof(metadata) = 'object'
  ),
  embedding extensions.vector(1024) not null,
  created_at timestamptz not null default now(),
  constraint knowledge_chunks_document_index_unique
    unique (document_id, chunk_index)
);

create index if not exists knowledge_chunks_embedding_hnsw_idx
  on public.knowledge_chunks
  using hnsw (embedding vector_cosine_ops);

create table if not exists public.rag_retrievals (
  id uuid primary key default gen_random_uuid(),
  agent_run_id uuid not null
    references public.agent_runs(id) on delete cascade,
  lead_id uuid not null
    references public.leads(id) on delete cascade,
  query text not null check (char_length(query) between 1 and 2000),
  chunk_id uuid not null
    references public.knowledge_chunks(id) on delete restrict,
  similarity double precision not null check (similarity between 0 and 1),
  rank integer not null check (rank between 1 and 20),
  created_at timestamptz not null default now(),
  constraint rag_retrievals_run_rank_unique unique (agent_run_id, rank)
);

create index if not exists rag_retrievals_lead_created_idx
  on public.rag_retrievals (lead_id, created_at desc);

create index if not exists rag_retrievals_chunk_idx
  on public.rag_retrievals (chunk_id);

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
      'nurture_state',
      'stop_state',
      'persist_agent_state',
      'END'
    )
  );

create or replace function public.match_knowledge_chunks(
  query_embedding extensions.vector(1024),
  match_threshold double precision default 0.65,
  match_count integer default 5
)
returns table (
  document_id uuid,
  chunk_id uuid,
  title text,
  content text,
  similarity double precision,
  metadata jsonb
)
language sql
stable
security invoker
set search_path = ''
as $$
  select
    kd.id as document_id,
    kc.id as chunk_id,
    kd.title,
    kc.content,
    (
      1 - (kc.embedding operator(extensions.<=>) query_embedding)
    )::double precision as similarity,
    pg_catalog.jsonb_strip_nulls(
      kd.metadata
      || kc.metadata
      || pg_catalog.jsonb_build_object(
        'document_type', kd.document_type,
        'source', kd.source,
        'chunk_index', kc.chunk_index
      )
    ) as metadata
  from public.knowledge_chunks as kc
  join public.knowledge_documents as kd on kd.id = kc.document_id
  where (
    1 - (kc.embedding operator(extensions.<=>) query_embedding)
  ) >= greatest(
    0.0,
    least(1.0, coalesce(match_threshold, 0.65))
  )
  order by kc.embedding operator(extensions.<=>) query_embedding
  limit greatest(
    1,
    least(20, coalesce(match_count, 5))
  );
$$;

alter table public.knowledge_documents enable row level security;
alter table public.knowledge_chunks enable row level security;
alter table public.rag_retrievals enable row level security;

revoke all on table public.knowledge_documents
  from public, anon, authenticated, service_role;
revoke all on table public.knowledge_chunks
  from public, anon, authenticated, service_role;
revoke all on table public.rag_retrievals
  from public, anon, authenticated, service_role;

grant select, insert, delete on table public.knowledge_documents to service_role;
grant select, insert on table public.knowledge_chunks to service_role;
grant select, insert on table public.rag_retrievals to service_role;
grant usage on schema extensions to service_role;

revoke all on function public.match_knowledge_chunks(
  extensions.vector,
  double precision,
  integer
) from public, anon, authenticated, service_role;
grant execute on function public.match_knowledge_chunks(
  extensions.vector,
  double precision,
  integer
) to service_role;

comment on table public.knowledge_documents is
  'Phase 3 internal GTM knowledge documents.';
comment on table public.knowledge_chunks is
  'Deterministic document chunks with Voyage voyage-4 embeddings (1024 dimensions).';
comment on table public.rag_retrievals is
  'Immutable evidence linking HOT lead research runs to retrieved knowledge chunks.';
comment on function public.match_knowledge_chunks is
  'Service-role-only cosine similarity search over internal GTM knowledge.';

commit;
