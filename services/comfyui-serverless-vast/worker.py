"""Vast.ai PyWorker configuration for the ComfyUI horror-painting image server.

This worker proxies HTTP requests from Vast's serverless engine to the local
FastAPI server running on port 8006, which in turn drives ComfyUI.

The PyWorker handles:
  - Request authentication and validation
  - Workload metering for autoscaling
  - Benchmarking for capacity estimation
  - Log-based readiness detection
"""

from __future__ import annotations

import random

from vastai import (
    BenchmarkConfig,
    HandlerConfig,
    LogActionConfig,
    Worker,
    WorkerConfig,
)

# ---------------------------------------------------------------------------
# Benchmark prompts — diverse horror-painting scenes
# ---------------------------------------------------------------------------

_BENCHMARK_PROMPTS: list[str] = [
    "cinematic haunted manor hallway, dusty portraits on walls, single candle flickering, long shadows, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting",
    "cinematic old decaying clock tower interior, dusty gears, flickering candlelight, deep shadows, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting",
    "cinematic abandoned cathedral nave, shattered stained glass, single candle on altar, long shadows, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting",
    "cinematic stormy coastline at night, shipwreck on jagged rocks, lighthouse beam cutting through rain, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting",
    "cinematic foggy swamp at dusk, gnarled trees rising from black water, faint lantern in the mist, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting",
]


def _benchmark_generator() -> dict[str, object]:
    """Generate a random benchmark request payload."""
    return {
        "prompt": random.choice(_BENCHMARK_PROMPTS),
        "width": 1216,
        "height": 832,
        "steps": 34,
        "cfg": 2.0,
    }


def _workload_calculator(payload: dict[str, object]) -> float:
    """Compute workload cost for a single generation request.

    34-step generation at 1216x832 is roughly constant cost per request.
    """
    return 100.0


# ---------------------------------------------------------------------------
# Worker configuration
# ---------------------------------------------------------------------------

_MODEL_SERVER_URL = "http://127.0.0.1"
_MODEL_SERVER_PORT = 8006
_MODEL_LOG_FILE = "/var/log/portal/server.log"

worker_config = WorkerConfig(
    model_server_url=_MODEL_SERVER_URL,
    model_server_port=_MODEL_SERVER_PORT,
    model_log_file=_MODEL_LOG_FILE,
    handlers=[
        HandlerConfig(
            route="/generate",
            allow_parallel_requests=False,  # GPU cannot parallelize inference
            max_queue_time=120.0,  # 34 steps takes longer than 4-step Lightning
            workload_calculator=_workload_calculator,
            benchmark_config=BenchmarkConfig(
                generator=_benchmark_generator,
                runs=2,  # Fewer runs — each takes ~30-60s
                concurrency=1,
            ),
        ),
    ],
    log_action_config=LogActionConfig(
        on_load=["Application startup complete."],
        on_error=["Traceback", "RuntimeError:", "ComfyUI startup failed", "CUDA OOM"],
        on_info=["Starting ComfyUI", "ComfyUI ready", "Workflow loaded"],
    ),
)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    Worker(worker_config).run()
