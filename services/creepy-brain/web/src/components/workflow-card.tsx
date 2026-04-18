"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Workflow } from "@/lib/api";

interface WorkflowCardProps {
  workflow: Workflow;
}

const STATUS_VARIANTS: Record<
  Workflow["status"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "outline",
  running: "default",
  completed: "secondary",
  failed: "destructive",
  cancelled: "outline",
  paused: "secondary",
};

function formatRelativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatDuration(start: string, end: string): string {
  const diff = new Date(end).getTime() - new Date(start).getTime();
  const totalSeconds = Math.floor(diff / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

export function WorkflowCard({ workflow }: WorkflowCardProps) {
  const { id, status, current_step, created_at, started_at, completed_at, error } = workflow;

  return (
    <Link href={`/workflows/${id}`} className="block">
      <Card className="hover:ring-primary/30 transition-shadow cursor-pointer">
        <CardHeader className="border-b">
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="font-mono text-sm text-muted-foreground">
              #{id.slice(0, 8)}
            </CardTitle>
            <Badge variant={STATUS_VARIANTS[status]}>
              {status.charAt(0).toUpperCase() + status.slice(1)}
            </Badge>
          </div>
          {current_step && (
            <p className="text-sm mt-1 text-muted-foreground">Step: {current_step}</p>
          )}
        </CardHeader>

        <CardContent className="pt-3">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Created {formatRelativeTime(created_at)}</span>

            {started_at && status === "running" && (
              <span>Running {formatRelativeTime(started_at)}</span>
            )}

            {status === "completed" && started_at && completed_at && (
              <span>Duration: {formatDuration(started_at, completed_at)}</span>
            )}

            {status === "failed" && error && (
              <span className="text-destructive truncate max-w-xs">{error}</span>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
