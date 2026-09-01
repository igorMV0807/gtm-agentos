# GTM AgentOS

GTM AgentOS is a portfolio-grade backend for AI-assisted go-to-market workflows. This repository contains **Phase 1: AI Lead Qualification Core**, **Phase 2: Agent Orchestration with LangGraph**, **Phase 3: RAG Knowledge Layer**, **Phase 4: MCP & Controlled Agent Tools**, and **Phase 5: External Actions & n8n**.

## Problem

Revenue teams receive leads from multiple sources, but qualification is often inconsistent, difficult to audit, and dependent on repetitive manual review. Duplicate records and unstructured model output make a simple automation unreliable in production.

## Solution

The Phase 1 endpoint validates a lead, reuses an existing record when the same lead is submitted again, persists the lead in Supabase/PostgreSQL, asks Claude for a structured qualification, validates the result with Pydantic, stores the decision, and records the complete agent execution. Phase 2 adds a separate LangGraph endpoint that reuses this qualification service, applies deterministic routing, and records every state transition. Phase 3 grounds HOT-lead research in approved internal GTM documents retrieved with pgvector. Phase 4 exposes a small, read-only, schema-validated MCP tool surface over the same internal services and records every accepted or rejected execution attempt. Phase 5 turns safe agent decisions into allowlisted, approval-gated, idempotent actions dispatched to n8n with signed webhooks and auditable callbacks.

The response contains:

- a score from 0 to 100;
- a `HOT`, `WARM`, or `COLD` classification;
- a concise reason;
- a controlled next action.

## Architecture

```text
app/
├── main.py                         # FastAPI application and error handlers
├── agents/
│   ├── routing.py                  # Deterministic classification routing
│   └── state.py                    # Validated LangGraph state and transitions
├── api/
│   ├── dependencies.py             # Composition root
│   └── routes/                      # Lead, knowledge, approval, and callback endpoints
├── core/
│   ├── config.py                   # Environment-based settings
│   ├── exceptions.py               # Safe application errors
│   └── logging.py                  # JSON logging
├── models/
│   ├── lead.py                     # Lead and agent run records
│   ├── knowledge.py                # Knowledge, chunk, and evidence records
│   ├── mcp.py                      # Tool audit and aggregate records
│   └── orchestration.py            # Persisted transition record
├── mcp/
│   ├── server.py                   # Isolated stdio MCP server
│   ├── registry.py                 # Closed tool registry and schemas
│   ├── execution.py                # Validation, sanitization, and audit boundary
│   ├── schemas.py                  # Explicit tool input/output contracts
│   └── tools/                      # Read-only lead, RAG, run, and analytics handlers
├── integrations/
│   ├── crm.py                      # CRM protocol + fixed-host HubSpot adapter
│   ├── email.py                    # Email protocol + mandatory approval guard
│   └── n8n.py                      # Signed, idempotent n8n dispatcher
├── repositories/
│   ├── lead_repository.py          # Lead persistence interface + Supabase adapter
│   ├── agent_run_repository.py     # Agent run interface + Supabase adapter
│   ├── knowledge_repository.py     # Document and vector persistence
│   ├── rag_repository.py           # Vector RPC and evidence persistence
│   ├── mcp_repository.py           # Controlled read-only tool queries
│   ├── tool_call_repository.py     # Append-only sanitized tool audit
│   ├── external_action_repository.py # Idempotent action lifecycle persistence
│   └── agent_state_transition_repository.py
├── schemas/
│   ├── lead.py                     # Input validation
│   ├── knowledge.py                # Ingestion, retrieval, and source contracts
│   ├── external_actions.py         # Closed action, callback, and draft schemas
│   ├── qualification.py            # Structured LLM output and Phase 1 response
│   └── orchestration.py            # Routes, actions, status, Phase 2 response
└── services/
    ├── agent_orchestration_service.py # LangGraph nodes and execution
    ├── chunking_service.py         # Deterministic word-window chunking
    ├── embedding_service.py        # Provider boundary + Voyage adapter
    ├── knowledge_ingestion_service.py
    ├── external_action_service.py  # Approval, dispatch, callback, and audit policy
    ├── lead_service.py             # Idempotent lead ingestion
    ├── llm_service.py              # Provider boundary + Anthropic adapter
    ├── qualification_service.py    # End-to-end qualification
    └── retrieval_service.py        # Top-K internal knowledge retrieval

sql/001_initial_schema.sql          # Tables, constraints, indexes, RLS, grants
sql/002_agent_state_transitions.sql # Immutable graph transition history
sql/003_rag_knowledge_base.sql      # pgvector knowledge and RAG evidence
sql/004_mcp_tool_calls.sql          # Immutable sanitized tool-call audit
sql/005_external_actions.sql        # Approval-gated actions and immutable events
n8n/gtm-agentos-actions.workflow.json # Credential-free demonstration workflow
demo_knowledge/                     # Fictional portfolio knowledge documents
tests/                              # External-service-free test suite
```

