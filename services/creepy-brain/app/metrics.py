"""Prometheus metrics for Creepy Brain service."""

from prometheus_client import Counter, Gauge, Histogram

# Workflow metrics
workflow_started: Counter = Counter(
    "workflow_started_total",
    "Total workflows started",
    ["workflow_type"],
)
workflow_completed: Counter = Counter(
    "workflow_completed_total",
    "Total workflows completed",
    ["workflow_type"],
)
workflow_failed: Counter = Counter(
    "workflow_failed_total",
    "Total workflows failed",
    ["workflow_type", "failed_step"],
)
workflow_duration: Histogram = Histogram(
    "workflow_duration_seconds",
    "Workflow duration in seconds",
    ["workflow_type"],
    buckets=[60, 300, 600, 1800, 3600],
)
workflow_active: Gauge = Gauge(
    "workflow_active_count",
    "Currently running workflows",
)

# GPU pod metrics
gpu_pod_created: Counter = Counter(
    "gpu_pod_created_total",
    "Total GPU pods created",
    ["provider"],
)
gpu_pod_terminated: Counter = Counter(
    "gpu_pod_terminated_total",
    "Total GPU pods terminated",
    ["provider"],
)
gpu_pod_active: Gauge = Gauge(
    "gpu_pod_active_count",
    "Currently active GPU pods",
    ["provider"],
)
gpu_pod_cost: Counter = Counter(
    "gpu_pod_cost_cents_total",
    "Total GPU cost in cents",
    ["provider"],
)

# Step metrics
step_duration: Histogram = Histogram(
    "step_duration_seconds",
    "Step duration in seconds",
    ["step_name"],
    buckets=[10, 60, 300, 600, 1800],
)
step_failed: Counter = Counter(
    "step_failed_total",
    "Total step failures",
    ["step_name"],
)
