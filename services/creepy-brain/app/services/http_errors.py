"""HTTP error helpers shared by routes and services."""

from __future__ import annotations

from typing import TypeVar

from fastapi import HTTPException

T = TypeVar("T")


def require_found(value: T | None, detail: str) -> T:
    """Return *value* or raise a FastAPI 404 error."""
    if value is None:
        raise HTTPException(status_code=404, detail=detail)
    return value
