// All paths are relative — the Next.js rewrite proxy forwards /api/* to the backend.
// See next.config.ts for the proxy config and .env.local.example for NEXT_PUBLIC_API_URL.

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

/** Matches backend WorkflowResponse (list endpoint). */
export interface Workflow {
  id: string;
  status: "pending" | "running" | "completed" | "failed";
  workflow_type: string;
  current_step: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface WorkflowInput {
  premise: string;
  voice_name: string;
  generate_images: boolean;
  stitch_video: boolean;
  max_revisions: number;
  target_word_count: number;
}

export interface WorkflowStep {
  step_name: string;
  status: string;
  attempt_number: number;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface WorkflowChunk {
  chunk_index: number;
  tts_status: string;
  tts_duration_sec: number | null;
}

export interface WorkflowGpuPod {
  id: string;
  provider: string;
  status: string;
  created_at: string;
  ready_at: string | null;
  terminated_at: string | null;
  total_cost_cents: number;
}

/** Matches backend WorkflowDetailResponse (detail endpoint). */
export interface WorkflowDetail extends Workflow {
  input: WorkflowInput;
  result: Record<string, unknown> | null;
  steps: WorkflowStep[];
  chunks: WorkflowChunk[];
  gpu_pods: WorkflowGpuPod[];
}

export interface GpuPod {
  id: string;
  provider: string;
  workflow_id: string | null;
  status: string;
  gpu_type: string;
  cost_per_hour_cents: number;
  total_cost_cents: number;
  created_at: string;
  terminated_at: string | null;
}

export function fetchWorkflows(status?: string): Promise<Workflow[]> {
  const params = status ? `?status=${status}` : "";
  return apiFetch<Workflow[]>(`/api/workflows${params}`);
}

export function fetchWorkflow(id: string): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(`/api/workflows/${id}`);
}

// NOTE: /api/gpu-pods is not yet implemented in the backend.
// This will be wired up when the backend endpoint is added (see bead for GPU pod monitoring).
export function fetchGpuPods(status?: string): Promise<GpuPod[]> {
  const params = status ? `?status=${status}` : "";
  return apiFetch<GpuPod[]>(`/api/gpu-pods${params}`);
}

export async function terminatePod(podId: string): Promise<void> {
  const res = await fetch(`/api/gpu-pods/${podId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to terminate pod: ${res.status}`);
}

/** Terminal pod statuses — anything else is considered billable/active. */
const TERMINAL_STATUSES = new Set(["terminated", "error"]);

export function isActivePod(pod: GpuPod): boolean {
  return !TERMINAL_STATUSES.has(pod.status);
}
