// Schema-driven form renderer for pipeline step params

import type { StepParamSchemaEntry } from "./api.js";
import { formatStep, esc } from "./utils.js";

interface UIMetadata {
  expose?: boolean;
  group?: string;
  order?: number;
}

interface JsonSchemaProperty {
  type?: string;
  const?: unknown;
  default?: unknown;
  description?: string;
  minimum?: number;
  maximum?: number;
  multipleOf?: number;
  enum?: string[];
  title?: string;
  "x-ui"?: UIMetadata;
}

interface JsonSchema {
  properties?: Record<string, JsonSchemaProperty>;
}

/** Render collapsible step sections from schema entries. */
export function renderStepSections(steps: StepParamSchemaEntry[]): string {
  return steps.map(renderStep).join("");
}

function renderStep(entry: StepParamSchemaEntry): string {
  const schema = entry.json_schema as JsonSchema;
  const props = schema.properties ?? {};
  const enabledProp = props["enabled"];

  const alwaysOn = enabledProp?.const === true;
  const defaultEnabled = alwaysOn || Boolean(enabledProp?.default);
  const title = formatStep(entry.step_name);
  const fieldId = `step-${entry.params_field}`;

  // Sort fields by x-ui.order, then alphabetically
  const sortedEntries = Object.entries(props)
    .filter(([k]) => k !== "enabled")
    .sort(([aKey, a], [bKey, b]) => {
      const aOrder = a["x-ui"]?.order ?? 0;
      const bOrder = b["x-ui"]?.order ?? 0;
      if (aOrder !== bOrder) return aOrder - bOrder;
      return aKey.localeCompare(bKey);
    });

  // Group fields by x-ui.group
  const groups = new Map<string, [string, JsonSchemaProperty][]>();
  for (const entry of sortedEntries) {
    const group = entry[1]["x-ui"]?.group ?? "";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push(entry);
  }

  // Render grouped fields
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

  // Steps with only an enabled toggle and no extra fields
  if (!fields) {
    if (alwaysOn) return ""; // nothing to configure (e.g. TTS)
    return `
      <div class="step-section" data-params-field="${esc(entry.params_field)}">
        <label class="checkbox-label step-toggle">
          <input type="checkbox" class="step-enabled" ${defaultEnabled ? "checked" : ""}>
          ${esc(title)}
        </label>
      </div>`;
  }

  // Collapsible section with optional toggle
  const toggle = alwaysOn
    ? ""
    : `<input type="checkbox" class="step-enabled" ${defaultEnabled ? "checked" : ""}>`;

  return `
    <details class="step-section" data-params-field="${esc(entry.params_field)}" ${defaultEnabled ? "open" : ""}>
      <summary class="step-summary">${toggle}<span class="step-title">${esc(title)}</span></summary>
      <div class="step-fields">${fields}</div>
    </details>`;
}

function renderField(parentId: string, key: string, prop: JsonSchemaProperty): string {
  const id = `${parentId}-${key}`;
  const label = prop.title ?? formatStep(key);

  // Integer with min/max -> range slider
  if (prop.type === "integer" && prop.minimum != null && prop.maximum != null) {
    const val = prop.default ?? prop.minimum;
    const step = prop.multipleOf ?? 1;
    return `
      <div class="form-field">
        <label for="${id}">${esc(label)} <span class="muted range-val">${val}</span></label>
        <input type="range" id="${id}" data-key="${esc(key)}" data-type="integer"
               min="${prop.minimum}" max="${prop.maximum}" step="${step}" value="${val}">
      </div>`;
  }

  // Boolean -> checkbox
  if (prop.type === "boolean") {
    return `
      <div class="form-field">
        <label class="checkbox-label">
          <input type="checkbox" id="${id}" data-key="${esc(key)}" data-type="boolean" ${prop.default ? "checked" : ""}>
          ${esc(label)}
        </label>
      </div>`;
  }

  // String enum -> select
  if (prop.type === "string" && prop.enum) {
    const opts = prop.enum
      .map((v) => `<option value="${esc(v)}"${v === prop.default ? " selected" : ""}>${esc(v)}</option>`)
      .join("");
    return `
      <div class="form-field">
        <label for="${id}">${esc(label)}</label>
        <select id="${id}" data-key="${esc(key)}" data-type="string">${opts}</select>
      </div>`;
  }

  // Number (float) with min/max -> range slider
  if (prop.type === "number" && prop.minimum != null && prop.maximum != null) {
    const val = prop.default ?? prop.minimum;
    const step = prop.multipleOf ?? 0.1;
    return `
      <div class="form-field">
        <label for="${id}">${esc(label)} <span class="muted range-val">${val}</span></label>
        <input type="range" id="${id}" data-key="${esc(key)}" data-type="number"
               min="${prop.minimum}" max="${prop.maximum}" step="${step}" value="${val}">
      </div>`;
  }

  // Fallback: text input
  const val = prop.default != null ? String(prop.default) : "";
  return `
    <div class="form-field">
      <label for="${id}">${esc(label)}</label>
      <input type="text" id="${id}" data-key="${esc(key)}" data-type="string" value="${esc(val)}">
    </div>`;
}

/** Collect form values into a params object keyed by params_field name. */
export function collectParams(container: HTMLElement): Record<string, Record<string, unknown>> {
  const result: Record<string, Record<string, unknown>> = {};

  for (const section of container.querySelectorAll<HTMLElement>(".step-section")) {
    const field = section.dataset.paramsField;
    if (!field) continue;

    const params: Record<string, unknown> = {};

    const enabledCb = section.querySelector<HTMLInputElement>(".step-enabled");
    if (enabledCb) params["enabled"] = enabledCb.checked;

    for (const el of section.querySelectorAll<HTMLElement>("[data-key]")) {
      const key = el.dataset.key!;
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

/** Bind live-updating labels for range sliders. */
export function bindSliderLabels(container: HTMLElement): void {
  for (const input of container.querySelectorAll<HTMLInputElement>('input[type="range"]')) {
    const label = input.parentElement?.querySelector(".range-val");
    if (label) {
      input.addEventListener("input", () => { label.textContent = input.value; });
    }
  }
}
