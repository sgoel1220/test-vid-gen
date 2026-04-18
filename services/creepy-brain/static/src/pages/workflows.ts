// Workflow list page

import { fetchWorkflows, type WorkflowStatus, type WorkflowResponse } from "../api.js";
import { shortId, timeAgo, duration, statusClass, formatStep, esc } from "../utils.js";

const STATUSES: (WorkflowStatus | "all")[] = [
  "all", "running", "paused", "completed", "failed", "cancelled",
];

let currentFilter: WorkflowStatus | undefined;
let pollTimer: ReturnType<typeof setInterval> | undefined;

export function mount(container: HTMLElement): void {
  container.innerHTML = `
    <div class="toolbar">
      <div id="wf-filters" class="filter-row"></div>
    </div>
    <div id="wf-list" class="list"></div>
  `;

  const filtersEl = document.getElementById("wf-filters")!;
  for (const s of STATUSES) {
    const btn = document.createElement("button");
    btn.textContent = s === "all" ? "All" : s.charAt(0).toUpperCase() + s.slice(1);
    btn.className = s === "all" ? "filter-btn active" : "filter-btn";
    btn.addEventListener("click", () => {
      currentFilter = s === "all" ? undefined : s;
      filtersEl.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      refresh();
    });
    filtersEl.appendChild(btn);
  }

  refresh();
  pollTimer = setInterval(refresh, 5000);
}

export function unmount(): void {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = undefined;
}

async function refresh(): Promise<void> {
  const list = document.getElementById("wf-list");
  if (!list) return;

  try {
    const workflows = await fetchWorkflows(currentFilter);
    if (workflows.length === 0) {
      list.innerHTML = '<div class="empty">No workflows found.</div>';
      return;
    }
    list.innerHTML = workflows.map(renderRow).join("");
  } catch (err) {
    list.innerHTML = `<div class="error">Failed to load: ${esc(String(err))}</div>`;
  }
}

function renderRow(wf: WorkflowResponse): string {
  const cls = statusClass(wf.status);
  const step = wf.current_step ? formatStep(wf.current_step) : "";
  const age = timeAgo(wf.created_at);

  let timing = age;
  if (wf.status === "running" && wf.started_at) {
    timing = `running ${duration(wf.started_at)}`;
  } else if (wf.status === "completed" && wf.started_at && wf.completed_at) {
    timing = `took ${duration(wf.started_at, wf.completed_at)}`;
  }

  const errorSnippet = wf.error
    ? `<span class="row-error">${esc(wf.error.slice(0, 80))}</span>`
    : "";

  return `
    <a class="row" href="#/workflow/${wf.id}">
      <span class="row-id mono">${shortId(wf.id)}</span>
      <span class="badge ${cls}">${wf.status}</span>
      <span class="row-step">${step}</span>
      <span class="row-time">${timing}</span>
      ${errorSnippet}
    </a>
  `;
}
