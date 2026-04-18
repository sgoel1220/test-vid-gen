// GPU Pods page — uses /api/costs/summary + workflow detail gpu_pods

import { fetchCostSummary, fetchWorkflows, fetchWorkflowDetail, type CostSummary } from "../api.js";
import { formatCost, timeAgo, duration, statusClass, shortId, esc } from "../utils.js";

interface PodView {
  id: string;
  provider: string;
  status: string;
  total_cost_cents: number;
  created_at: string;
  ready_at: string | null;
  terminated_at: string | null;
  workflow_id: string;
}

let pollTimer: ReturnType<typeof setInterval> | undefined;

export function mount(container: HTMLElement): void {
  container.innerHTML = `
    <div id="gp-summary" class="summary-row">Loading costs...</div>
    <h3>Active Pods</h3>
    <div id="gp-active" class="list"></div>
    <h3>Terminated Pods</h3>
    <div id="gp-terminated" class="list"></div>
  `;
  refresh();
  pollTimer = setInterval(refresh, 10000);
}

export function unmount(): void {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = undefined;
}

const TERMINAL = new Set(["terminated", "error", "stopped"]);

function isActive(status: string): boolean {
  return !TERMINAL.has(status);
}

async function refresh(): Promise<void> {
  const summaryEl = document.getElementById("gp-summary");
  const activeEl = document.getElementById("gp-active");
  const terminatedEl = document.getElementById("gp-terminated");
  if (!summaryEl || !activeEl || !terminatedEl) return;

  try {
    // Fetch cost summary from dedicated endpoint
    const costs = await fetchCostSummary();
    summaryEl.innerHTML = renderSummary(costs);

    // Collect pods from recent workflows (no dedicated gpu-pods endpoint yet)
    const pods = await collectPods();
    const active = pods.filter((p) => isActive(p.status));
    const terminated = pods.filter((p) => !isActive(p.status));

    // Warn if cost summary shows more active pods than we found
    if (costs.active_pod_count > active.length) {
      const missing = costs.active_pod_count - active.length;
      activeEl.innerHTML = `<div class="error">Warning: ${missing} active pod(s) not shown (attached to older workflows or missing from recent results).</div>`;
    } else {
      activeEl.innerHTML = "";
    }
    activeEl.innerHTML += active.length > 0
      ? active.map(renderPod).join("")
      : '<div class="empty">No active pods.</div>';
    terminatedEl.innerHTML = terminated.length > 0
      ? terminated.map(renderPod).join("")
      : '<div class="empty">No terminated pods.</div>';
  } catch (err) {
    summaryEl.innerHTML = `<div class="error">${esc(String(err))}</div>`;
  }
}

async function collectPods(): Promise<PodView[]> {
  // Fetch recent workflows and extract gpu_pods from their details
  const workflows = await fetchWorkflows(undefined, 20);
  const seen = new Set<string>();
  const pods: PodView[] = [];

  for (const wf of workflows.slice(0, 10)) {
    try {
      const detail = await fetchWorkflowDetail(wf.id);
      for (const p of detail.gpu_pods) {
        if (!seen.has(p.id)) {
          seen.add(p.id);
          pods.push({ ...p, workflow_id: wf.id });
        }
      }
    } catch (err) {
      console.warn(`Failed to fetch pods for workflow ${wf.id}:`, err);
    }
  }
  return pods;
}

function renderSummary(c: CostSummary): string {
  return `
    <span>Active Pods: <strong>${c.active_pod_count}</strong></span>
    <span>Today: <strong>${formatCost(c.today_cents)}</strong></span>
    <span>This Month: <strong>${formatCost(c.month_cents)}</strong></span>
  `;
}

function renderPod(p: PodView): string {
  const cls = statusClass(p.status);
  const uptime = isActive(p.status) ? duration(p.created_at) : "";
  return `
    <div class="row pod-row">
      <span class="mono">${shortId(p.id)}</span>
      <span>${p.provider}</span>
      <span class="badge ${cls}">${p.status}</span>
      <span>${formatCost(p.total_cost_cents)}</span>
      ${uptime ? `<span>up ${uptime}</span>` : ""}
      <a href="#/workflow/${p.workflow_id}" class="link">workflow</a>
    </div>
  `;
}
