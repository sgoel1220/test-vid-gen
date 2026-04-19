// Workflow detail page

import {
  fetchWorkflowDetail,
  fetchStoryByWorkflow,
  updateStory,
  retryWorkflow,
  retryTtsStep,
  retryChunks,
  encodeToMp3,
  cancelWorkflow,
  pauseWorkflow,
  resumeWorkflow,
  type WorkflowDetailResponse,
  type StoryDetailResponse,
} from "../api.js";
import {
  shortId, timeAgo, duration, statusClass, formatStep, formatCost, esc,
} from "../utils.js";

let pollTimer: ReturnType<typeof setInterval> | undefined;
let workflowId = "";
let actionInFlight = false;
let chunkPage = 0;
const CHUNKS_PER_PAGE = 20;

// Story editing state
let storyData: StoryDetailResponse | null = null;
let storyDirty = false;
let storySaving = false;

export function mount(container: HTMLElement, id: string): void {
  workflowId = id;
  storyData = null;
  storyDirty = false;
  storySaving = false;
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

    // Fetch story if generate_story step is completed
    const storyStep = wf.steps.find((s) => s.step_name === "generate_story");
    if (storyStep?.status === "completed" && !storyDirty) {
      try {
        storyData = await fetchStoryByWorkflow(workflowId);
      } catch {
        storyData = null;
      }
    }

    el.innerHTML = renderDetail(wf);
    renderActions(wf);
    attachActionListeners(wf);
    attachChunkListeners();
    attachStoryListeners(wf);
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
  const hasFailedChunks = wf.chunks.some((c) => c.tts_status === "failed");
  if (hasFailedChunks && (wf.status === "failed" || wf.status === "cancelled" || wf.status === "completed")) {
    btns.push('<button id="act-retry-tts" class="btn">Retry TTS Step</button>');
  }
  const hasWavWithoutMp3 = wf.chunks.some(
    (c) => c.tts_audio_blob_id && !c.tts_mp3_blob_id,
  );
  if (hasWavWithoutMp3) {
    btns.push('<button id="act-encode-mp3" class="btn">WAV→MP3</button>');
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

async function runAction(
  fn: () => Promise<unknown>,
  navigateToNew = false,
  label = "Working…",
): Promise<void> {
  if (actionInFlight) return;
  actionInFlight = true;
  setActionsDisabled(true);
  showActionStatus(label);
  try {
    const result = await fn();
    if (navigateToNew && result && typeof result === "object" && "id" in result) {
      location.hash = `#/workflow/${(result as WorkflowDetailResponse).id}`;
      return;
    }
    showActionStatus("");
    await refresh();
  } catch (err) {
    showActionStatus(`Error: ${String(err)}`, true);
  } finally {
    actionInFlight = false;
    setActionsDisabled(false);
  }
}

function showActionStatus(msg: string, isError = false): void {
  let el = document.getElementById("action-status");
  if (!el) {
    el = document.createElement("span");
    el.id = "action-status";
    el.style.cssText = "margin-left:12px;font-size:0.85rem;";
    document.getElementById("wd-actions")?.appendChild(el);
  }
  el.textContent = msg;
  el.style.color = isError ? "#e05252" : "#aaa";
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
  document.getElementById("act-retry-tts")?.addEventListener("click", () => {
    runAction(() => retryTtsStep(wf.id), false, "Retrying TTS step…");
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
  document.getElementById("act-encode-mp3")?.addEventListener("click", () => {
    runAction(
      () => encodeToMp3(wf.id).then((r) => {
        showActionStatus(`Encoded ${r.encoded} chunk(s)${r.skipped ? `, ${r.skipped} failed` : ""}`);
        return r;
      }),
      false,
      "Encoding WAV→MP3…",
    );
  });
}

function attachChunkListeners(): void {
  // Pagination
  document.getElementById("chunk-prev")?.addEventListener("click", () => {
    chunkPage--;
    refresh();
  });
  document.getElementById("chunk-next")?.addEventListener("click", () => {
    chunkPage++;
    refresh();
  });

  // Per-chunk retry buttons
  document.querySelectorAll<HTMLButtonElement>(".chunk-retry-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation(); // don't toggle row expand
      const idx = parseInt(btn.dataset.chunkIndex ?? "", 10);
      if (!isNaN(idx)) {
        runAction(() => retryChunks(workflowId, [idx]), false, `Retrying chunk ${idx}…`);
      }
    });
  });

  // Click-to-expand rows
  document.querySelectorAll<HTMLTableRowElement>(".chunk-row").forEach((row) => {
    row.style.cursor = "pointer";
    row.addEventListener("click", (e) => {
      // Don't toggle when clicking audio controls or retry button
      if ((e.target as HTMLElement).closest("audio, button")) return;
      const idx = row.dataset.chunkIdx;
      const detail = document.querySelector<HTMLTableRowElement>(
        `tr[data-detail-for="${idx}"]`,
      );
      if (detail) {
        detail.style.display = detail.style.display === "none" ? "" : "none";
      }
    });
  });
}

function attachStoryListeners(wf: WorkflowDetailResponse): void {
  const textarea = document.getElementById("story-textarea") as HTMLTextAreaElement | null;
  if (textarea) {
    textarea.addEventListener("input", () => {
      storyDirty = true;
    });
  }

  document.getElementById("story-save")?.addEventListener("click", async () => {
    if (!storyData || storySaving) return;
    const ta = document.getElementById("story-textarea") as HTMLTextAreaElement | null;
    if (!ta) return;
    storySaving = true;
    try {
      storyData = await updateStory(storyData.id, ta.value);
      storyDirty = false;
      await refresh();
    } catch (err) {
      alert(`Save failed: ${err}`);
    } finally {
      storySaving = false;
    }
  });

  document.getElementById("story-approve")?.addEventListener("click", async () => {
    if (storySaving) return;
    storySaving = true;
    try {
      // Save any edits first
      if (storyDirty && storyData) {
        const ta = document.getElementById("story-textarea") as HTMLTextAreaElement | null;
        if (ta) {
          storyData = await updateStory(storyData.id, ta.value);
          storyDirty = false;
        }
      }
      // Resume workflow
      await resumeWorkflow(wf.id);
      await refresh();
    } catch (err) {
      alert(`Approve failed: ${err}`);
    } finally {
      storySaving = false;
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

  // Story section
  if (storyData) {
    parts.push(renderStorySection(wf));
  }

  // Chunks (paginated)
  if (wf.chunks.length > 0) {
    const totalPages = Math.ceil(wf.chunks.length / CHUNKS_PER_PAGE);
    if (chunkPage >= totalPages) chunkPage = totalPages - 1;
    if (chunkPage < 0) chunkPage = 0;
    const start = chunkPage * CHUNKS_PER_PAGE;
    const pageChunks = wf.chunks.slice(start, start + CHUNKS_PER_PAGE);

    const rows = pageChunks.map((c) => {
      const sc = statusClass(c.tts_status);
      const dur = c.tts_duration_sec != null ? `${c.tts_duration_sec.toFixed(1)}s` : "-";
      const textPreview = c.chunk_text.length > 200
        ? esc(c.chunk_text.slice(0, 200)) + "&hellip;"
        : esc(c.chunk_text);
      const audioBlobId = c.tts_mp3_blob_id ?? c.tts_audio_blob_id;
      const downloadBlobId = c.tts_mp3_blob_id ?? c.tts_audio_blob_id;
      const downloadExt = c.tts_mp3_blob_id ? "mp3" : "wav";
      const audio = audioBlobId
        ? `<div class="audio-cell">
            <audio class="chunk-audio" controls preload="none" src="/api/blobs/${audioBlobId}"></audio>
            <a class="dl-link" href="/api/blobs/${downloadBlobId}" download="chunk-${c.chunk_index}.${downloadExt}" title="Download">⬇</a>
           </div>`
        : '<span class="muted">-</span>';
      const completed = c.tts_completed_at ? timeAgo(c.tts_completed_at) : "-";
      const canRetryChunk = c.tts_status === "failed" &&
        (wf.status === "failed" || wf.status === "cancelled" || wf.status === "completed");
      const retryBtn = canRetryChunk
        ? `<button class="btn btn-sm chunk-retry-btn" data-chunk-index="${c.chunk_index}" title="Retry this chunk">↺</button>`
        : "";
      return `<tr class="chunk-row" data-chunk-idx="${c.chunk_index}">
        <td>${c.chunk_index}</td>
        <td class="chunk-text-cell" title="Click to expand">${textPreview}</td>
        <td><span class="badge ${sc}">${c.tts_status}</span> ${retryBtn}</td>
        <td>${dur}</td>
        <td>${audio}</td>
        <td>${completed}</td>
      </tr>
      <tr class="chunk-detail-row" data-detail-for="${c.chunk_index}" style="display:none">
        <td colspan="6"><div class="chunk-full-text">${esc(c.chunk_text)}</div></td>
      </tr>`;
    }).join("");

    const pagination = totalPages > 1
      ? `<div class="pagination">
          <button class="btn" id="chunk-prev" ${chunkPage === 0 ? "disabled" : ""}>Prev</button>
          <span class="pagination-info">Page ${chunkPage + 1} of ${totalPages}</span>
          <button class="btn" id="chunk-next" ${chunkPage >= totalPages - 1 ? "disabled" : ""}>Next</button>
        </div>`
      : "";

    parts.push(`
      <div class="section">
        <h3>Chunks (${wf.chunks.length})</h3>
        <table>
          <thead><tr><th>#</th><th>Text</th><th>Status</th><th>Duration</th><th>Audio</th><th>Completed</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        ${pagination}
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

function renderStorySection(wf: WorkflowDetailResponse): string {
  if (!storyData) return "";

  const isPaused = wf.status === "paused";
  const text = storyData.full_text ?? "";
  const wordCount = storyData.word_count ?? 0;
  const title = storyData.title ? esc(storyData.title) : "Untitled";

  if (isPaused) {
    // Editable mode
    return `
      <div class="section">
        <h3>Story: ${title} <span class="muted">(${wordCount} words)</span></h3>
        <p class="story-hint">Review and edit the story below, then approve to continue to TTS.</p>
        <textarea id="story-textarea" class="story-textarea" rows="20">${esc(text)}</textarea>
        <div class="story-actions">
          <button id="story-save" class="btn">Save</button>
          <button id="story-approve" class="btn btn-approve">Approve &amp; Continue</button>
        </div>
      </div>
    `;
  }

  // Read-only mode
  return `
    <div class="section">
      <h3>Story: ${title} <span class="muted">(${wordCount} words)</span></h3>
      <div class="story-text">${esc(text)}</div>
    </div>
  `;
}
