import { Card, CardContent } from "@/components/ui/card";
import { isActivePod, type GpuPod } from "@/lib/api";

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function uptimeCostCents(pod: GpuPod): number {
  const start = new Date(pod.created_at).getTime();
  const now = Date.now();
  const hours = (now - start) / 3_600_000;
  return Math.round(hours * pod.cost_per_hour_cents);
}

interface CostSummaryProps {
  pods: GpuPod[];
}

export function CostSummary({ pods }: CostSummaryProps) {
  const activePods = pods.filter(isActivePod);

  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);

  const monthStart = new Date();
  monthStart.setDate(1);
  monthStart.setHours(0, 0, 0, 0);

  const todayCents = pods
    .filter((p) => new Date(p.created_at) >= todayStart)
    .reduce((sum, p) => {
      const cost = isActivePod(p) ? uptimeCostCents(p) : p.total_cost_cents;
      return sum + cost;
    }, 0);

  const monthCents = pods
    .filter((p) => new Date(p.created_at) >= monthStart)
    .reduce((sum, p) => {
      const cost = isActivePod(p) ? uptimeCostCents(p) : p.total_cost_cents;
      return sum + cost;
    }, 0);

  const summaryItems = [
    { label: "Active", value: String(activePods.length) },
    { label: "Today", value: formatCents(todayCents) },
    { label: "This month", value: formatCents(monthCents) },
  ];

  return (
    <div className="grid grid-cols-3 gap-4">
      {summaryItems.map(({ label, value }) => (
        <Card key={label}>
          <CardContent className="pt-4">
            <p className="text-xs text-muted-foreground uppercase tracking-wide">
              {label}
            </p>
            <p className="text-2xl font-semibold mt-1">{value}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
