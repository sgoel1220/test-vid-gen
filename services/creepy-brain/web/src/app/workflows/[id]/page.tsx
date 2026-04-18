"use client";

import { use } from "react";
import Link from "next/link";
import { useWorkflow } from "@/lib/hooks";
import type { Workflow } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

const STATUS_VARIANTS: Record<Workflow["status"], BadgeVariant> = {
  pending: "outline",
  running: "default",
  completed: "secondary",
  failed: "destructive",
  cancelled: "outline",
  paused: "secondary",
};

const GENERIC_STATUS_VARIANTS: Record<string, BadgeVariant> = {
  pending: "outline",
  running: "default",
  completed: "secondary",
  succeeded: "secondary",
  success: "secondary",
  failed: "destructive",
  error: "destructive",
  cancelled: "outline",
  paused: "secondary",
  skipped: "outline",
  terminated: "secondary",
};

function formatStatus(status: string): string {
  return status
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatDateTime(value: string | null): string {
  if (!value) return "Not recorded";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatRelativeTime(value: string): string {
  const diff = Date.now() - new Date(value).getTime();
  const minutes = Math.floor(diff / 60_000);

  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  return `${Math.floor(hours / 24)}d ago`;
}

function formatDurationMs(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "Not started";

  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();

  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "Not recorded";

  return formatDurationMs(endMs - startMs);
}

function formatSeconds(seconds: number | null): string {
  if (seconds === null) return "Not recorded";
  return formatDurationMs(seconds * 1000);
}

function formatCost(cents: number): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}

function isCompletedStepStatus(status: string): boolean {
  return ["completed", "succeeded", "success"].includes(status.toLowerCase());
}

function StepStatusBadge({ status }: { status: string }) {
  const normalizedStatus = status.toLowerCase();

  return (
    <Badge variant={GENERIC_STATUS_VARIANTS[normalizedStatus] ?? "outline"}>
      {formatStatus(status)}
    </Badge>
  );
}

function WorkflowStatusBadge({ status }: { status: Workflow["status"] }) {
  return <Badge variant={STATUS_VARIANTS[status]}>{formatStatus(status)}</Badge>;
}

export default function WorkflowDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: workflow, error, isLoading } = useWorkflow(id);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Link href="/workflows" className="text-sm text-muted-foreground hover:text-foreground">
          Back to workflows
        </Link>
        <h1 className="text-2xl font-semibold">Workflow #{id.slice(0, 8)}</h1>
        <p className="text-sm text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (error || !workflow) {
    return (
      <div className="space-y-4">
        <Link href="/workflows" className="text-sm text-muted-foreground hover:text-foreground">
          Back to workflows
        </Link>
        <h1 className="text-2xl font-semibold">Workflow #{id.slice(0, 8)}</h1>
        <p className="text-sm text-destructive">
          Failed to load workflow{error?.message ? `: ${error.message}` : "."}
        </p>
      </div>
    );
  }

  const completedSteps = workflow.steps.filter((step) =>
    isCompletedStepStatus(step.status)
  ).length;
  const stepProgress =
    workflow.steps.length > 0
      ? Math.round((completedSteps / workflow.steps.length) * 100)
      : workflow.status === "completed"
        ? 100
        : 0;
  const stepProgressLabel =
    workflow.steps.length > 0
      ? `${completedSteps}/${workflow.steps.length} completed`
      : "No steps recorded";

  return (
    <div className="space-y-6">
      <Link href="/workflows" className="text-sm text-muted-foreground hover:text-foreground">
        Back to workflows
      </Link>

      <Card>
        <CardHeader className="border-b">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="font-mono text-lg">#{workflow.id.slice(0, 8)}</CardTitle>
              <p className="mt-1 break-all text-xs text-muted-foreground">{workflow.id}</p>
            </div>
            <WorkflowStatusBadge status={workflow.status} />
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 text-sm text-muted-foreground md:grid-cols-3">
            <div>
              <div className="font-medium text-foreground">Created</div>
              <div>{formatDateTime(workflow.created_at)}</div>
              <div>{formatRelativeTime(workflow.created_at)}</div>
            </div>
            <div>
              <div className="font-medium text-foreground">Duration</div>
              <div>{formatDuration(workflow.started_at, workflow.completed_at)}</div>
              {workflow.started_at && (
                <div>Started {formatDateTime(workflow.started_at)}</div>
              )}
            </div>
            <div>
              <div className="font-medium text-foreground">Current Step</div>
              <div>{workflow.current_step ?? "None"}</div>
              {workflow.completed_at && (
                <div>Completed {formatDateTime(workflow.completed_at)}</div>
              )}
            </div>
          </div>

          {workflow.error && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {workflow.error}
            </p>
          )}

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="font-medium">Step Progress</span>
              <span className="text-muted-foreground">{stepProgressLabel}</span>
            </div>
            <Progress value={stepProgress} aria-label="Workflow step progress" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Input</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <div className="mb-1 text-sm font-medium">Premise</div>
            <p className="whitespace-pre-wrap text-sm text-muted-foreground">
              {workflow.input.premise}
            </p>
          </div>
          <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-5">
            <div>
              <div className="font-medium">Voice</div>
              <div className="text-muted-foreground">{workflow.input.voice_name}</div>
            </div>
            <div>
              <div className="font-medium">Images</div>
              <div className="text-muted-foreground">
                {workflow.input.generate_images ? "Enabled" : "Disabled"}
              </div>
            </div>
            <div>
              <div className="font-medium">Video Stitching</div>
              <div className="text-muted-foreground">
                {workflow.input.stitch_video ? "Enabled" : "Disabled"}
              </div>
            </div>
            <div>
              <div className="font-medium">Max Revisions</div>
              <div className="text-muted-foreground">{workflow.input.max_revisions}</div>
            </div>
            <div>
              <div className="font-medium">Target Words</div>
              <div className="text-muted-foreground">
                {workflow.input.target_word_count.toLocaleString()}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Steps</CardTitle>
        </CardHeader>
        <CardContent>
          {workflow.steps.length === 0 ? (
            <p className="text-sm text-muted-foreground">No steps recorded.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Step</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Attempt</TableHead>
                  <TableHead>Timing</TableHead>
                  <TableHead>Error</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {workflow.steps.map((step) => (
                  <TableRow key={`${step.step_name}-${step.attempt_number}`}>
                    <TableCell className="font-medium">{step.step_name}</TableCell>
                    <TableCell>
                      <StepStatusBadge status={step.status} />
                    </TableCell>
                    <TableCell>{step.attempt_number}</TableCell>
                    <TableCell>
                      <div>{formatDuration(step.started_at, step.completed_at)}</div>
                      <div className="text-xs text-muted-foreground">
                        {formatDateTime(step.started_at)}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-md whitespace-normal text-destructive">
                      {step.error ?? "-"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Chunks</CardTitle>
        </CardHeader>
        <CardContent>
          {workflow.chunks.length === 0 ? (
            <p className="text-sm text-muted-foreground">No chunks recorded.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Index</TableHead>
                  <TableHead>TTS Status</TableHead>
                  <TableHead>Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {workflow.chunks.map((chunk) => (
                  <TableRow key={chunk.chunk_index}>
                    <TableCell>{chunk.chunk_index}</TableCell>
                    <TableCell>
                      <StepStatusBadge status={chunk.tts_status} />
                    </TableCell>
                    <TableCell>{formatSeconds(chunk.tts_duration_sec)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <section className="space-y-3">
        <h2 className="text-base font-medium">GPU Pods</h2>
        {workflow.gpu_pods.length === 0 ? (
          <p className="text-sm text-muted-foreground">No GPU pods recorded.</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {workflow.gpu_pods.map((pod) => (
              <Card key={pod.id}>
                <CardHeader className="border-b">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <CardTitle className="font-mono text-sm">#{pod.id.slice(0, 8)}</CardTitle>
                      <p className="mt-1 text-sm text-muted-foreground">{pod.provider}</p>
                    </div>
                    <StepStatusBadge status={pod.status} />
                  </div>
                </CardHeader>
                <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
                  <div>
                    <div className="font-medium">Cost</div>
                    <div className="text-muted-foreground">
                      {formatCost(pod.total_cost_cents)}
                    </div>
                  </div>
                  <div>
                    <div className="font-medium">Created</div>
                    <div className="text-muted-foreground">{formatDateTime(pod.created_at)}</div>
                  </div>
                  <div>
                    <div className="font-medium">Ready</div>
                    <div className="text-muted-foreground">{formatDateTime(pod.ready_at)}</div>
                  </div>
                  <div>
                    <div className="font-medium">Terminated</div>
                    <div className="text-muted-foreground">
                      {formatDateTime(pod.terminated_at)}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>

      {workflow.status === "completed" && (
        <Card>
          <CardHeader>
            <CardTitle>Result</CardTitle>
          </CardHeader>
          <CardContent>
            {workflow.result ? (
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
                {JSON.stringify(workflow.result, null, 2)}
              </pre>
            ) : (
              <p className="text-sm text-muted-foreground">No result returned.</p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
