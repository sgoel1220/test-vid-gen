"use client";

import { useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { isActivePod, type GpuPod } from "@/lib/api";

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function uptimeLabel(createdAt: string): string {
  const ms = Date.now() - new Date(createdAt).getTime();
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}

function runningCostLabel(pod: GpuPod): string {
  const ms = Date.now() - new Date(pod.created_at).getTime();
  const hours = ms / 3_600_000;
  const cents = Math.round(hours * pod.cost_per_hour_cents);
  return `${formatCents(cents)} so far`;
}

function statusVariant(
  status: string
): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "running":
      return "default";
    case "terminated":
      return "outline";
    default:
      return "secondary";
  }
}

interface PodCardProps {
  pod: GpuPod;
  onTerminate: () => void;
}

export function PodCard({ pod, onTerminate }: PodCardProps) {
  const [confirming, setConfirming] = useState(false);

  function handleTerminateClick() {
    setConfirming(true);
  }

  function handleConfirm() {
    setConfirming(false);
    onTerminate();
  }

  function handleCancel() {
    setConfirming(false);
  }

  const isActive = isActivePod(pod);

  return (
    <Card>
      <CardContent className="pt-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-sm font-medium truncate">
                {pod.id}
              </span>
              <Badge variant={statusVariant(pod.status)}>{pod.status}</Badge>
            </div>

            <div className="mt-2 text-sm text-muted-foreground space-y-0.5">
              <p>
                <span className="font-medium text-foreground">GPU:</span>{" "}
                {pod.gpu_type} &middot;{" "}
                {formatCents(pod.cost_per_hour_cents)}/hr
              </p>

              {pod.workflow_id && (
                <p>
                  <span className="font-medium text-foreground">Workflow:</span>{" "}
                  <Link
                    href={`/workflows/${pod.workflow_id}`}
                    className="underline underline-offset-2 hover:text-foreground"
                  >
                    {pod.workflow_id}
                  </Link>
                </p>
              )}

              {isActive && (
                <p>
                  <span className="font-medium text-foreground">Uptime:</span>{" "}
                  {uptimeLabel(pod.created_at)} &middot;{" "}
                  {runningCostLabel(pod)}
                </p>
              )}

              {!isActive && pod.terminated_at && (
                <p>
                  <span className="font-medium text-foreground">
                    Total cost:
                  </span>{" "}
                  {formatCents(pod.total_cost_cents)}
                  {pod.status !== "terminated" && (
                    <> &middot; {pod.status}</>
                  )}
                </p>
              )}
            </div>
          </div>

          {isActive && (
            <div className="flex items-center gap-2 shrink-0">
              {confirming ? (
                <>
                  <Button size="sm" variant="destructive" onClick={handleConfirm}>
                    Confirm
                  </Button>
                  <Button size="sm" variant="outline" onClick={handleCancel}>
                    Cancel
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleTerminateClick}
                >
                  Terminate
                </Button>
              )}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
