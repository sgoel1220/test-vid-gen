// API client — all fetch wrappers and TypeScript interfaces

// ── Types ──────────────────────────────────────────────────────

export type WorkflowStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "paused";

export type StepName =
  | "generate_story"
  | "tts_synthesis"
  | "image_generation"
  | "stitch_final"
  | "cleanup_gpu_pod";

export type StepStatus = "pending" | "running" | "completed" | "failed" | "skipped";
export type ChunkTtsStatus = "pending" | "processing" | "completed" | "failed";
export type GpuPodStatus = "creating" | "running" | "ready" | "stopped" | "terminated" | "error";

export interface WorkflowResponse {
  id: string;
  status: WorkflowStatus;
  workflow_type: string;
  current_step: StepName | null;
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

export interface WorkflowResult {
  story_id: string | null;
  run_id: string | null;
  final_audio_blob_id: string | null;
  final_video_blob_id: string | null;
  total_duration_sec: number | null;
  chunk_count: number | null;
  gpu_pod_id: string | null;
  total_cost_cents: number | null;
}

export interface WorkflowStep {
  step_name: StepName;
  status: StepStatus;
  attempt_number: number;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface WorkflowChunk {
  chunk_index: number;
  chunk_text: string;
  tts_status: ChunkTtsStatus;
  tts_duration_sec: number | null;
  tts_audio_blob_id: string | null;
  tts_mp3_blob_id: string | null;
  tts_completed_at: string | null;
  scene_id: string | null;
}

export interface WorkflowGpuPod {
  id: string;
  provider: string;
  status: GpuPodStatus;
  created_at: string;
  ready_at: string | null;
  terminated_at: string | null;
  total_cost_cents: number;
}

export interface WorkflowDetailResponse extends WorkflowResponse {
  input: WorkflowInput;
  result: WorkflowResult | null;
  steps: WorkflowStep[];
  chunks: WorkflowChunk[];
  gpu_pods: WorkflowGpuPod[];
}

export interface CostSummary {
  today_cents: number;
  month_cents: number;
  active_pod_count: number;
}

export interface StoryAct {
  act_number: number;
  title: string | null;
  content: string;
  word_count: number | null;
}

export interface StoryDetailResponse {
  id: string;
  title: string | null;
  premise: string;
  status: string;
  word_count: number | null;
  full_text: string | null;
  acts: StoryAct[];
}

// ── Fetch helpers ──────────────────────────────────────────────

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export function fetchWorkflows(
  status?: WorkflowStatus,
  limit = 20,
): Promise<WorkflowResponse[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  params.set("limit", String(limit));
  return api(`/api/workflows?${params}`);
}

export function fetchWorkflowDetail(id: string): Promise<WorkflowDetailResponse> {
  return api(`/api/workflows/${id}`);
}

export function retryWorkflow(id: string): Promise<WorkflowResponse> {
  return api(`/api/workflows/${id}/retry`, { method: "POST" });
}

export function cancelWorkflow(id: string): Promise<void> {
  return api(`/api/workflows/${id}`, { method: "DELETE" });
}

export function pauseWorkflow(id: string): Promise<void> {
  return api(`/api/workflows/${id}/pause`, { method: "POST" });
}

export function resumeWorkflow(id: string): Promise<WorkflowResponse> {
  return api(`/api/workflows/${id}/resume`, { method: "POST" });
}

export function retryTtsStep(id: string): Promise<WorkflowResponse> {
  return api(`/api/workflows/${id}/retry-step`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step_name: "tts_synthesis" }),
  });
}

export function retryChunks(id: string, chunkIndices?: number[]): Promise<WorkflowResponse> {
  return api(`/api/workflows/${id}/retry-chunks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chunk_indices: chunkIndices ?? null }),
  });
}

export function fetchCostSummary(): Promise<CostSummary> {
  return api("/api/costs/summary");
}

export interface CreateWorkflowRequest {
  premise: string;
  voice_name: string;
  target_word_count: number;
  generate_images?: boolean;
  stitch_video?: boolean;
}

export interface VoiceResponse {
  id: string;
  name: string;
  description: string | null;
  is_default: boolean;
}

export function createWorkflow(req: CreateWorkflowRequest): Promise<WorkflowResponse> {
  return api("/api/workflows", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
}

export function fetchVoices(): Promise<VoiceResponse[]> {
  return api("/api/voices");
}

export function fetchStoryByWorkflow(workflowId: string): Promise<StoryDetailResponse> {
  return api(`/api/stories/by-workflow/${workflowId}`);
}

export function updateStory(storyId: string, fullText: string): Promise<StoryDetailResponse> {
  return api(`/api/stories/${storyId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ full_text: fullText }),
  });
}
