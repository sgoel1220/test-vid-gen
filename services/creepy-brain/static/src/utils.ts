// Shared utility functions

/** Format cents as dollars: 1234 → "$12.34" */
export function formatCost(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/** Relative time: "3m ago", "2h ago", "1d ago" */
export function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  return `${d}d ago`;
}

/** Duration between two ISO strings (or now) in "Xm Ys" */
export function duration(start: string, end?: string | null): string {
  const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime();
  const sec = Math.max(0, Math.floor(ms / 1000));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (min < 60) return `${min}m ${remSec}s`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hr}h ${remMin}m`;
}

/** Truncate UUID: "abc12345-..." → "abc123" */
export function shortId(id: string): string {
  return id.slice(0, 8);
}

/** Capitalize and replace underscores: "generate_story" → "Generate Story" */
export function formatStep(step: string): string {
  return step.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Status → CSS class suffix */
export function statusClass(status: string): string {
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

/** Escape HTML to prevent XSS */
export function esc(s: string): string {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}
