// Workflow detail page

import {
  fetchWorkflowDetail,
  retryWorkflow,
  cancelWorkflow,
  pauseWorkflow,
  resumeWorkflow,
  type WorkflowDetailResponse,
} from "../api.js";
import {
  shortId, timeAgo, duration, statusClass, formatStep, formatCost, esc,
} from "../utils.js";

let pollTimer: ReturnType<typeof setInterval> | undefined;
let workflowId = "";
let actionInFlight = false;

export function mount(container: HTMLElement, id: string): void {
  workflowId = id;
  container.innerHTML = `
    <div class="toolbar">
      <a href="#/workflows" class="back-link">&larr; Workflows</a>
      <span id="wd-actions"></span>
    </div>
    <div id="wd-content">Loading...</div>
  `;
  refresh();
  pollTimer = setInterval(refresh, 5000);
}

export function unmount(): void {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = undefined;
}

async function refresh(): Promise<void> {
  const el = document.getElementById("wd-content");
  if (!el) return;
  try {
    const wf = await fetchWorkflowDetail(workflowId);
    el.innerHTML = renderDetail(wf);
    renderActions(wf);
    attachActionListeners(wf);
  } catch (err) {
    el.innerHTML = `<div class="error">Failed: ${esc(String(err))}</div>`;
  }
}

function renderActions(wf: WorkflowDetailResponse): void {
  const el = document.getElementById("wd-actions");
  if (!el) return;
  const btns: string[] = [];
  if (wf.status === "failed") {
    btns.push('<button id="act-retry" class="btn">Retry</button>');
  }
  if (wf.status === "running") {
    btns.push('<button id="act-pause" class="btn">Pause</button>');
  }
  if (wf.status === "paused" || wf.status === "failed") {
    btns.push('<button id="act-resume" class="btn">Resume</button>');
  }
  if (wf.status === "running" || wf.status === "paused" || wf.status === "pending") {
    btns.push('<button id="act-cancel" class="btn btn-danger">Cancel</button>');
  }
  el.innerHTML = btns.join(" ");
}

async function runAction(fn: () => Promise<unknown>, navigateToNew = false): Promise<void> {
  if (actionInFlight) return;
  actionInFlight = true;
  setActionsDisabled(true);
  try {
    const result = await fn();
    if (navigateToNew && result && typeof result === "object" && "id" in result) {
      location.hash = `#/workflow/${(result as WorkflowDetailResponse).id}`;
      return;
    }
    await refresh();
  } catch (err) {
    const el = document.getElementById("wd-content");
    if (el) el.innerHTML += `<div class="error">Action failed: ${esc(String(err))}</div>`;
  } finally {
    actionInFlight = false;
    setActionsDisabled(false);
  }
}

function setActionsDisabled(disabled: boolean): void {
  const el = document.getElementById("wd-actions");
  if (!el) return;
  el.querySelectorAll("button").forEach((b) => { b.disabled = disabled; });
}

function attachActionListeners(wf: WorkflowDetailResponse): void {
  document.getElementById("act-retry")?.addEventListener("click", () => {
    runAction(() => retryWorkflow(wf.id), true);
  });
  document.getElementById("act-pause")?.addEventListener("click", () => {
    runAction(() => pauseWorkflow(wf.id));
  });
  document.getElementById("act-resume")?.addEventListener("click", () => {
    runAction(() => resumeWorkflow(wf.id));
  });
  document.getElementById("act-cancel")?.addEventListener("click", () => {
    if (confirm("Cancel this workflow?")) {
      runAction(() => cancelWorkflow(wf.id));
    }
  });
}

function renderDetail(wf: WorkflowDetailResponse): string {
  const cls = statusClass(wf.status);
  const parts: string[] = [];

  // Summary
  parts.push(`
    <div class="section">
      <h2>${shortId(wf.id)} <span class="badge ${cls}">${wf.status}</span></h2>
      <div class="meta-grid">
        <span>Created</span><span>${timeAgo(wf.created_at)}</span>
        ${wf.started_at ? `<span>Started</span><span>${timeAgo(wf.started_at)}</span>` : ""}
        ${wf.completed_at ? `<span>Completed</span><span>${timeAgo(wf.completed_at)}</span>` : ""}
        ${wf.started_at ? `<span>Duration</span><span>${duration(wf.started_at, wf.completed_at)}</span>` : ""}
        ${wf.current_step ? `<span>Current Step</span><span>${formatStep(wf.current_step)}</span>` : ""}
        ${wf.error ? `<span>Error</span><span class="text-err">${esc(wf.error)}</span>` : ""}
      </div>
    </div>
  `);

  // Input
  if (wf.input) {
    parts.push(`
      <div class="section">
        <h3>Input</h3>
        <div class="meta-grid">
          <span>Premise</span><span>${esc(wf.input.premise)}</span>
          <span>Voice</span><span>${esc(wf.input.voice_name)}</span>
          <span>Images</span><span>${wf.input.generate_images ? "Yes" : "No"}</span>
          <span>Video</span><span>${wf.input.stitch_video ? "Yes" : "No"}</span>
          <span>Revisions</span><span>${wf.input.max_revisions}</span>
          <span>Target Words</span><span>${wf.input.target_word_count.toLocaleString()}</span>
        </div>
      </div>
    `);
  }

  // Steps
  if (wf.steps.length > 0) {
    const rows = wf.steps.map((s) => {
      const sc = statusClass(s.status);
      const t = s.started_at ? duration(s.started_at, s.completed_at) : "-";
      return `<tr>
        <td>${formatStep(s.step_name)}</td>
        <td><span class="badge ${sc}">${s.status}</span></td>
        <td>${s.attempt_number}</td>
        <td>${t}</td>
        <td class="text-err">${s.error ? esc(s.error.slice(0, 60)) : ""}</td>
      </tr>`;
    }).join("");
    parts.push(`
      <div class="section">
        <h3>Steps</h3>
        <table>
          <thead><tr><th>Step</th><th>Status</th><th>Attempt</th><th>Time</th><th>Error</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `);
  }

  // Chunks
  if (wf.chunks.length > 0) {
    const rows = wf.chunks.map((c) => {
      const sc = statusClass(c.tts_status);
      const dur = c.tts_duration_sec != null ? `${c.tts_duration_sec.toFixed(1)}s` : "-";
      return `<tr>
        <td>${c.chunk_index}</td>
        <td><span class="badge ${sc}">${c.tts_status}</span></td>
        <td>${dur}</td>
      </tr>`;
    }).join("");
    parts.push(`
      <div class="section">
        <h3>Chunks</h3>
        <table>
          <thead><tr><th>#</th><th>TTS Status</th><th>Duration</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `);
  }

  // GPU Pods
  if (wf.gpu_pods.length > 0) {
    const rows = wf.gpu_pods.map((p) => {
      const sc = statusClass(p.status);
      return `<tr>
        <td class="mono">${shortId(p.id)}</td>
        <td>${p.provider}</td>
        <td><span class="badge ${sc}">${p.status}</span></td>
        <td>${formatCost(p.total_cost_cents)}</td>
        <td>${timeAgo(p.created_at)}</td>
      </tr>`;
    }).join("");
    parts.push(`
      <div class="section">
        <h3>GPU Pods</h3>
        <table>
          <thead><tr><th>ID</th><th>Provider</th><th>Status</th><th>Cost</th><th>Created</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `);
  }

  // Result
  if (wf.result) {
    parts.push(`
      <div class="section">
        <h3>Result</h3>
        <pre class="code-block">${esc(JSON.stringify(wf.result, null, 2))}</pre>
      </div>
    `);
  }

  return parts.join("");
}