The service layer depends on repository, LLM, embedding, and tool interfaces rather than vendor clients. Supabase, Anthropic, Voyage, and MCP are adapters at the edges, so provider-specific code does not leak into qualification, ingestion, retrieval, or tool policy.

## Tech Stack

- Python 3.12
- FastAPI
- Pydantic v2 and pydantic-settings
- Supabase/PostgreSQL
- Anthropic Claude with native structured outputs
- Voyage AI `voyage-4` embeddings
- pgvector with cosine similarity and HNSW indexing
- LangGraph 1.2
- Official MCP Python SDK `2.1.1`
- n8n as the external workflow execution layer
- Pytest

## How It Works

1. `POST /api/v1/leads/qualify` receives and validates the payload.
2. The service searches by `external_id`; if no match exists, it searches by `email + company`.
3. An existing lead is updated. A new lead is inserted only when no duplicate exists.
4. A `started` row is written to `agent_runs` before the model call.
5. Claude returns a Pydantic-backed structured output.
6. The application validates the output again before using it.
7. The latest decision is saved on the lead.
8. The agent run becomes `completed` with output and latency, or `failed` with a safe error code.
9. The API returns the qualification as JSON.

Database unique indexes provide a second idempotency boundary. The service also recovers from a concurrent duplicate insert by re-reading and updating the winning record.

## API Example

Request:

```bash
curl -X POST http://localhost:8000/api/v1/leads/qualify \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "lead_001",
    "name": "John Smith",
    "email": "john@acme.com",
    "company": "Acme",
    "job_title": "Head of Sales",
    "company_size": 80,
    "industry": "SaaS",
    "country": "United States",
    "website": "https://acme.com"
  }'
```

Response:

```json
{
  "score": 87,
  "classification": "HOT",
  "reason": "Strong fit and senior buying role.",
  "next_action": "personalized_outreach",
  "lead_id": "70fc9a5c-87d6-43db-ac3c-726874e6cc27"
}
```

Other useful endpoints:

- `POST /api/v1/leads/agent`
- `POST /api/v1/knowledge/documents`
- `GET /health`
- `GET /docs`

## Phase 2 — Agent Orchestration

### Why LangGraph

LangGraph makes each orchestration step explicit and keeps application-controlled routing separate from model output. Claude still performs only the structured Phase 1 qualification. It cannot choose node names or alter graph edges.

The Phase 2 endpoint is:

```text
POST /api/v1/leads/agent
```

It accepts the same validated lead payload as the Phase 1 endpoint. A successful response has this shape:

```json
{
  "lead_id": "70fc9a5c-87d6-43db-ac3c-726874e6cc27",
  "agent_run_id": "61293a82-c6a9-4af2-97ab-5fc6ec7a9123",
  "score": 90,
  "classification": "HOT",
  "route": "research",
  "next_action": "research_company",
  "status": "completed"
}
```

### Agent State

The graph uses a Pydantic state model containing the validated request, lead and run identifiers, qualification, classification, score, reason, controlled route, controlled next action, current step, status, safe error code, and pending transition records. Extra state fields are rejected.

