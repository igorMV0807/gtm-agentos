# GTM AgentOS

GTM AgentOS is a portfolio-grade backend for AI-assisted go-to-market workflows. This repository contains **Phase 1: AI Lead Qualification Core** and **Phase 2: Agent Orchestration with LangGraph**.

## Problem

Revenue teams receive leads from multiple sources, but qualification is often inconsistent, difficult to audit, and dependent on repetitive manual review. Duplicate records and unstructured model output make a simple automation unreliable in production.

## Solution

The Phase 1 endpoint validates a lead, reuses an existing record when the same lead is submitted again, persists the lead in Supabase/PostgreSQL, asks Claude for a structured qualification, validates the result with Pydantic, stores the decision, and records the complete agent execution. Phase 2 adds a separate LangGraph endpoint that reuses this qualification service, applies deterministic routing, and records every state transition.

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
│   └── routes/leads.py             # HTTP endpoint
├── core/
│   ├── config.py                   # Environment-based settings
│   ├── exceptions.py               # Safe application errors
│   └── logging.py                  # JSON logging
├── models/
│   ├── lead.py                     # Lead and agent run records
│   └── orchestration.py            # Persisted transition record
├── repositories/
│   ├── lead_repository.py          # Lead persistence interface + Supabase adapter
│   ├── agent_run_repository.py     # Agent run interface + Supabase adapter
│   └── agent_state_transition_repository.py
├── schemas/
│   ├── lead.py                     # Input validation
│   ├── qualification.py            # Structured LLM output and Phase 1 response
│   └── orchestration.py            # Routes, actions, status, Phase 2 response
└── services/
    ├── agent_orchestration_service.py # LangGraph nodes and execution
    ├── lead_service.py             # Idempotent lead ingestion
    ├── llm_service.py              # Provider boundary + Anthropic adapter
    └── qualification_service.py    # End-to-end orchestration

sql/001_initial_schema.sql          # Tables, constraints, indexes, RLS, grants
sql/002_agent_state_transitions.sql # Immutable graph transition history
tests/                              # External-service-free test suite
```

The service layer depends on repository and LLM interfaces rather than vendor clients. Supabase and Anthropic are adapters at the edges, so a future provider can be added without rewriting the qualification workflow.

## Tech Stack

- Python 3.12
- FastAPI
- Pydantic v2 and pydantic-settings
- Supabase/PostgreSQL
- Anthropic Claude with native structured outputs
- LangGraph 1.2
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

## Database

Run [`sql/001_initial_schema.sql`](sql/001_initial_schema.sql) and then [`sql/002_agent_state_transitions.sql`](sql/002_agent_state_transitions.sql) in the Supabase SQL Editor before starting the API.

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

3. Create the database objects by running `sql/001_initial_schema.sql` and then `sql/002_agent_state_transitions.sql` in the Supabase SQL Editor.

4. Copy `.env.example` to `.env` and configure:

   ```dotenv
   SUPABASE_URL=https://your-project-ref.supabase.co
   SUPABASE_KEY=your-backend-secret-or-service-role-key
   ANTHROPIC_API_KEY=your-anthropic-api-key
   LLM_PROVIDER=anthropic
   LLM_MODEL=your-supported-claude-model
   ```

5. Start the API.

   ```bash
   uvicorn app.main:app --reload
   ```

6. Verify it is running.

   ```bash
   curl http://localhost:8000/health
   ```

## Testing

Run:

```bash
pytest
```

The tests replace both Supabase and Claude with in-memory fakes. They do not require credentials, network access, or paid API calls.

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

## Roadmap

- **Phase 1 — AI Lead Qualification Core** (completed)
- **Phase 2 — Agent orchestration with LangGraph** (completed)
- **Phase 3 — RAG + PostgreSQL/pgvector**
- **Phase 4 — Tools + MCP Server**
- **Phase 5 — n8n + CRM + email integrations**
- **Phase 6 — Observability + Human-in-the-loop + dashboard**

No Phase 3+ functionality is implemented in this codebase.
