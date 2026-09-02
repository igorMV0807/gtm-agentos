# Public screenshot checklist

This directory contains real, sanitized screenshots of the GTM AgentOS Operations Console.

All captures were produced from the locally running application in explicit Portfolio Mode. The records are synthetic and labeled as demo data; no external provider was called.

Included assets:

- `operations-overview.png` — overview metrics, approval queue, and recent agent runs;
- `run-inspector.png` — qualification, route, latency, ranked RAG evidence, MCP calls, and external actions;
- `lead-timeline.png` — ordered qualification, graph, retrieval, MCP, and action events;
- `rag-evidence.png` — focused ranked RAG sources and similarities;
- `mcp-tool-calls.png` — focused completed and rejected MCP audit records;
- `approval-queue.png` — synthetic approval-gated outreach draft;
- `recent-failures.png` — sanitized operational failure codes.

Before committing any image:

1. use only fictional or disposable test data;
2. remove email addresses, recipients, account names, and personal identifiers;
3. remove lead, run, action, workflow, provider, project, and message IDs;
4. remove browser tabs, address bars, local machine paths, and usernames;
5. verify that no API key, token, cookie, authorization header, webhook signature, or environment value is visible;
6. avoid showing provider dashboards unless every tenant-specific detail is redacted;
7. confirm the screenshot represents real product behavior and is not a fabricated mockup.

The PNG files contain no EXIF entries or embedded metadata fields.
