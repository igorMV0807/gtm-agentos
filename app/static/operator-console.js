"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};
const dateText = (value) => value ? new Date(value).toLocaleString() : "—";
const numberText = (value, digits = 0) => Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: digits });

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    window.location.assign("/operator/login");
    throw new Error("operator_auth_required");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.error?.code || "request_failed");
  }
  return response.json();
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2400);
}

function renderOverview(data) {
  $("demo-banner").hidden = !data.demo_mode;
  $("total-leads").textContent = numberText(data.leads.total);
  $("hot-leads").textContent = numberText(data.leads.hot);
  $("warm-leads").textContent = numberText(data.leads.warm);
  $("cold-leads").textContent = numberText(data.leads.cold);
  $("agent-success").textContent = `${numberText(data.agents.success_rate * 100, 1)}%`;
  $("agent-latency").textContent = `Average latency ${numberText(data.agents.average_latency_ms)}ms`;
  $("ai-cost").textContent = `$${numberText(data.ai.estimated_cost_usd, 4)}`;
  $("tool-calls").textContent = numberText(data.tools.calls);
  $("tool-rejected").textContent = `${numberText(data.tools.rejected)} rejected`;
  $("rag-retrievals").textContent = numberText(data.rag.retrievals);
  $("rag-similarity").textContent = `Average similarity ${numberText(data.rag.average_similarity, 2)}`;
  $("pending-approvals").textContent = numberText(data.actions.waiting_approval);
  $("failed-actions").textContent = numberText(data.actions.failed);
  const failures = $("failures");
  failures.replaceChildren();
  if (!data.recent_failures.length) failures.append(el("p", "empty-state", "No recent failures."));
  for (const item of data.recent_failures) {
    const row = el("div", "failure");
    const left = el("div");
    left.append(el("code", "", item.error_code), el("p", "muted", item.component));
    row.append(left, el("time", "", dateText(item.timestamp)));
    failures.append(row);
  }
}

function renderApprovals(data) {
  const container = $("approvals");
  container.replaceChildren();
  if (!data.items.length) {
    container.append(el("p", "empty-state", "No actions are waiting for approval."));
    return;
  }
  for (const action of data.items) {
    const card = el("article", "approval");
    const meta = el("div", "approval-meta");
    meta.append(el("h3", "", action.company || "Unknown company"));
    meta.append(el("p", "", `${action.lead_name || "Lead"} · ${action.action_type}`));
    meta.append(el("p", "", dateText(action.created_at)));
    const draft = el("div", "approval-draft");
    if (action.payload_preview.subject) draft.append(el("h3", "", action.payload_preview.subject));
    if (action.payload_preview.body) draft.append(el("p", "", action.payload_preview.body));
    if (!action.payload_preview.body) draft.append(el("p", "", JSON.stringify(action.payload_preview, null, 2)));
    const buttons = el("div", "approval-actions");
    const approve = el("button", "button approve", "Approve");
    const reject = el("button", "button reject", "Reject");
    approve.type = reject.type = "button";
    approve.disabled = reject.disabled = action.demo;
    approve.title = reject.title = action.demo ? "Disabled for synthetic portfolio data" : "";
    approve.addEventListener("click", () => decide(action.id, "approve"));
    reject.addEventListener("click", () => decide(action.id, "reject"));
    buttons.append(approve, reject);
    card.append(meta, draft, buttons);
    container.append(card);
  }
}

async function decide(actionId, decision) {
  try {
    await api(`/api/v1/actions/${actionId}/${decision}`, { method: "POST", body: "{}" });
    showToast(decision === "approve" ? "Action approved." : "Action rejected.");
    await loadCore();
  } catch (error) { showToast(`Unable to ${decision}: ${error.message}`); }
}

function renderRuns(data) {
  const body = $("runs");
  body.replaceChildren();
  for (const run of data.items) {
    const row = el("tr");
    const lead = el("td");
    lead.append(el("strong", "", run.company || run.lead_name || "Lead"));
    const classification = el("td");
    classification.append(el("span", `pill ${run.classification || ""}`, run.classification || "—"));
    const status = el("td");
    status.append(el("span", `pill ${run.status}`, run.status));
    row.append(lead, classification, status, el("td", "", run.latency_ms === null ? "—" : `${run.latency_ms}ms`));
    row.addEventListener("click", () => inspectRun(run.id, run.lead_id));
    body.append(row);
  }
}

async function inspectRun(runId, leadId) {
  try {
    const [run, timeline] = await Promise.all([
      api(`/api/v1/admin/agent-runs/${runId}`),
      api(`/api/v1/admin/leads/${leadId}/timeline`),
    ]);
    renderInspector(run);
    renderTimeline(timeline);
  } catch (error) { showToast(`Unable to inspect run: ${error.message}`); }
}

function renderInspector(run) {
  const root = $("inspector");
  root.className = "inspector-content";
  root.replaceChildren();
  const facts = el("div", "facts");
  for (const [label, value] of [["Classification", run.classification || "—"], ["Score", run.score ?? "—"], ["Route", run.route || "—"], ["Status", run.status], ["Latency", run.latency_ms === null ? "—" : `${run.latency_ms}ms`], ["Model", run.model]]) {
    const fact = el("div", "fact"); fact.append(el("span", "", label), el("strong", "", value)); facts.append(fact);
  }
  root.append(facts);
  if (run.reasoning_summary) root.append(el("p", "muted", run.reasoning_summary));
  const evidence = el("div", "evidence"); evidence.append(el("h3", "", "RAG evidence"));
  if (!run.rag_evidence.length) evidence.append(el("p", "muted", "No RAG evidence for this run."));
  for (const item of run.rag_evidence) { const row = el("div", "evidence-row"); row.append(el("span", "", item.document_title), el("span", "", `#${item.rank}`), el("strong", "", Number(item.similarity).toFixed(2))); evidence.append(row); }
  const tools = el("div", "tools"); tools.append(el("h3", "", "Tool calls"));
  if (!run.tool_calls.length) tools.append(el("p", "muted", "No audited tool calls."));
  for (const item of run.tool_calls) { const row = el("div", "tool-row"); row.append(el("span", "", item.tool_name), el("span", `pill ${item.status}`, item.status), el("strong", "", `${item.latency_ms}ms`)); tools.append(row); }
  const actions = el("div", "actions"); actions.append(el("h3", "", "External actions"));
  if (!run.external_actions.length) actions.append(el("p", "muted", "No external actions."));
  for (const item of run.external_actions) actions.append(el("p", "muted", `${item.action_type} · ${item.status}`));
  root.append(evidence, tools, actions);
}

function renderTimeline(data) {
  const root = $("timeline");
  root.className = "timeline-list";
  root.replaceChildren();
  for (const item of data.events) {
    const row = el("div", "timeline-event");
    row.append(el("h3", "", item.event));
    row.append(el("p", "", `${item.component}${item.status ? ` · ${item.status}` : ""} · ${dateText(item.timestamp)}`));
    root.append(row);
  }
}

async function loadCore() {
  const [overview, approvals, runs] = await Promise.all([
    api("/api/v1/admin/overview"),
    api("/api/v1/admin/actions?status=pending&limit=20"),
    api("/api/v1/admin/agent-runs?limit=20"),
  ]);
  renderOverview(overview); renderApprovals(approvals); renderRuns(runs);
}

$("refresh").addEventListener("click", () => loadCore().catch((error) => showToast(error.message)));
loadCore().catch((error) => showToast(`Console unavailable: ${error.message}`));
