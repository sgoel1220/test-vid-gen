"""Job store — Repository pattern wrapping an in-memory dict + lock."""

from __future__ import annotations

import threading
from typing import Any, Optional

from fastapi import HTTPException

from enums import JobStatus
from models import LiteCloneJobStatusResponse


class JobStore:
    """Thread-safe in-memory store for async TTS job state."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(
        self, job_id: str, selected_chunk_indices: Optional[list[int]] = None
    ) -> LiteCloneJobStatusResponse:
        state = LiteCloneJobStatusResponse(
            job_id=job_id,
            status=JobStatus.QUEUED,
            message="Queued",
            selected_chunk_indices=list(selected_chunk_indices or []),
        )
        with self._lock:
            self._store[job_id] = state.model_dump()
        return state

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            entry = self._store.get(job_id)
            if entry is not None:
                entry.update(fields)

    def get(self, job_id: str) -> LiteCloneJobStatusResponse:
        with self._lock:
            entry = self._store.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
        return LiteCloneJobStatusResponse.model_validate(entry)

    def get_raw(self, job_id: str) -> dict[str, Any]:
        """Return raw dict for jobs that use a different response model."""
        with self._lock:
            entry = self._store.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
        return dict(entry)

    def status_url(self, job_id: str) -> str:
        return f"/api/jobs/{job_id}"


# Singleton used by run_orchestrator and routes
job_store = JobStore()
