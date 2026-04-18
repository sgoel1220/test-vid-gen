"use client";

import { useState } from "react";
import { useWorkflows } from "@/lib/hooks";
import { WorkflowCard } from "@/components/workflow-card";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

const STATUS_TABS = [
  { value: "all", label: "All" },
  { value: "running", label: "Running" },
  { value: "paused", label: "Paused" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
] as const;

type StatusTab = (typeof STATUS_TABS)[number]["value"];

export default function WorkflowsPage() {
  const [activeTab, setActiveTab] = useState<StatusTab>("all");
  const filter = activeTab === "all" ? undefined : activeTab;
  const { data: workflows, error, isLoading } = useWorkflows(filter);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Workflows</h1>
        <Button disabled title="Coming soon">
          + New Workflow
        </Button>
      </div>

      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as StatusTab)}>
        <TabsList>
          {STATUS_TABS.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>

        {STATUS_TABS.map((tab) => (
          <TabsContent key={tab.value} value={tab.value}>
            {isLoading && (
              <p className="text-sm text-muted-foreground py-8 text-center">Loading…</p>
            )}
            {error && (
              <p className="text-sm text-destructive py-8 text-center">
                Failed to load workflows: {error.message}
              </p>
            )}
            {!isLoading && !error && workflows?.length === 0 && (
              <p className="text-sm text-muted-foreground py-8 text-center">No workflows found.</p>
            )}
            {!isLoading && !error && workflows && workflows.length > 0 && (
              <div className="space-y-3 mt-4">
                {workflows.map((wf) => (
                  <WorkflowCard key={wf.id} workflow={wf} />
                ))}
              </div>
            )}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
