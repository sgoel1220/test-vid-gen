"""Hatchet worker registration and startup.

Run as a separate process (recommended for production):
    python -m app.workflows.worker

This keeps the worker independently scalable from the API server.
"""

from app.logging import configure_logging
from app.workflows import WORKFLOWS, hatchet


def start_worker() -> None:
    """Register all workflows and start the Hatchet worker.

    Raises:
        RuntimeError: If no workflows are registered in WORKFLOWS.
    """
    if not WORKFLOWS:
        raise RuntimeError(
            "No workflows registered. Add workflow instances to app.workflows.WORKFLOWS "
            "before starting the worker."
        )

    configure_logging(json_logs=False)

    worker = hatchet.worker("creepy-brain-worker", workflows=WORKFLOWS)
    worker.start()


if __name__ == "__main__":
    start_worker()
