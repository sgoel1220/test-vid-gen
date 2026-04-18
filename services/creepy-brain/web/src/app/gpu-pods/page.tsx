"use client";

import { useCallback } from "react";
import { mutate } from "swr";
import { useGpuPods } from "@/lib/hooks";
import { terminatePod, isActivePod } from "@/lib/api";
import { PodCard } from "@/components/pod-card";
import { CostSummary } from "@/components/cost-summary";

export default function GpuPodsPage() {
  const { data: pods, error, isLoading } = useGpuPods();

  const handleTerminate = useCallback(
    async (podId: string) => {
      try {
        await terminatePod(podId);
        await mutate(["gpu-pods", undefined]);
      } catch (err) {
        console.error("Terminate failed:", err);
      }
    },
    []
  );

  if (isLoading) {
    return (
      <div>
        <h1 className="text-2xl font-semibold mb-4">GPU Pods</h1>
        <p className="text-muted-foreground">Loading…</p>
      </div>
    );
  }

  if (error || !pods) {
    return (
      <div>
        <h1 className="text-2xl font-semibold mb-4">GPU Pods</h1>
        <p className="text-destructive text-sm">
          Failed to load pods. Backend may not be running.
        </p>
      </div>
    );
  }

  const activePods = pods.filter(isActivePod);
  const terminatedPods = pods.filter((p) => !isActivePod(p)).slice(0, 20);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">GPU Pods</h1>

      <CostSummary pods={pods} />

      <section>
        <h2 className="text-base font-medium mb-3">
          Active Pods{" "}
          <span className="text-muted-foreground font-normal">
            ({activePods.length})
          </span>
        </h2>
        {activePods.length === 0 ? (
          <p className="text-sm text-muted-foreground">No active pods.</p>
        ) : (
          <div className="space-y-3">
            {activePods.map((pod) => (
              <PodCard
                key={pod.id}
                pod={pod}
                onTerminate={() => handleTerminate(pod.id)}
              />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="text-base font-medium mb-3">Recent Terminated</h2>
        {terminatedPods.length === 0 ? (
          <p className="text-sm text-muted-foreground">No terminated pods.</p>
        ) : (
          <div className="space-y-3">
            {terminatedPods.map((pod) => (
              <PodCard
                key={pod.id}
                pod={pod}
                onTerminate={() => handleTerminate(pod.id)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
