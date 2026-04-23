// Workflow list page

import {
  fetchWorkflows, fetchVoices, createWorkflow, fetchPipelineSchema,
  type WorkflowStatus, type WorkflowResponse, type VoiceResponse,
  type PipelineSchemaResponse, type StepParamSchemaEntry,
} from "../api.js";
import { shortId, timeAgo, duration, statusClass, formatStep, esc } from "../utils.js";

const STATUSES: (WorkflowStatus | "all")[] = [
  "all", "running", "paused", "completed", "failed", "cancelled",
];

let currentFilter: WorkflowStatus | undefined;
let pollTimer: ReturnType<typeof setInterval> | undefined;

export function mount(container: HTMLElement): void {
  container.innerHTML = `
    <div class="section">
      <h2>New Workflow</h2>
      <form id="wf-create" class="create-form">
        <div class="form-row">
          <label for="wf-premise">Premise</label>
          <textarea id="wf-premise" rows="2" placeholder="A house at the edge of town..." required></textarea>
        </div>
        <div class="form-row-inline">
          <div class="form-field">
            <label for="wf-voice">Voice</label>
            <select id="wf-voice" required>
              <option value="">Loading...</option>
            </select>
          </div>
          <div class="form-field">
            <label for="wf-words">Word Count <span id="wf-words-val" class="muted">200</span></label>
            <input type="range" id="wf-words" min="100" max="400" step="50" value="200">
          </div>
          <div class="form-field">
            <label class="checkbox-label">
              <input type="checkbox" id="wf-images"> Generate images
            </label>
            <label class="checkbox-label">
              <input type="checkbox" id="wf-music"> Background music
            </label>
            <label class="checkbox-label">
              <input type="checkbox" id="wf-sfx"> Sound effects
            </label>
            <label class="checkbox-label">
              <input type="checkbox" id="wf-stitch"> Stitch video
            </label>
          </div>
          <div class="form-field form-field-btn">
            <button type="submit" class="btn" id="wf-submit">Create</button>
          </div>
        </div>
        <div id="wf-create-error" class="error" style="display:none"></div>
      </form>
    </div>
    <div class="toolbar">
      <div id="wf-filters" class="filter-row"></div>
    </div>
    <div id="wf-list" class="list"></div>
  `;

  // Load voices
  loadVoices();

  // Word count slider label
  const slider = document.getElementById("wf-words") as HTMLInputElement;
  const valLabel = document.getElementById("wf-words-val")!;
  slider.addEventListener("input", () => {
    valLabel.textContent = slider.value;
  });

  // Form submit
  const form = document.getElementById("wf-create") as HTMLFormElement;
  form.addEventListener("submit", handleCreate);

  // Filters
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

async function loadVoices(): Promise<void> {
  const select = document.getElementById("wf-voice") as HTMLSelectElement;
  try {
    const voices: VoiceResponse[] = await fetchVoices();
    select.innerHTML = voices
      .map((v) => `<option value="${esc(v.name)}"${v.is_default ? " selected" : ""}>${esc(v.name)}</option>`)
      .join("");
    if (voices.length === 0) {
      select.innerHTML = '<option value="">No voices available</option>';
    }
  } catch {
    select.innerHTML = '<option value="">Failed to load voices</option>';
  }
}

async function handleCreate(e: Event): Promise<void> {
  e.preventDefault();
  const premise = (document.getElementById("wf-premise") as HTMLTextAreaElement).value.trim();
  const voice = (document.getElementById("wf-voice") as HTMLSelectElement).value;
  const words = parseInt((document.getElementById("wf-words") as HTMLInputElement).value, 10);
  const generateImages = (document.getElementById("wf-images") as HTMLInputElement).checked;
  const generateMusic = (document.getElementById("wf-music") as HTMLInputElement).checked;
  const generateSfx = (document.getElementById("wf-sfx") as HTMLInputElement).checked;
  const stitchVideo = (document.getElementById("wf-stitch") as HTMLInputElement).checked;
  const errEl = document.getElementById("wf-create-error")!;
  const btn = document.getElementById("wf-submit") as HTMLButtonElement;

  if (!premise || !voice) return;

  btn.disabled = true;
  btn.textContent = "Creating...";
  errEl.style.display = "none";

  try {
    const wf = await createWorkflow({
      premise,
      voice_name: voice,
      story_params: { target_word_count: words },
      image_params: { enabled: generateImages },
      music_params: { enabled: generateMusic },
      generate_sfx: generateSfx,
      stitch_params: { enabled: stitchVideo },
    });
    // Navigate to the new workflow
    location.hash = `#/workflow/${wf.id}`;
  } catch (err) {
    errEl.textContent = String(err);
    errEl.style.display = "block";
    btn.disabled = false;
    btn.textContent = "Create";
  }
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
