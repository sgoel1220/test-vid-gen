var __defProp = Object.defineProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};

// src/pages/workflows.ts
var workflows_exports = {};
__export(workflows_exports, {
  mount: () => mount,
  unmount: () => unmount
});

// src/api.ts
async function api(path, init) {
  const res = await fetch(path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) return void 0;
  return res.json();
}
function fetchWorkflows(status, limit = 20) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  params.set("limit", String(limit));
  return api(`/api/workflows?${params}`);
}
function fetchWorkflowDetail(id) {
  return api(`/api/workflows/${id}`);
}
function retryWorkflow(id) {
  return api(`/api/workflows/${id}/retry`, { method: "POST" });
}
function cancelWorkflow(id) {
  return api(`/api/workflows/${id}`, { method: "DELETE" });
}
function pauseWorkflow(id) {
  return api(`/api/workflows/${id}/pause`, { method: "POST" });
}
function resumeWorkflow(id) {
  return api(`/api/workflows/${id}/resume`, { method: "POST" });
}
function retryTtsStep(id) {
  return api(`/api/workflows/${id}/retry-step`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step_name: "tts_synthesis" })
  });
}
function retryChunks(id, chunkIndices) {
  return api(`/api/workflows/${id}/retry-chunks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chunk_indices: chunkIndices ?? null })
  });
}
function encodeToMp3(id) {
  return api(`/api/workflows/${id}/encode-mp3`, { method: "POST" });
}
function fetchCostSummary() {
  return api("/api/costs/summary");
}
function fetchWorkflowCost(id) {
  return api(`/api/costs/workflow/${id}`);
}
function fetchPipelineSchema() {
  return api("/api/workflows/schema");
}
function createWorkflow(req) {
  return api("/api/workflows", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req)
  });
}
function fetchVoices() {
  return api("/api/voices");
}
function fetchStoryByWorkflow(workflowId2) {
  return api(`/api/stories/by-workflow/${workflowId2}`);
}
function updateStory(storyId, fullText) {
  return api(`/api/stories/${storyId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ full_text: fullText })
  });
}
function fetchWorkflowLogs(id) {
  return api(`/api/workflows/${id}/logs`);
}
function forkWorkflow(id, fromStep) {
  return api(`/api/workflows/${id}/fork`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from_step: fromStep })
  });
}

