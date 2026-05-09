// Workflow list page

import {
  fetchWorkflows, fetchVoices, createWorkflow, fetchPipelineSchema,
  type WorkflowStatus, type WorkflowResponse, type VoiceResponse,
  type CreateWorkflowRequest,
} from "../api.js";
import { shortId, timeAgo, duration, statusClass, formatStep, esc } from "../utils.js";
import { patchHTML } from "../dom.js";
import { renderStepSections, collectParams, bindSliderLabels } from "../schema-form.js";

const STATUSES: (WorkflowStatus | "all")[] = [
  "all", "running", "paused", "completed", "failed", "cancelled",
];

let currentFilter: WorkflowStatus | undefined;
let pollTimer: ReturnType<typeof setInterval> | undefined;
let schemaReady = false;

export function mount(container: HTMLElement): void {
  container.innerHTML = `
    <div class="section">
      <h2>New Workflow</h2>
      <form id="wf-create" class="create-form">
        <div class="form-row" id="wf-premise-row">
          <label for="wf-premise">Premise</label>
          <textarea id="wf-premise" rows="2" placeholder="A house at the edge of town..." required></textarea>
        </div>
        <div class="form-row">
          <label for="wf-story-mode">Story source</label>
          <select id="wf-story-mode">
            <option value="ai">AI Generated</option>
            <option value="manual">Write My Own</option>
          </select>
        </div>
        <div class="form-row" id="wf-manual-row" style="display:none">
          <label for="wf-manual-story">Story text</label>
          <textarea id="wf-manual-story" rows="12" placeholder="Paste or write your full story here..."></textarea>
        </div>
        <div class="form-row-inline">
          <div class="form-field">
            <label for="wf-voice">Voice</label>
            <select id="wf-voice" required>
              <option value="">Loading...</option>
            </select>
          </div>
          <div class="form-field form-field-btn">
            <button type="submit" class="btn" id="wf-submit" disabled>Create</button>
          </div>
        </div>
        <div id="wf-step-params" class="step-sections"></div>
        <div id="wf-create-error" class="error" style="display:none"></div>
      </form>
    </div>
    <div class="toolbar">
      <div id="wf-filters" class="filter-row"></div>
    </div>
    <div id="wf-list" class="list"></div>
  `;

  loadVoices();
  loadSchema();

  const form = document.getElementById("wf-create") as HTMLFormElement;
  form.addEventListener("submit", handleCreate);

  const storyModeEl = document.getElementById("wf-story-mode") as HTMLSelectElement;
  const manualRowEl = document.getElementById("wf-manual-row") as HTMLElement;
  const stepParamsEl = document.getElementById("wf-step-params") as HTMLElement;

  const premiseRowEl = document.getElementById("wf-premise-row") as HTMLElement;
  const premiseEl = document.getElementById("wf-premise") as HTMLTextAreaElement;

  storyModeEl.addEventListener("change", () => {
    const isManual = storyModeEl.value === "manual";
    manualRowEl.style.display = isManual ? "" : "none";
    premiseRowEl.style.display = isManual ? "none" : "";
    premiseEl.required = !isManual;
    const storySection = stepParamsEl.querySelector(".step-section[data-step='story_params']") as HTMLElement | null;
    if (storySection) storySection.style.display = isManual ? "none" : "";
  });

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

async function loadSchema(): Promise<void> {
  const el = document.getElementById("wf-step-params");
  const btn = document.getElementById("wf-submit") as HTMLButtonElement | null;
  if (!el) return;
  try {
    const schema = await fetchPipelineSchema();
    el.innerHTML = renderStepSections(schema.steps);
    bindSliderLabels(el);
    schemaReady = true;
    if (btn) btn.disabled = false;
  } catch {
    el.innerHTML = '<div class="error">Failed to load step config</div>';
  }
}

async function handleCreate(e: Event): Promise<void> {
  e.preventDefault();
  const premise = (document.getElementById("wf-premise") as HTMLTextAreaElement).value.trim();
  const voice = (document.getElementById("wf-voice") as HTMLSelectElement).value;
  const errEl = document.getElementById("wf-create-error")!;
  const btn = document.getElementById("wf-submit") as HTMLButtonElement;
  const storyMode = (document.getElementById("wf-story-mode") as HTMLSelectElement).value;
  const manualStoryText = storyMode === "manual"
    ? (document.getElementById("wf-manual-story") as HTMLTextAreaElement).value.trim()
    : undefined;

  if (storyMode !== "manual" && !premise) return;
  if (!voice || !schemaReady) return;
  if (storyMode === "manual" && !manualStoryText) return;

  btn.disabled = true;
  btn.textContent = "Creating...";
  errEl.style.display = "none";

  try {
    const paramsEl = document.getElementById("wf-step-params")!;
    const stepParams = collectParams(paramsEl);

    // Backfill legacy flat flags for backward compat with existing readers
    const storyP = stepParams["story_params"] as Record<string, unknown> | undefined;
    const imageP = stepParams["image_params"] as Record<string, unknown> | undefined;
    const stitchP = stepParams["stitch_params"] as Record<string, unknown> | undefined;
    const sfxP = stepParams["sfx_params"] as Record<string, unknown> | undefined;

    const effectivePremise = premise || (manualStoryText ? manualStoryText.slice(0, 120).split("\n")[0] : "");

    const req = {
      premise: effectivePremise,
      voice_name: voice,
      manual_story_text: manualStoryText,
      ...stepParams,
      target_word_count: storyP?.["target_word_count"] as number | undefined,
      generate_images: imageP?.["enabled"] as boolean | undefined,
      stitch_video: stitchP?.["enabled"] as boolean | undefined,
      generate_sfx: sfxP?.["enabled"] as boolean | undefined,
    } as CreateWorkflowRequest;
    const wf = await createWorkflow(req);
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
      patchHTML(list, '<div class="empty">No workflows found.</div>');
      return;
    }
    patchHTML(list, workflows.map(renderRow).join(""));
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
