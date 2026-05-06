"""Vast.ai PyWorker configuration for the SDXL Impressionist image server.

This worker proxies HTTP requests from Vast's serverless engine to the local
FastAPI model server running on port 8006.

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
# Benchmark prompts — diverse styles to estimate throughput
# ---------------------------------------------------------------------------

_BENCHMARK_PROMPTS: list[str] = [
    "a dark forest at night, moonlight through the trees, impressionist style",
    "sunset over a calm ocean with sailboats, oil painting",
    "medieval castle on a hilltop in autumn fog",
    "rainy city street with neon reflections, impressionist",
    "alpine meadow with wildflowers and distant mountains",
    "old library interior with towering bookshelves, candlelight",
    "japanese garden in spring with cherry blossoms and koi pond",
    "stormy seascape with lighthouse on rocky cliffs",
    "venetian canal at dusk with gondolas and lanterns",
    "snowy village at christmas with warm glowing windows",
]


def _benchmark_generator() -> dict[str, object]:
    """Generate a random benchmark request payload."""
    return {
        "prompt": random.choice(_BENCHMARK_PROMPTS),
        "negative_prompt": "photorealistic, photograph, blurry, low quality, watermark, text, deformed",
        "width": 1280,
        "height": 720,
        "steps": 4,
        "guidance_scale": 2.0,
    }


def _workload_calculator(payload: dict[str, object]) -> float:
    """Compute workload cost for a single image generation request.

    Image generation has roughly constant cost per request (4 steps at fixed resolution),
    so we return a flat value. The autoscaler uses this to estimate capacity.
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
            max_queue_time=60.0,
            workload_calculator=_workload_calculator,
            benchmark_config=BenchmarkConfig(
                generator=_benchmark_generator,
                runs=3,
                concurrency=1,  # Serial — GPU handles one request at a time
            ),
        ),
    ],
    log_action_config=LogActionConfig(
        on_load=["Application startup complete."],
        on_error=["Traceback", "RuntimeError:", "Pipeline loading failed", "CUDA OOM"],
        on_info=["Loading VAE", "Loading SDXL", "Loading Impressionism", "Loading SDXL-Lightning", "Fusing LoRAs"],
    ),
)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    Worker(worker_config).run()