// src/utils.ts
function formatCost(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}
function timeAgo(iso) {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1e3);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  return `${d}d ago`;
}
function duration(start, end) {
  const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime();
  const sec = Math.max(0, Math.floor(ms / 1e3));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (min < 60) return `${min}m ${remSec}s`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hr}h ${remMin}m`;
}
function shortId(id) {
  return id.slice(0, 8);
}
function formatStep(step) {
  return step.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
function statusClass(status) {
  switch (status) {
    case "completed":
    case "ready":
      return "ok";
    case "running":
    case "processing":
    case "creating":
      return "active";
    case "paused":
    case "pending":
    case "stopped":
      return "warn";
    case "failed":
    case "error":
    case "cancelled":
      return "err";
    default:
      return "muted";
  }
}
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// src/dom.ts
function patchHTML(target, newHTML) {
  const template = document.createElement("template");
  template.innerHTML = newHTML;
  const newNodes = template.content;
  if (!target.firstChild) {
    target.innerHTML = newHTML;
    return;
  }
  reconcileChildren(target, newNodes);
}
function reconcileChildren(parent, newContent) {
  const oldChildren = Array.from(parent.childNodes);
  const newChildren = Array.from(newContent.childNodes);
  const max = Math.max(oldChildren.length, newChildren.length);
  for (let i = 0; i < max; i++) {
    const oldChild = oldChildren[i];
    const newChild = newChildren[i];
    if (!oldChild && newChild) {
      parent.appendChild(newChild.cloneNode(true));
      continue;
    }
    if (oldChild && !newChild) {
      parent.removeChild(oldChild);
      continue;
    }
    if (!oldChild || !newChild) continue;
    if (oldChild.nodeType !== newChild.nodeType) {
      parent.replaceChild(newChild.cloneNode(true), oldChild);
      continue;
    }
    if (oldChild.nodeType === Node.TEXT_NODE) {
      if (oldChild.textContent !== newChild.textContent) {
        oldChild.textContent = newChild.textContent;
      }
      continue;
    }
    if (oldChild.nodeType === Node.ELEMENT_NODE) {
      const oldEl = oldChild;
      const newEl = newChild;
      if (oldEl.tagName !== newEl.tagName) {
        parent.replaceChild(newEl.cloneNode(true), oldEl);
        continue;
      }
      if (oldEl.tagName === "AUDIO" && !oldEl.paused) {
        continue;
      }
      if (oldEl === document.activeElement) {
        continue;
      }
      if (oldEl.classList.contains("section")) {
        if (oldEl.innerHTML === newEl.innerHTML) {
          continue;
        }
      }
      patchAttributes(oldEl, newEl);
      const frag = document.createDocumentFragment();
      while (newEl.firstChild) frag.appendChild(newEl.firstChild);
      reconcileChildren(oldEl, frag);
    }
  }
  while (parent.childNodes.length > newChildren.length) {
    parent.removeChild(parent.lastChild);
  }
}
function patchAttributes(oldEl, newEl) {
  for (const attr of Array.from(oldEl.attributes)) {
    if (!newEl.hasAttribute(attr.name)) {
      oldEl.removeAttribute(attr.name);
    }
  }
  for (const attr of Array.from(newEl.attributes)) {
    if (oldEl.getAttribute(attr.name) !== attr.value) {
      oldEl.setAttribute(attr.name, attr.value);
    }
  }
}

// src/schema-form.ts
function renderStepSections(steps) {
  return steps.map(renderStep).join("");
}
function renderStep(entry) {
  const schema = entry.json_schema;
  const props = schema.properties ?? {};
  const enabledProp = props["enabled"];
  const alwaysOn = enabledProp?.const === true;
  const defaultEnabled = alwaysOn || Boolean(enabledProp?.default);
  const title = formatStep(entry.step_name);
  const fieldId = `step-${entry.params_field}`;
  const sortedEntries = Object.entries(props).filter(([k]) => k !== "enabled").sort(([aKey, a], [bKey, b]) => {
    const aOrder = a["x-ui"]?.order ?? 0;
    const bOrder = b["x-ui"]?.order ?? 0;
    if (aOrder !== bOrder) return aOrder - bOrder;
    return aKey.localeCompare(bKey);
  });
  const groups = /* @__PURE__ */ new Map();
  for (const entry2 of sortedEntries) {
    const group = entry2[1]["x-ui"]?.group ?? "";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(entry2);
  }
  let fields = "";
  for (const [group, entries] of groups) {
    if (group) {
      fields += `<div class="field-group"><div class="field-group-label">${esc(formatStep(group))}</div>`;
    }
    for (const [k, p] of entries) {
      fields += renderField(fieldId, k, p);
    }
    if (group) fields += `</div>`;
  }
  if (!fields) {
    if (alwaysOn) return "";
    return `
      <div class="step-section" data-params-field="${esc(entry.params_field)}">
        <label class="checkbox-label step-toggle">
          <input type="checkbox" class="step-enabled" ${defaultEnabled ? "checked" : ""}>
          ${esc(title)}
        </label>
      </div>`;
  }
  const toggle = alwaysOn ? "" : `<input type="checkbox" class="step-enabled" ${defaultEnabled ? "checked" : ""}>`;
  return `
    <details class="step-section" data-params-field="${esc(entry.params_field)}" ${defaultEnabled ? "open" : ""}>
      <summary class="step-summary">${toggle}<span class="step-title">${esc(title)}</span></summary>
      <div class="step-fields">${fields}</div>
    </details>`;
}
function renderField(parentId, key, prop) {
  const id = `${parentId}-${key}`;
  const label = prop.title ?? formatStep(key);
  if (prop.type === "integer" && prop.minimum != null && prop.maximum != null) {
    const val2 = prop.default ?? prop.minimum;
    const step = prop.multipleOf ?? 1;
    return `
      <div class="form-field">
        <label for="${id}">${esc(label)} <span class="muted range-val">${val2}</span></label>
        <input type="range" id="${id}" data-key="${esc(key)}" data-type="integer"
               min="${prop.minimum}" max="${prop.maximum}" step="${step}" value="${val2}">
      </div>`;
  }
  if (prop.type === "boolean") {
    return `
      <div class="form-field">
        <label class="checkbox-label">
          <input type="checkbox" id="${id}" data-key="${esc(key)}" data-type="boolean" ${prop.default ? "checked" : ""}>
          ${esc(label)}
        </label>
      </div>`;
  }
  if (prop.type === "string" && prop.enum) {
    const opts = prop.enum.map((v) => `<option value="${esc(v)}"${v === prop.default ? " selected" : ""}>${esc(v)}</option>`).join("");
    return `
      <div class="form-field">
        <label for="${id}">${esc(label)}</label>
        <select id="${id}" data-key="${esc(key)}" data-type="string">${opts}</select>
      </div>`;
  }
  if (prop.type === "number" && prop.minimum != null && prop.maximum != null) {
    const val2 = prop.default ?? prop.minimum;
    const step = prop.multipleOf ?? 0.1;
    return `
      <div class="form-field">
        <label for="${id}">${esc(label)} <span class="muted range-val">${val2}</span></label>
        <input type="range" id="${id}" data-key="${esc(key)}" data-type="number"
               min="${prop.minimum}" max="${prop.maximum}" step="${step}" value="${val2}">
      </div>`;
  }
  const val = prop.default != null ? String(prop.default) : "";
  return `
    <div class="form-field">
      <label for="${id}">${esc(label)}</label>
      <input type="text" id="${id}" data-key="${esc(key)}" data-type="string" value="${esc(val)}">
    </div>`;
}
function collectParams(container) {
  const result = {};
  for (const section of container.querySelectorAll(".step-section")) {
    const field = section.dataset.paramsField;
    if (!field) continue;
    const params = {};
    const enabledCb = section.querySelector(".step-enabled");
    if (enabledCb) params["enabled"] = enabledCb.checked;
    for (const el of section.querySelectorAll("[data-key]")) {
      const key = el.dataset.key;
      const dtype = el.dataset.type;
      if (el instanceof HTMLInputElement) {
        if (dtype === "boolean") params[key] = el.checked;
        else if (dtype === "integer") params[key] = parseInt(el.value, 10);
        else if (dtype === "number") params[key] = parseFloat(el.value);
        else params[key] = el.value;
      } else if (el instanceof HTMLSelectElement) {
        params[key] = el.value;
      }
    }
    result[field] = params;
  }
  return result;
}
function bindSliderLabels(container) {
  for (const input of container.querySelectorAll('input[type="range"]')) {
    const label = input.parentElement?.querySelector(".range-val");
    if (label) {
      input.addEventListener("input", () => {
        label.textContent = input.value;
      });
    }
  }
}

// src/pages/workflows.ts
var STATUSES = [
  "all",
  "running",
  "paused",
  "completed",
  "failed",
  "cancelled"
];
var currentFilter;
var pollTimer;
var schemaReady = false;
function mount(container) {
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
  const form = document.getElementById("wf-create");
  form.addEventListener("submit", handleCreate);
  const filtersEl = document.getElementById("wf-filters");
  for (const s of STATUSES) {
    const btn = document.createElement("button");
    btn.textContent = s === "all" ? "All" : s.charAt(0).toUpperCase() + s.slice(1);
    btn.className = s === "all" ? "filter-btn active" : "filter-btn";
    btn.addEventListener("click", () => {
      currentFilter = s === "all" ? void 0 : s;
      filtersEl.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      refresh();
    });
    filtersEl.appendChild(btn);
  }
  refresh();
  pollTimer = setInterval(refresh, 5e3);
}
function unmount() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = void 0;
}
async function loadVoices() {
  const select = document.getElementById("wf-voice");
  try {
    const voices = await fetchVoices();
    select.innerHTML = voices.map((v) => `<option value="${esc(v.name)}"${v.is_default ? " selected" : ""}>${esc(v.name)}</option>`).join("");
    if (voices.length === 0) {
      select.innerHTML = '<option value="">No voices available</option>';
    }
  } catch {
    select.innerHTML = '<option value="">Failed to load voices</option>';
  }
}
async function loadSchema() {
  const el = document.getElementById("wf-step-params");
  const btn = document.getElementById("wf-submit");
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
async function handleCreate(e) {
  e.preventDefault();
  const premise = document.getElementById("wf-premise").value.trim();
  const voice = document.getElementById("wf-voice").value;
  const errEl = document.getElementById("wf-create-error");
  const btn = document.getElementById("wf-submit");
  if (!premise || !voice || !schemaReady) return;
  btn.disabled = true;
  btn.textContent = "Creating...";
  errEl.style.display = "none";
  try {
    const paramsEl = document.getElementById("wf-step-params");
    const stepParams = collectParams(paramsEl);
    const storyP = stepParams["story_params"];
    const imageP = stepParams["image_params"];
    const stitchP = stepParams["stitch_params"];
    const sfxP = stepParams["sfx_params"];
    const req = {
      premise,
      voice_name: voice,
      ...stepParams,
      target_word_count: storyP?.["target_word_count"],
      generate_images: imageP?.["enabled"],
      stitch_video: stitchP?.["enabled"],
      generate_sfx: sfxP?.["enabled"]
    };
    const wf = await createWorkflow(req);
    location.hash = `#/workflow/${wf.id}`;
  } catch (err) {
    errEl.textContent = String(err);
    errEl.style.display = "block";
    btn.disabled = false;
    btn.textContent = "Create";
  }
}
async function refresh() {
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
function renderRow(wf) {
  const cls = statusClass(wf.status);
  const step = wf.current_step ? formatStep(wf.current_step) : "";
  const age = timeAgo(wf.created_at);
  let timing = age;
  if (wf.status === "running" && wf.started_at) {
    timing = `running ${duration(wf.started_at)}`;
  } else if (wf.status === "completed" && wf.started_at && wf.completed_at) {
    timing = `took ${duration(wf.started_at, wf.completed_at)}`;
  }
  const errorSnippet = wf.error ? `<span class="row-error">${esc(wf.error.slice(0, 80))}</span>` : "";
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

// src/pages/workflow-detail.ts
var workflow_detail_exports = {};
__export(workflow_detail_exports, {
  mount: () => mount2,
  unmount: () => unmount2
});
var pollTimer2;
var workflowId = "";
var actionInFlight = false;
var chunkPage = 0;
var CHUNKS_PER_PAGE = 20;
var storyData = null;
var storyDirty = false;
var storySaving = false;
var logEntries = [];
var logsExpanded = false;
var workflowCost = null;
function mount2(container, id) {
  workflowId = id;
  storyData = null;
  storyDirty = false;
  storySaving = false;
  logEntries = [];
  logsExpanded = false;
  workflowCost = null;
  container.innerHTML = `
    <div class="toolbar">
      <a href="#/workflows" class="back-link">&larr; Workflows</a>
      <span id="wd-actions"></span>
    </div>
    <div id="wd-content">Loading...</div>
  `;
  refresh2();
  pollTimer2 = setInterval(refresh2, 5e3);
}
function unmount2() {
  if (pollTimer2) clearInterval(pollTimer2);
  pollTimer2 = void 0;
}
var TERMINAL_STATUSES = /* @__PURE__ */ new Set(["completed", "failed", "cancelled"]);
function isAudioPlaying() {
  return Array.from(document.querySelectorAll("audio.chunk-audio")).some((a) => !a.paused);
}
async function refresh2() {
  if (isAudioPlaying()) return;
  const el = document.getElementById("wd-content");
  if (!el) return;
  try {
    const [wf, logs, cost] = await Promise.all([
      fetchWorkflowDetail(workflowId),
      fetchWorkflowLogs(workflowId).catch(() => logEntries),
      fetchWorkflowCost(workflowId).catch(() => null)
    ]);
    logEntries = logs;
    workflowCost = cost;
    if (TERMINAL_STATUSES.has(wf.status) && pollTimer2) {
      clearInterval(pollTimer2);
      pollTimer2 = void 0;
    }
    const storyStep = wf.steps.find((s) => s.step_name === "generate_story");
    if (storyStep?.status === "completed" && !storyDirty) {
      try {
        storyData = await fetchStoryByWorkflow(workflowId);
      } catch {
        storyData = null;
      }
    }
    patchHTML(el, renderDetail(wf));
    renderActions(wf);
    attachActionListeners(wf);
    attachChunkListeners();
    attachStoryListeners(wf);
    attachLogListeners();
  } catch (err) {
    el.innerHTML = `<div class="error">Failed: ${esc(String(err))}</div>`;
  }
}
function renderActions(wf) {
  const el = document.getElementById("wd-actions");
  if (!el) return;
  const btns = [];
  if (wf.status === "failed") {
    btns.push('<button id="act-retry" class="btn">Retry</button>');
  }
  const hasFailedChunks = wf.chunks.some((c) => c.tts_status === "failed");
  if (hasFailedChunks && (wf.status === "failed" || wf.status === "cancelled" || wf.status === "completed")) {
    btns.push('<button id="act-retry-tts" class="btn">Retry TTS Step</button>');
  }
  const hasWavWithoutMp3 = wf.chunks.some(
    (c) => c.tts_audio_blob_id && !c.tts_mp3_blob_id
  );
  if (hasWavWithoutMp3) {
    btns.push('<button id="act-encode-mp3" class="btn">WAV\u2192MP3</button>');
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
  patchHTML(el, btns.join(" "));
}
async function runAction(fn, navigateToNew = false, label = "Working\u2026") {
  if (actionInFlight) return;
  actionInFlight = true;
  setActionsDisabled(true);
  showActionStatus(label);
  try {
    const result = await fn();
    if (navigateToNew && result && typeof result === "object" && "id" in result) {
      location.hash = `#/workflow/${result.id}`;
      return;
    }
    showActionStatus("");
    await refresh2();
  } catch (err) {
    showActionStatus(`Error: ${String(err)}`, true);
  } finally {
    actionInFlight = false;
    setActionsDisabled(false);
  }
}
function showActionStatus(msg, isError = false) {
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
function setActionsDisabled(disabled) {
  const el = document.getElementById("wd-actions");
  if (!el) return;
  el.querySelectorAll("button").forEach((b) => {
    b.disabled = disabled;
  });
}
function attachActionListeners(wf) {
  document.getElementById("act-retry")?.addEventListener("click", () => {
    runAction(() => retryWorkflow(wf.id), true);
  });
  document.getElementById("act-retry-tts")?.addEventListener("click", () => {
    runAction(() => retryTtsStep(wf.id), false, "Retrying TTS step\u2026");
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
      "Encoding WAV\u2192MP3\u2026"
    );
  });
  document.querySelectorAll(".step-fork-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const stepName = btn.dataset.stepName;
      if (!stepName) return;
      if (!confirm(`Fork from step "${formatStep(stepName)}"?

A new workflow will copy all prior data and re-run from this step onward.`)) return;
      runAction(
        () => forkWorkflow(wf.id, stepName).then((r) => {
          location.hash = `#/workflow/${r.workflow_id}`;
        }),
        false,
        `Forking from ${formatStep(stepName)}\u2026`
      );
    });
  });
}
function attachChunkListeners() {
  document.getElementById("chunk-prev")?.addEventListener("click", () => {
    chunkPage--;
    refresh2();
  });
  document.getElementById("chunk-next")?.addEventListener("click", () => {
    chunkPage++;
    refresh2();
  });
  document.querySelectorAll(".chunk-retry-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = parseInt(btn.dataset.chunkIndex ?? "", 10);
      if (!isNaN(idx)) {
        runAction(() => retryChunks(workflowId, [idx]), false, `Retrying chunk ${idx}\u2026`);
      }
    });
  });
  document.querySelectorAll(".chunk-row").forEach((row) => {
    row.style.cursor = "pointer";
    row.addEventListener("click", (e) => {
      if (e.target.closest("audio, button")) return;
      const idx = row.dataset.chunkIdx;
      const detail = document.querySelector(
        `tr[data-detail-for="${idx}"]`
      );
      if (detail) {
        detail.style.display = detail.style.display === "none" ? "" : "none";
      }
    });
  });
}
function attachStoryListeners(wf) {
  const textarea = document.getElementById("story-textarea");
  if (textarea) {
    textarea.addEventListener("input", () => {
      storyDirty = true;
    });
  }
  document.getElementById("story-save")?.addEventListener("click", async () => {
    if (!storyData || storySaving) return;
    const ta = document.getElementById("story-textarea");
    if (!ta) return;
    storySaving = true;
    try {
      storyData = await updateStory(storyData.id, ta.value);
      storyDirty = false;
      await refresh2();
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
      if (storyDirty && storyData) {
        const ta = document.getElementById("story-textarea");
        if (ta) {
          storyData = await updateStory(storyData.id, ta.value);
          storyDirty = false;
        }
      }
      await resumeWorkflow(wf.id);
      await refresh2();
    } catch (err) {
      alert(`Approve failed: ${err}`);
    } finally {
      storySaving = false;
    }
  });
}
function attachLogListeners() {
  document.getElementById("logs-toggle")?.addEventListener("click", () => {
    logsExpanded = !logsExpanded;
    const body = document.getElementById("logs-body");
    const btn = document.getElementById("logs-toggle");
    if (body) body.style.display = logsExpanded ? "" : "none";
    if (btn) btn.textContent = logsExpanded ? "\u25B2 Collapse" : "\u25BC Expand";
  });
}
function renderLogsSection() {
  if (logEntries.length === 0) return "";
  const LEVEL_CLASS = {
    ERROR: "log-error",
    WARNING: "log-warn",
    WARN: "log-warn",
    INFO: "log-info",
    DEBUG: "log-debug",
    CRITICAL: "log-error"
  };
  const rows = logEntries.map((e) => {
    const cls = LEVEL_CLASS[e.level] ?? "log-info";
    const ts = e.timestamp.slice(11, 19);
    const step = e.step ? `<span class="log-step">[${esc(e.step)}]</span> ` : "";
    return `<div class="log-line ${cls}"><span class="log-ts">${ts}</span> <span class="log-level">${esc(e.level)}</span> ${step}<span class="log-msg">${esc(e.message)}</span></div>`;
  }).join("");
  return `
    <div class="section">
      <h3>
        Logs (${logEntries.length})
        <button id="logs-toggle" class="btn btn-sm" style="margin-left:8px;">${logsExpanded ? "\u25B2 Collapse" : "\u25BC Expand"}</button>
      </h3>
      <div id="logs-body" style="display:${logsExpanded ? "" : "none"}">
        <div class="log-container">${rows}</div>
      </div>
    </div>
  `;
}
function renderDetail(wf) {
  const cls = statusClass(wf.status);
  const parts = [];
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
        ${workflowCost ? `
        <span>Cost</span>
        <span title="GPU: ${formatCost(workflowCost.gpu_cost_cents)} \xB7 LLM: ${formatCost(workflowCost.llm_cost_cents)}">${formatCost(workflowCost.total_cost_cents)}</span>` : ""}
      </div>
    </div>
  `);
  if (wf.input) {
    parts.push(`
      <div class="section">
        <h3>Input</h3>
        <div class="meta-grid">
          <span>Premise</span><span>${esc(wf.input.premise)}</span>
          <span>Voice</span><span>${esc(wf.input.voice_name)}</span>
          <span>Images</span><span>${wf.input.generate_images ? "Yes" : "No"}</span>
          <span>Video</span><span>${wf.input.stitch_video ? "Yes" : "No"}</span>
          <span>Revisions</span><span>${wf.input.story_params?.max_revisions ?? wf.input.max_revisions}</span>
          <span>Target Words</span><span>${(wf.input.story_params?.target_word_count ?? wf.input.target_word_count).toLocaleString()}</span>
        </div>
      </div>
    `);
  }
  if (wf.steps.length > 0) {
    const latestByStep = /* @__PURE__ */ new Map();
    for (const s of wf.steps) {
      const prev = latestByStep.get(s.step_name);
      if (!prev || s.attempt_number > prev.attempt_number) latestByStep.set(s.step_name, s);
    }
    const canFork = TERMINAL_STATUSES.has(wf.status);
    const rows = [...latestByStep.values()].map((s) => {
      const sc = statusClass(s.status);
      const t = s.started_at ? duration(s.started_at, s.completed_at) : "-";
      const forkBtn = canFork && s.status === "completed" && s.step_name !== "cleanup_gpu_pod" ? `<button class="btn btn-sm step-fork-btn" data-step-name="${s.step_name}" title="Fork from this step">Fork \u21AA</button>` : "";
      return `<tr>
        <td>${formatStep(s.step_name)}</td>
        <td><span class="badge ${sc}">${s.status}</span></td>
        <td>${s.attempt_number}</td>
        <td>${t}</td>
        <td class="text-err">${s.error ? esc(s.error.slice(0, 60)) : ""}</td>
        <td>${forkBtn}</td>
      </tr>`;
    }).join("");
    parts.push(`
      <div class="section">
        <h3>Steps</h3>
        <table>
          <thead><tr><th>Step</th><th>Status</th><th>Attempt</th><th>Time</th><th>Error</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `);
  }
  const logsHtml = renderLogsSection();
  if (logsHtml) parts.push(logsHtml);
  if (storyData) {
    parts.push(renderStorySection(wf));
  }
  if (wf.scenes.length > 0) {
    const rows = wf.scenes.map((s) => {
      const sc = statusClass(s.image_status);
      const textPreview = s.combined_text.length > 300 ? esc(s.combined_text.slice(0, 300)) + "&hellip;" : esc(s.combined_text);
      const promptPreview = s.image_prompt ? s.image_prompt.length > 200 ? esc(s.image_prompt.slice(0, 200)) + "&hellip;" : esc(s.image_prompt) : '<span class="muted">-</span>';
      const img = s.image_blob_id ? `<img src="/api/blobs/${s.image_blob_id}" class="scene-thumb" alt="scene ${s.scene_index}" loading="lazy">` : '<span class="muted">-</span>';
      return `<tr>
        <td class="scene-combined-text">${textPreview}</td>
        <td class="scene-prompt-text">${promptPreview}</td>
        <td class="scene-image-cell">${img}</td>
        <td><span class="badge ${sc}">${s.image_status}</span></td>
      </tr>`;
    }).join("");
    parts.push(`
      <div class="section">
        <h3>Scenes (${wf.scenes.length})</h3>
        <table class="scenes-table">
          <thead><tr><th>Chunk Text</th><th>Image Prompt</th><th>Image</th><th>Status</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `);
  }
  if (wf.sfx_clips && wf.sfx_clips.length > 0) {
    const sfxRows = wf.sfx_clips.map((clip) => {
      const audioUrl = `/api/blobs/${clip.blob_id}`;
      const promptPreview = clip.description.length > 120 ? esc(clip.description.slice(0, 120)) + "&hellip;" : esc(clip.description);
      return `<tr>
        <td>${clip.scene_index}</td>
        <td>${clip.cue_index}</td>
        <td>${esc(clip.position)}</td>
        <td>${clip.duration_sec.toFixed(1)}s</td>
        <td class="scene-prompt-text" title="${esc(clip.description)}">${promptPreview}</td>
        <td><audio class="chunk-audio" controls preload="none" src="${audioUrl}"></audio><a class="dl-link" href="${audioUrl}" download="sfx-${clip.scene_index}-${clip.cue_index}.wav" title="Download">\u2B07</a></td>
      </tr>`;
    }).join("");
    parts.push(`
      <div class="section">
        <h3>SFX Clips (${wf.sfx_clips.length})</h3>
        <table>
          <thead><tr><th>Scene</th><th>Cue</th><th>Position</th><th>Duration</th><th>Prompt</th><th>Audio</th></tr></thead>
          <tbody>${sfxRows}</tbody>
        </table>
      </div>
    `);
  }
  if (wf.chunks.length > 0) {
    const totalPages = Math.ceil(wf.chunks.length / CHUNKS_PER_PAGE);
    if (chunkPage >= totalPages) chunkPage = totalPages - 1;
    if (chunkPage < 0) chunkPage = 0;
    const start = chunkPage * CHUNKS_PER_PAGE;
    const pageChunks = wf.chunks.slice(start, start + CHUNKS_PER_PAGE);
    const rows = pageChunks.map((c) => {
      const sc = statusClass(c.tts_status);
      const dur = c.tts_duration_sec != null ? `${c.tts_duration_sec.toFixed(1)}s` : "-";
      const textPreview = c.chunk_text.length > 200 ? esc(c.chunk_text.slice(0, 200)) + "&hellip;" : esc(c.chunk_text);
      const mp3Row = c.tts_mp3_blob_id ? `<div class="audio-row"><span class="audio-label">MP3</span><audio class="chunk-audio" controls preload="none" src="/api/blobs/${c.tts_mp3_blob_id}"></audio><a class="dl-link" href="/api/blobs/${c.tts_mp3_blob_id}" download="chunk-${c.chunk_index}.mp3" title="Download MP3">\u2B07</a></div>` : "";
      const wavRow = c.tts_audio_blob_id ? `<div class="audio-row"><span class="audio-label">WAV</span><audio class="chunk-audio" controls preload="none" src="/api/blobs/${c.tts_audio_blob_id}"></audio><a class="dl-link" href="/api/blobs/${c.tts_audio_blob_id}" download="chunk-${c.chunk_index}.wav" title="Download WAV">\u2B07</a></div>` : "";
      const audio = mp3Row || wavRow ? `<div class="audio-cell">${mp3Row}${wavRow}</div>` : '<span class="muted">-</span>';
      const completed = c.tts_completed_at ? timeAgo(c.tts_completed_at) : "-";
      const canRetryChunk = c.tts_status === "failed" && (wf.status === "failed" || wf.status === "cancelled" || wf.status === "completed");
      const retryBtn = canRetryChunk ? `<button class="btn btn-sm chunk-retry-btn" data-chunk-index="${c.chunk_index}" title="Retry this chunk">\u21BA</button>` : "";
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
    const pagination = totalPages > 1 ? `<div class="pagination">
          <button class="btn" id="chunk-prev" ${chunkPage === 0 ? "disabled" : ""}>Prev</button>
          <span class="pagination-info">Page ${chunkPage + 1} of ${totalPages}</span>
          <button class="btn" id="chunk-next" ${chunkPage >= totalPages - 1 ? "disabled" : ""}>Next</button>
        </div>` : "";
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
  {
    const outputParts = [];
    const r = wf.result;
    if (r?.final_audio_blob_id) {
      const audioUrl = `/api/blobs/${r.final_audio_blob_id}`;
      outputParts.push(`
        <div class="output-block">
          <h4>Audio</h4>
          <audio controls preload="metadata" style="width:100%">
            <source src="${audioUrl}" type="audio/mpeg">
          </audio>
          <div class="output-meta">
            ${r.total_duration_sec != null ? `<span>${duration(r.total_duration_sec)}</span>` : ""}
            ${r.chunk_count != null ? `<span>${r.chunk_count} chunks</span>` : ""}
            <a href="${audioUrl}" download="audio.mp3" class="btn btn-sm">Download MP3</a>
          </div>
        </div>
      `);
    }
    if (r?.final_video_blob_id) {
      const videoUrl = `/api/blobs/${r.final_video_blob_id}`;
      outputParts.push(`
        <div class="output-block">
          <h4>Video (Clean)</h4>
          <video controls preload="metadata" style="width:100%">
            <source src="${videoUrl}" type="video/mp4">
          </video>
          <div class="output-meta">
            <a href="${videoUrl}" download="video.mp4" class="btn btn-sm">Download MP4</a>
          </div>
        </div>
      `);
    }
    if (r?.waveform_video_blob_id) {
      const waveformUrl = `/api/blobs/${r.waveform_video_blob_id}`;
      outputParts.push(`
        <div class="output-block">
          <h4>Video (Waveform Overlay)</h4>
          <video controls preload="metadata" style="width:100%">
            <source src="${waveformUrl}" type="video/mp4">
          </video>
          <div class="output-meta">
            <a href="${waveformUrl}" download="video-waveform.mp4" class="btn btn-sm">Download MP4</a>
          </div>
        </div>
      `);
    }
    if (wf.music_bed_blob_id) {
      const musicUrl = `/api/blobs/${wf.music_bed_blob_id}`;
      const segRows = (wf.music_segments ?? []).map((seg) => {
        const segUrl = `/api/blobs/${seg.blob_id}`;
        const intensityBar = seg.intensity > 0 ? `<span class="music-intensity" title="Intensity ${seg.intensity}/10">${"\u2588".repeat(seg.intensity)}${"\u2591".repeat(10 - seg.intensity)} ${seg.intensity}/10</span>` : "";
        const promptText = seg.prompt ? esc(seg.prompt) : '<span class="muted">\u2014</span>';
        return `<tr>
          <td>${seg.scene_index}</td>
          <td class="music-prompt-cell">${promptText}</td>
          <td>${intensityBar}</td>
          <td>${seg.duration_sec.toFixed(1)}s</td>
          <td><audio class="chunk-audio" controls preload="none" src="${segUrl}"></audio><a class="dl-link" href="${segUrl}" download="music-scene-${seg.scene_index}.wav" title="Download">\u2B07</a></td>
        </tr>`;
      }).join("");
      const segTable = segRows ? `<table class="music-segments-table">
            <thead><tr><th>Scene</th><th>Prompt</th><th>Intensity</th><th>Duration</th><th>Audio</th></tr></thead>
            <tbody>${segRows}</tbody>
          </table>` : "";
      outputParts.push(`
        <div class="output-block">
          <h4>Music Bed</h4>
          <audio controls preload="metadata" style="width:100%">
            <source src="${musicUrl}" type="audio/wav">
          </audio>
          <div class="output-meta">
            <a href="${musicUrl}" download="music-bed.wav" class="btn btn-sm">Download WAV</a>
          </div>
          ${segTable}
        </div>
      `);
    }
    if (outputParts.length > 0) {
      parts.push(`
        <div class="section">
          <h3>Output</h3>
          ${outputParts.join("")}
        </div>
      `);
    }
  }
  return parts.join("");
}
function renderStorySection(wf) {
  if (!storyData) return "";
  const isPaused = wf.status === "paused";
  const text = storyData.full_text ?? "";
  const wordCount = storyData.word_count ?? 0;
  const title = storyData.title ? esc(storyData.title) : "Untitled";
  if (isPaused) {
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
  return `
    <div class="section">
      <h3>Story: ${title} <span class="muted">(${wordCount} words)</span></h3>
      <div class="story-text">${esc(text)}</div>
    </div>
  `;
}

// src/pages/gpu-pods.ts
var gpu_pods_exports = {};
__export(gpu_pods_exports, {
  mount: () => mount3,
  unmount: () => unmount3
});
var pollTimer3;
function mount3(container) {
  container.innerHTML = `
    <div id="gp-summary" class="summary-row">Loading costs...</div>
    <h3>Active Pods</h3>
    <div id="gp-active" class="list"></div>
    <h3>Terminated Pods</h3>
    <div id="gp-terminated" class="list"></div>
  `;
  refresh3();
  pollTimer3 = setInterval(refresh3, 1e4);
}
function unmount3() {
  if (pollTimer3) clearInterval(pollTimer3);
  pollTimer3 = void 0;
}
var TERMINAL = /* @__PURE__ */ new Set(["terminated", "error", "stopped"]);
function isActive(status) {
  return !TERMINAL.has(status);
}
async function refresh3() {
  const summaryEl = document.getElementById("gp-summary");
  const activeEl = document.getElementById("gp-active");
  const terminatedEl = document.getElementById("gp-terminated");
  if (!summaryEl || !activeEl || !terminatedEl) return;
  try {
    const costs = await fetchCostSummary();
    patchHTML(summaryEl, renderSummary(costs));
    const pods = await collectPods();
    const active = pods.filter((p) => isActive(p.status));
    const terminated = pods.filter((p) => !isActive(p.status));
    let activeHTML = "";
    if (costs.active_pod_count > active.length) {
      const missing = costs.active_pod_count - active.length;
      activeHTML += `<div class="error">Warning: ${missing} active pod(s) not shown (attached to older workflows or missing from recent results).</div>`;
    }
    activeHTML += active.length > 0 ? active.map(renderPod).join("") : '<div class="empty">No active pods.</div>';
    patchHTML(activeEl, activeHTML);
    patchHTML(terminatedEl, terminated.length > 0 ? terminated.map(renderPod).join("") : '<div class="empty">No terminated pods.</div>');
  } catch (err) {
    summaryEl.innerHTML = `<div class="error">${esc(String(err))}</div>`;
  }
}
async function collectPods() {
  const workflows = await fetchWorkflows(void 0, 20);
  const seen = /* @__PURE__ */ new Set();
  const pods = [];
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
function renderSummary(c) {
  return `
    <span>Active Pods: <strong>${c.active_pod_count}</strong></span>
    <span>Today: <strong>${formatCost(c.today_cents)}</strong></span>
    <span>This Month: <strong>${formatCost(c.month_cents)}</strong></span>
  `;
}
function renderPod(p) {
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

// src/pages/image-test.ts
var image_test_exports = {};
__export(image_test_exports, {
  mount: () => mount4,
  unmount: () => unmount4
});
var pollTimer4 = null;
function mount4(container) {
  container.innerHTML = `
    <div class="section">
      <h2>Image Generation</h2>
      <div class="create-form">
        <div class="form-row">
          <label>Prompt</label>
          <textarea id="img-prompt" rows="3" placeholder="impressionist painting of a dark forest at twilight, thick brushstrokes, moody atmosphere"></textarea>
        </div>
        <div class="form-row">
          <label>Negative Prompt</label>
          <input type="text" id="img-negative" value="photorealistic, photograph, blurry, low quality, watermark, text, deformed">
        </div>
        <div class="form-row-inline">
          <div class="form-field">
            <label>Width</label>
            <input type="text" id="img-width" value="1280">
          </div>
          <div class="form-field">
            <label>Height</label>
            <input type="text" id="img-height" value="720">
          </div>
          <div class="form-field">
            <label>Seed (0 = random)</label>
            <input type="text" id="img-seed" value="0">
          </div>
          <div class="form-field-btn">
            <button class="btn" id="img-generate">Generate</button>
          </div>
        </div>
      </div>
      <div id="img-status" class="muted" style="margin-bottom:0.5rem;font-size:0.85rem;"></div>
      <div id="img-output"></div>
    </div>

    <div class="section" style="margin-top:2rem;">
      <h2>Paste Response</h2>
      <p class="muted" style="font-size:0.8rem;margin-bottom:0.5rem;">Or paste a raw RunPod JSON response to render:</p>
      <div class="form-row">
        <textarea id="img-json" rows="5" placeholder='{"status":"COMPLETED","output":{"images":[...]}}'></textarea>
      </div>
      <button class="btn" id="img-render">Render</button>
      <div id="img-paste-output" style="margin-top:0.75rem;"></div>
    </div>
  `;
  document.getElementById("img-generate").addEventListener("click", handleGenerate);
  document.getElementById("img-render").addEventListener("click", handleRender);
}
function unmount4() {
  if (pollTimer4) {
    clearInterval(pollTimer4);
    pollTimer4 = null;
  }
}
async function handleGenerate() {
  const prompt = document.getElementById("img-prompt").value.trim();
  const negative = document.getElementById("img-negative").value.trim();
  const width = parseInt(document.getElementById("img-width").value) || 1280;
  const height = parseInt(document.getElementById("img-height").value) || 720;
  const seed = parseInt(document.getElementById("img-seed").value) || Math.floor(Math.random() * 2 ** 32);
  if (!prompt) {
    setStatus("Enter a prompt.", "text-err");
    return;
  }
  const workflow = buildWorkflow(prompt, negative, width, height, seed);
  const statusEl = document.getElementById("img-status");
  const outputEl = document.getElementById("img-output");
  setStatus("Submitting...");
  outputEl.innerHTML = "";
  try {
    const resp = await fetch("/api/image/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workflow })
    });
    if (!resp.ok) {
      const text = await resp.text();
      setStatus(`Error ${resp.status}: ${text}`, "text-err");
      return;
    }
    const data = await resp.json();
    if (data.status === "COMPLETED") {
      renderOutput(data, outputEl);
      setStatus("Done!");
    } else if (data.id) {
      setStatus(`Job ${data.id} \u2014 ${data.status}. Polling...`);
      startPolling(data.id, outputEl);
    } else {
      setStatus("Unexpected response");
      outputEl.innerHTML = `<pre class="code-block">${JSON.stringify(data, null, 2)}</pre>`;
    }
  } catch (err) {
    setStatus(`Network error: ${err}`, "text-err");
  }
}
function startPolling(jobId, outputEl) {
  if (pollTimer4) clearInterval(pollTimer4);
  pollTimer4 = setInterval(async () => {
    try {
      const resp = await fetch(`/api/image/status/${jobId}`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.status === "COMPLETED") {
        clearInterval(pollTimer4);
        pollTimer4 = null;
        renderOutput(data, outputEl);
        setStatus("Done!");
      } else if (data.status === "FAILED") {
        clearInterval(pollTimer4);
        pollTimer4 = null;
        setStatus(`Failed: ${data.error || "unknown"}`, "text-err");
        outputEl.innerHTML = `<pre class="code-block">${JSON.stringify(data, null, 2)}</pre>`;
      } else {
        setStatus(`Job ${jobId} \u2014 ${data.status}...`);
      }
    } catch {
    }
  }, 3e3);
}
function handleRender() {
  const raw = document.getElementById("img-json").value.trim();
  const outputEl = document.getElementById("img-paste-output");
  outputEl.innerHTML = "";
  if (!raw) {
    outputEl.innerHTML = '<p class="text-err">Paste JSON first.</p>';
    return;
  }
  let data;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    outputEl.innerHTML = `<p class="text-err">Invalid JSON: ${e.message}</p>`;
    return;
  }
  if (data.status && data.status !== "COMPLETED") {
    outputEl.innerHTML = `<p class="muted">Status: <strong>${data.status}</strong> \u2014 not completed.</p>`;
    return;
  }
  renderOutput(data, outputEl);
}
function renderOutput(data, el) {
  const images = extractImages(data);
  if (images.length === 0) {
    el.innerHTML = `<p class="text-err">No images found.</p><pre class="code-block" style="max-height:200px;overflow:auto;">${JSON.stringify(data.output || data, null, 2).slice(0, 1e3)}</pre>`;
    return;
  }
  el.innerHTML = images.map((b64, i) => {
    const src = b64.startsWith("data:") ? b64 : `data:image/png;base64,${b64}`;
    return `<img src="${src}" style="max-width:100%;border-radius:6px;border:1px solid #333;margin-bottom:0.5rem;" alt="Generated image ${i + 1}">`;
  }).join("");
}
function extractImages(data) {
  const images = [];
  const output = data.output || data;
  if (Array.isArray(output.images)) {
    for (const img of output.images) {
      if (typeof img === "string") images.push(img);
      else if (img.image) images.push(img.image);
    }
  }
  if (typeof output.message === "string" && output.message.length > 200) {
    images.push(output.message);
  }
  if (typeof output === "string" && output.length > 200) {
    images.push(output);
  }
  return images;
}
function buildWorkflow(prompt, negative, width, height, seed) {
  return {
    "3": { class_type: "KSampler", inputs: { seed, steps: 4, cfg: 2, sampler_name: "euler", scheduler: "sgm_uniform", denoise: 1, model: ["15", 0], positive: ["6", 0], negative: ["7", 0], latent_image: ["5", 0] } },
    "4": { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "sd_xl_base_1.0.safetensors" } },
    "5": { class_type: "EmptyLatentImage", inputs: { width, height, batch_size: 1 } },
    "6": { class_type: "CLIPTextEncode", inputs: { text: prompt, clip: ["14", 1] } },
    "7": { class_type: "CLIPTextEncode", inputs: { text: negative || "photorealistic, photograph, blurry, low quality, watermark, text, deformed", clip: ["14", 1] } },
    "8": { class_type: "VAEDecode", inputs: { samples: ["3", 0], vae: ["10", 0] } },
    "9": { class_type: "SaveImage", inputs: { filename_prefix: "output", images: ["8", 0] } },
    "10": { class_type: "VAELoader", inputs: { vae_name: "sdxl_vae_fp16_fix.safetensors" } },
    "14": { class_type: "LoraLoader", inputs: { lora_name: "impressionism_sdxl.safetensors", strength_model: 0.8, strength_clip: 0.8, model: ["4", 0], clip: ["4", 1] } },
    "15": { class_type: "LoraLoader", inputs: { lora_name: "sdxl_lightning_4step_lora.safetensors", strength_model: 1, strength_clip: 1, model: ["14", 0], clip: ["14", 1] } }
  };
}
function setStatus(msg, cls = "") {
  const el = document.getElementById("img-status");
  el.textContent = msg;
  el.className = cls || "muted";
}

// src/pages/settings.ts
var settings_exports = {};
__export(settings_exports, {
  mount: () => mount5,
  unmount: () => unmount5
});
function mount5(container) {
  container.innerHTML = `
    <div class="section">
      <h2>Settings</h2>
      <p class="muted">Coming soon.</p>
    </div>
  `;
}
function unmount5() {
}

// src/main.ts
var currentPage = null;
var routes = [
  { pattern: /^#\/workflows$/, page: workflows_exports, extractArgs: () => [] },
  { pattern: /^#\/workflow\/(.+)$/, page: workflow_detail_exports, extractArgs: (m) => [m[1]] },
  { pattern: /^#\/gpu-pods$/, page: gpu_pods_exports, extractArgs: () => [] },
  { pattern: /^#\/image-test$/, page: image_test_exports, extractArgs: () => [] },
  { pattern: /^#\/settings$/, page: settings_exports, extractArgs: () => [] }
];
function navigate() {
  const hash = location.hash || "#/workflows";
  const container = document.getElementById("app");
  if (currentPage) {
    currentPage.unmount();
    currentPage = null;
  }
  for (const route of routes) {
    const m = hash.match(route.pattern);
    if (m) {
      currentPage = route.page;
      route.page.mount(container, ...route.extractArgs(m));
      updateNav(hash);
      return;
    }
  }
  location.hash = "#/workflows";
}
function updateNav(hash) {
  document.querySelectorAll("nav a").forEach((a) => {
    const href = a.getAttribute("href") ?? "";
    const active = hash === href || hash.startsWith(href + "/") || href === "#/workflows" && hash.startsWith("#/workflow/");
    a.classList.toggle("active", active);
  });
}
window.addEventListener("hashchange", navigate);
document.addEventListener("DOMContentLoaded", navigate);