### Graph Flow

```text
START
  ↓
Load Lead
  ↓
Qualify
  ↓
Decision Router
  ├─ HOT  → Research
  ├─ WARM → Nurture
  └─ COLD → Stop
  ↓
Persist State
  ↓
END
```

### Routing

Routing is deterministic application logic:

- `HOT` → `research` → `research_company`
- `WARM` → `nurture` → `nurture_sequence`
- `COLD` → `stop` → `discard`

`research` and `nurture` are structured agent states only in Phase 2. They do not call external research services, email tools, or CRM integrations.

### Persistence

Each Phase 2 request creates a `lead_orchestration` row in `agent_runs`. The existing Phase 1 qualification service retains its own nested `lead_qualification` run, preserving its established audit behavior.

The `agent_state_transitions` table stores the ordered path from `START` through `END`, including the route and a minimal JSON payload for each transition. It has foreign keys to `agent_runs` and `leads`, indexes for run and lead history, RLS enabled, no public client access, and backend `SELECT`/`INSERT` privileges only.

### Failure Handling

Node failures become safe error codes in the graph state. When an orchestration run exists, transitions are persisted and its `agent_runs` row becomes `failed`. Invalid states, invalid routes, qualification provider failures, database failures, and unexpected graph errors never expose a stack trace to the API client.

## Phase 3 — RAG Knowledge Layer

### Why RAG

HOT leads need context that reflects the company's actual positioning, ICP, product, playbook, objections, and case studies. RAG retrieves relevant passages from the approved internal knowledge base before Claude writes a research brief. This reduces unsupported claims and preserves the evidence behind the output.

Phase 3 performs **no public web search, browser research, or web scraping**. It queries only documents stored in this project's PostgreSQL database.

### Knowledge Ingestion

The backend endpoint is:

```text
POST /api/v1/knowledge/documents
```

Example request:

```json
{
  "title": "Ideal Customer Profile",
  "document_type": "icp",
  "content": "Approved internal GTM guidance...",
  "source": "demo_knowledge/icp.md",
  "metadata": {
    "portfolio": true
  }
}
```

The service validates the document, creates its document record, chunks the content, generates document embeddings in one batch, and stores the chunks and vectors. If embedding or chunk persistence fails, the newly created document is removed so incomplete knowledge is not retained.

The endpoint is an administrative backend operation. Protect it with the deployment's service authentication or API gateway before exposing the API publicly.

### Chunking and Embeddings

Chunking uses deterministic word windows: 160 words per chunk with a 24-word overlap by default. This keeps the implementation auditable and avoids a tokenizer-specific dependency. The values are configurable, and the same input and settings always produce the same chunks.

Anthropic [does not provide its own embedding model](https://platform.claude.com/docs/en/build-with-claude/embeddings). The isolated embedding adapter therefore uses Voyage AI `voyage-4`, which supports general-purpose and multilingual retrieval and produces 1,024-dimensional vectors by default. Ingestion uses `input_type=document`; retrieval queries use `input_type=query`, following the [Voyage embedding API](https://docs.voyageai.com/docs/embeddings).

### pgvector Retrieval

`knowledge_chunks.embedding` is `extensions.vector(1024)`. The migration creates an HNSW index with `vector_cosine_ops`, matching the cosine-distance operator used by `match_knowledge_chunks`. HNSW was selected because Supabase recommends it as the default vector index for its performance and robustness as data changes ([Supabase HNSW guide](https://supabase.com/docs/guides/ai/vector-indexes/hnsw-indexes)).

`RetrievalService` embeds the lead-specific query, calls the service-role-only database function, filters results by the configured similarity threshold, sorts by similarity, and returns up to the configured Top K chunks. Defaults are Top 5 and a minimum similarity of 0.40, calibrated against the included demo knowledge with `voyage-4`.

### HOT Lead Flow

```text
HOT Lead
   ↓
Research
   ↓
Build Retrieval Query
   ↓
Voyage Query Embedding
   ↓
pgvector Cosine Search
   ↓
Top-K Internal GTM Knowledge
   ↓
Claude
   ↓
Grounded Research Context
   ↓
Evidence Stored
```

The LangGraph path is now:

```text
research_state
  ↓
retrieve_gtm_knowledge
  ↓
build_research_context
  ↓
persist_agent_state
```

WARM and COLD leads retain their Phase 2 paths and never call the embedding provider or retrieval service.

For HOT leads, the existing response receives two additive fields:

```json
{
  "research_context": "Grounded brief generated only from the lead and retrieved chunks.",
  "sources": [
    {
      "document_id": "8ac2726c-f757-4a17-972e-c29f0dbecfa6",
      "chunk_id": "759b5e8b-e458-4918-a88d-466f10b97a9d",
      "title": "Ideal Customer Profile",
      "similarity": 0.91
    }
  ]
}
```

The original response fields are unchanged. WARM and COLD responses omit the optional RAG fields.

### Evidence and Hallucination Control

Every selected chunk is written to `rag_retrievals` with its orchestration run, lead, query, chunk, similarity, and rank. This makes it possible to trace the research context back to exact internal sources.

Claude receives only the validated lead and retrieved chunks. Its system instruction prohibits public knowledge, assumptions, and unsupported claims. If no chunk meets the threshold, Claude is not called and the successful HOT execution returns:

```text
insufficient_internal_knowledge
```

## Phase 4 — MCP & Agent Tools

### Why MCP

The [Model Context Protocol](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) gives an agent a standard way to discover and call narrowly defined tools without receiving direct database credentials or database clients. GTM AgentOS uses the official Tier 1 [Python SDK](https://github.com/modelcontextprotocol/python-sdk) pinned to `mcp==2.1.1`, compatible with the current 2026-07-28 protocol and earlier negotiated revisions, and runs its MCP server over `stdio`, isolated from the public HTTP application.

Phase 4 is deliberately read-only. It does not send email, update a CRM, run shell commands, execute arbitrary SQL, fetch arbitrary URLs, inspect files, or read environment variables. External actions remain out of scope until a later human-in-the-loop phase.

### Tool Registry

`ToolRegistry` is the only executable tool catalog. Every definition contains:

- a unique allowlisted name;
- a concise description;
- a Pydantic input model;
- a Pydantic output model;
- one handler.

The server exposes exactly these tools:

| Tool | Purpose | Key limits |
|---|---|---|
| `get_lead` | Return safe lead fields | UUID only; email and website omitted |
| `search_leads` | Filter leads | Approved filters only; maximum 50 results |
| `get_lead_history` | Return safe run and transition history | UUID only; raw model input/output omitted |
| `search_internal_knowledge` | Search internal GTM knowledge | Existing `RetrievalService`; query ≤ 1,000 characters; Top K ≤ 10 |
| `get_agent_run` | Return an auditable run summary | UUID only; safe output keys only |
| `get_pipeline_summary` | Return simple classification and route counts | Fixed aggregates; no custom grouping |

Names outside this registry are rejected. Tool parameters cannot supply table names, SQL, Python, shell commands, file paths, secrets, or URLs.

### Schemas and Security Boundaries

Inputs and outputs are validated twice: the MCP SDK derives protocol schemas from typed functions, and the internal executor validates against the registry's explicit Pydantic models before and after the handler. Models reject extra fields and bound strings, filters, `limit`, and `top_k`.

The LLM never receives a Supabase client. MCP handlers call repository interfaces or the existing Phase 3 `RetrievalService`. Lead tools omit email, website, qualification reason, raw run inputs, raw run outputs, and unsafe internal errors. Audit payloads recursively redact fields whose names indicate passwords, tokens, credentials, API keys, authorization headers, cookies, or secrets.

### Auditability

Every registry execution writes one append-only `tool_calls` row with sanitized input/output, status, safe error code, latency, and optional lead/run foreign keys. Rejected unknown tools and invalid payloads are audited as `rejected`; handler failures are audited as `failed`; valid calls are `completed`.

`tool_calls` has RLS enabled. `public`, `anon`, and `authenticated` have no privileges. The backend `service_role` receives only `SELECT` and `INSERT`, so existing audit rows cannot be updated or deleted through this adapter.

Structured log events are limited to safe metadata:

```text
mcp_server_started
tool_call_started
tool_call_completed
tool_call_failed
tool_input_rejected
unknown_tool_rejected
```

### Agent Tool Execution

The production LangGraph was not changed merely to force a demonstration tool call. Qualification, routing, and HOT-lead RAG remain deterministic and backward compatible. A host or future controlled graph node can pass an existing `lead_id` or `agent_run_id` to the MCP client, receive a schema-validated result, and then continue the agent flow. Tests demonstrate this path with the SDK's in-memory MCP client and the same registry used by the `stdio` server.

```text
Claude / Agent
      ↓
LangGraph
      ↓
Tool Decision
      ↓
MCP Server
      ↓
Tool Registry
   ↙    ↓     ↘
Leads  RAG   Runs
   ↘    ↓     ↙
   Supabase
      ↓
Validated Result
      ↓
Agent continues
```

This relationship is additive: LangGraph owns state and deterministic routing, RAG owns grounded internal retrieval, and MCP owns discovery, tool schemas, execution policy, and tool-call auditing.

Example MCP calls use structured arguments and results:

```json
{
  "tool": "get_lead",
  "arguments": {"lead_id": "11111111-1111-4111-8111-111111111111"},
  "result": {
    "lead": {
      "id": "11111111-1111-4111-8111-111111111111",
      "name": "Example Buyer",
      "company": "Example SaaS",
      "classification": "HOT"
    }
  }
}
```

```json
{
  "tool": "search_internal_knowledge",
  "arguments": {"query": "Head of Sales pilot", "top_k": 3},
  "result": {
    "query": "Head of Sales pilot",
    "results": [
      {
        "document_id": "22222222-2222-4222-8222-222222222222",
        "chunk_id": "33333333-3333-4333-8333-333333333333",
        "title": "Sales Playbook",
        "content": "Use a focused pilot for qualified B2B SaaS leads.",
        "similarity": 0.91
      }
    ],
    "count": 1
  }
}
```

```json
{
  "tool": "get_pipeline_summary",
  "arguments": {},
  "result": {
    "total_leads": 9,
    "hot": 4,
    "warm": 3,
    "cold": 2,
    "research": 4,
    "nurture": 3,
    "stop": 2
  }
}
```

An attempted `delete_lead` call is rejected as `unknown_tool`; there is no destructive handler to invoke.

## Phase 5 — External Actions & n8n

### Why n8n

n8n is the external execution layer, not the system of record. GTM AgentOS keeps
lead data, qualification, graph state, policy, idempotency, approval, and audit in
the backend. n8n receives one already validated action, invokes the configured CRM
or email provider, and returns a signed result. This keeps vendor workflow details
outside the domain while preventing n8n from deciding what the agent is allowed to
do.

```text
Agent
  ↓
Draft Action
  ↓
External Action
  ↓
Human Approval
  ↓
Signed n8n Webhook
  ↓
CRM / Email
  ↓
Signed Callback
  ↓
Audit
  ↓
Agent continues from persisted result
```

Only these action types exist in the API schema and database constraint:

- `create_or_update_crm_lead`;
- `create_follow_up_task`;
- `draft_outreach_email`;
- `send_approved_email`;
- `mark_lead_status`.

There is no generic action creation endpoint, arbitrary HTTP action, caller-provided
URL, SQL, shell execution, or delete action. The public control endpoints can only
approve or reject an action that already exists, or receive its signed result:

```text
POST /api/v1/actions/{action_id}/approve
POST /api/v1/actions/{action_id}/reject
POST /api/v1/integrations/n8n/callback
```

### Separation of responsibilities

- LangGraph decides among application-owned graph edges; it does not call a CRM or
  email provider directly.
- Claude returns a strict `subject`, `body`, and public `reasoning_summary` using
  only the lead, generated research context, and retrieved approved chunks.
- `ExternalActionService` validates the closed payload schema, applies approval
  policy, sanitizes stored data, and owns lifecycle transitions.
- `N8nActionService` sends only to the configured `N8N_WEBHOOK_URL`; an LLM or
  callback cannot replace that destination.
- `CRMProvider` and `EmailProvider` keep vendor APIs outside domain and graph code.
  The demonstration CRM adapter targets fixed HubSpot API paths.

### Human approval and email safety

A HOT lead with grounded RAG evidence receives a structured draft and one
`send_approved_email` action in `pending` status. Draft creation never sends the
message. The email guard accepts only a `send_approved_email` action whose
`approved_at` is present and whose state has passed the approval gate. Rejection is
terminal and does not invoke n8n. WARM creates a follow-up task proposal without an
email; COLD creates no external action.

### Idempotency

Every action has a unique, bounded key derived from
`lead_id:action_type:campaign_step`. The repository uses an idempotent database
upsert and returns the existing row on duplicates. Approval is a conditional state
transition, so repeated approval requests cannot dispatch an action already in
`executing` or `completed`. A failed dispatch may be retried on the same row with
the same key; it never creates a second email, CRM lead, or task record.

### Signed webhooks and callbacks

Outbound and callback messages use HMAC SHA-256 over `timestamp.raw_body`. The
receiver requires `X-GTM-Timestamp` and `X-GTM-Signature`, compares signatures in
constant time, and rejects timestamps outside a bounded replay window. The callback
schema accepts only `action_id`, `completed|failed`, an optional external reference,
and bounded metadata. It never trusts an inbound `action_type`: the service reloads
the existing action by ID before applying a conditional transition.

### External action audit trail

`external_actions` stores the current action state, safe payload, approval and
execution timestamps, stable idempotency key, provider reference, safe result, and
error code. `external_action_events` is append-only and records requested, drafted,
approved, rejected, started, callback-received, completed, and failed events. Both
tables have RLS enabled; anonymous and authenticated roles have no grants, while the
backend service role receives only the operations its repositories need. Token-,
secret-, password-, cookie-, credential-, and authorization-shaped fields are
redacted before audit or result persistence.

### Demonstration n8n workflow

Import `n8n/gtm-agentos-actions.workflow.json` into n8n, then configure these n8n
environment variables:

```dotenv
N8N_WEBHOOK_SECRET=replace-with-a-long-random-shared-secret
GTM_AGENTOS_CALLBACK_URL=https://agentos.example.com/api/v1/integrations/n8n/callback
```

The inactive workflow demonstrates Webhook Trigger → signature validation →
allowlisted switch → CRM/email/task provider placeholder → signed callback. It has
no embedded credentials and deliberately uses no real provider node. Replace only
the provider placeholders when performing a separately authorized external
integration validation; keep signature validation, allowlisting, idempotency, and
callback signing intact.

## Database

Run [`sql/001_initial_schema.sql`](sql/001_initial_schema.sql), [`sql/002_agent_state_transitions.sql`](sql/002_agent_state_transitions.sql), [`sql/003_rag_knowledge_base.sql`](sql/003_rag_knowledge_base.sql), [`sql/004_mcp_tool_calls.sql`](sql/004_mcp_tool_calls.sql), and [`sql/005_external_actions.sql`](sql/005_external_actions.sql) in order before starting the API or MCP server.

The migration creates:

- `leads`, including qualification fields and unique idempotency indexes;
- `agent_runs`, including model input/output, status, error, and latency;
- a private trigger function that maintains `updated_at`;
- Row Level Security on both public tables;
- explicit access for `service_role` only, with access revoked from `anon` and `authenticated`.

The Phase 2 migration adds:

- `agent_state_transitions`, with foreign keys, controlled state/route values, and JSONB payloads;
- indexes for ordered audit queries by run and lead;
- RLS with no `anon` or `authenticated` access;
- immutable backend access through `SELECT` and `INSERT` only.

The Phase 3 migration adds:

- the `vector` extension in the `extensions` schema;
- `knowledge_documents` and `knowledge_chunks` with a cascading document foreign key;
- 1,024-dimensional vectors and an HNSW cosine index;
- the service-role-only `match_knowledge_chunks` search function;
- `rag_retrievals` with indexed foreign keys and immutable evidence;
- the two new HOT-path states in transition constraints;
- RLS on every new table, no `anon` or `authenticated` privileges, and least-privilege backend grants.

The Phase 4 migration adds:

- append-only `tool_calls` with safe status, error, latency, input, and output constraints;
- nullable foreign keys to leads and agent runs that preserve the audit row on deletion;
- indexes for run, lead, tool, status, and creation time;
- RLS and explicit revocation from `public`, `anon`, and `authenticated`;
- backend-only `SELECT` and `INSERT` privileges.

The Phase 5 migration adds:

- `external_actions` with a closed action/status allowlist, bounded JSONB fields,
  unique idempotency keys, lifecycle consistency checks, and restrictive foreign keys;
- append-only `external_action_events` for complete lifecycle auditing;
- indexed lead, run, active-status, and event lookup paths;
- the HOT draft and action-request states in graph transition constraints;
- RLS and explicit revocation from `public`, `anon`, and `authenticated`;
- service-role-only `SELECT`, `INSERT`, and conditional `UPDATE` for actions, and
  `SELECT`/`INSERT` for immutable events. No role receives `DELETE`.

`SUPABASE_KEY` must be a backend-only Supabase secret/service-role key. Never expose it in a browser or commit it to Git.

## Reliability

- Pydantic rejects malformed input before business logic runs.
- Anthropic structured outputs are mapped to a strict Pydantic model.
- Scores, classifications, next actions, and database values have constraints.
- Arbitrary model text cannot become an application control instruction.
- Provider timeouts return HTTP `504`.
- Invalid model output and provider failures return HTTP `502`.
- Database failures return HTTP `503`.
- Public responses never include internal provider or database details.
- Logs use named events and omit lead payloads, email addresses, and secrets.
- Every attempted model call has an auditable `agent_runs` record when the database is available.
- Embedding batches are validated for count, ordering, finite values, and exact vector dimension.
- Retrieval applies both a bounded Top K and a similarity threshold.
- Empty retrieval skips Claude and returns a controlled fallback.
- RAG evidence links every source chunk to its lead and orchestration run.
- MCP exposes a fixed registry of six read-only tools over `stdio` only.
- Tool inputs and outputs are schema-validated before and after every handler.
- Tool audit records are append-only and recursively redact credential-shaped fields.
- No MCP tool accepts SQL, table names, shell commands, file paths, arbitrary URLs, or code.
- External action payloads are selected from a closed schema map and size-bounded.
- Sensitive email/CRM execution requires an explicit, persisted approval transition.
- Stable idempotency keys and conditional updates prevent duplicate dispatch.
- n8n webhook requests and callbacks use HMAC signatures and a replay window.
- Provider URLs come only from configuration; action and model payloads cannot set them.

## Running Locally

Requirements: Python 3.12 and a Supabase project.

1. Create and activate a virtual environment.

   Windows PowerShell:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   macOS/Linux:

   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```

2. Install the fully locked dependency set.

   ```bash
   python -m pip install -r requirements.lock
   ```

   `requirements.txt` lists the direct dependencies; `requirements.lock` pins the
   complete tested dependency graph for reproducible installations.

3. Create the database objects by running `sql/001_initial_schema.sql`, `sql/002_agent_state_transitions.sql`, `sql/003_rag_knowledge_base.sql`, `sql/004_mcp_tool_calls.sql`, and `sql/005_external_actions.sql` in order in the Supabase SQL Editor.

4. Copy `.env.example` to `.env` and configure:

   ```dotenv
   SUPABASE_URL=https://your-project-ref.supabase.co
   SUPABASE_KEY=your-backend-secret-or-service-role-key
   ANTHROPIC_API_KEY=your-anthropic-api-key
   LLM_PROVIDER=anthropic
   LLM_MODEL=your-supported-claude-model
   EMBEDDING_PROVIDER=voyage
   EMBEDDING_MODEL=voyage-4
   EMBEDDING_API_KEY=your-voyage-api-key
   EMBEDDING_DIMENSION=1024
   RAG_TOP_K=5
   RAG_SIMILARITY_THRESHOLD=0.40
   RAG_CHUNK_SIZE_WORDS=160
   RAG_CHUNK_OVERLAP_WORDS=24
   N8N_WEBHOOK_URL=https://your-n8n.example/webhook/gtm-agentos-actions
   N8N_WEBHOOK_SECRET=replace-with-a-long-random-shared-secret
   CRM_PROVIDER=hubspot
   HUBSPOT_ACCESS_TOKEN=
   ```

   `HUBSPOT_ACCESS_TOKEN` is optional until the HubSpot adapter is intentionally
   used. Keep all provider credentials server-side. The API requires HTTPS for the
   n8n URL except when it explicitly targets localhost.

5. Start the API.

   ```bash
   uvicorn app.main:app --reload
   ```

6. Verify it is running.

   ```bash
   curl http://localhost:8000/health
   ```

7. Run the isolated MCP server over `stdio` when an MCP host needs the tools.

   ```bash
   python -m app.mcp.server
   ```

   Configure the MCP host to launch that command with this project as its working
   directory. Keep the environment server-side; never place Supabase, Anthropic,
   or Voyage secrets in MCP tool arguments.

## Testing

Run:

```bash
pytest
```

The tests replace Supabase, Claude, Voyage, vector search, MCP repositories, and tool auditing with in-memory fakes or local transports. They do not require credentials, network access, or paid API calls.

Covered behavior includes:

- valid lead qualification;
- invalid payload rejection;
- duplicate lead reuse;
- `HOT`, `WARM`, and `COLD` results;
- invalid LLM output;
- provider failure;
- LLM timeout;
- database failure;
- successful and failed `agent_runs` updates.
- deterministic `HOT`, `WARM`, and `COLD` graph routes;
- invalid route rejection;
- persisted graph transitions;
- failed graph nodes and safe API errors;
- the Phase 2 response contract;
- continued compatibility of the Phase 1 endpoint.
- knowledge document ingestion and deterministic chunking;
- stored embedding and chunk metadata;
- Top-K retrieval and similarity filtering;
- HOT-only RAG execution;
- grounded Claude inputs and optional source output;
- no-context fallback without a Claude call;
- persisted retrieval evidence;
- safe embedding, retrieval, and research-provider failures;
- continued backward compatibility for WARM, COLD, and Phase 1 responses.
- all six read-only MCP tools;
- exact MCP tool exposure through the official in-memory client;
- registry allowlisting and rejection of unknown tools;
- strict input/output schemas and rejection of extra filters;
- result limits and reuse of the existing retrieval service;
- completed, failed, and rejected tool-call audit records;
- secret redaction from logs and stored audit payloads;
- continued compatibility of the Phase 1–3 HTTP endpoints.
- allowlisted external action creation and strict payload rejection;
- human approval, rejection, single dispatch, safe failure, and retry behavior;
- unique action idempotency and duplicate approval protection;
- valid, invalid, and stale HMAC signatures plus strict callback schemas;
- credential redaction from nested callback metadata;
- CRM and n8n adapter requests through local recording fakes;
- structured HOT email drafts, WARM task planning, and COLD no-action behavior;
- continued compatibility of the Phase 1–4 behavior without external calls.

## Roadmap

- **Phase 1 — AI Lead Qualification Core** (completed)
- **Phase 2 — Agent orchestration with LangGraph** (completed)
- **Phase 3 — RAG + PostgreSQL/pgvector** (completed)
- **Phase 4 — Tools + MCP Server** (completed)
- **Phase 5 — n8n + CRM + email integrations** (completed locally with fakes)
- **Phase 6 — Observability + Human-in-the-loop + dashboard**

Phase 5 has not been validated against real n8n, HubSpot, or email accounts. No
Phase 6 functionality is implemented in this codebase.
