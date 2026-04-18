"""Custom SQLAlchemy types for Pydantic models in JSONB columns."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB

T = TypeVar("T", bound=BaseModel)


class PydanticType(TypeDecorator[T]):
    """SQLAlchemy type for storing Pydantic models as JSONB.

    This type handles automatic serialization/deserialization between
    Pydantic models and PostgreSQL JSONB columns.
    """

    impl = JSONB
    cache_ok = True

    def __init__(self, pydantic_model: type[T], **kwargs: Any) -> None:
        self.pydantic_model = pydantic_model
        super().__init__(**kwargs)

    def process_bind_param(self, value: T | dict[str, Any] | None, dialect: Any) -> dict[str, Any] | None:
        """Convert Pydantic model to dict for storage."""
        if value is None:
            return None
        if isinstance(value, dict):
            # If already a dict, validate and convert to model first
            value = self.pydantic_model.model_validate(value)
        return value.model_dump(mode="json")

    def process_result_value(self, value: dict[str, Any] | None, dialect: Any) -> T | None:
        """Convert dict from database to Pydantic model."""
        if value is None:
            return None
        return self.pydantic_model.model_validate(value)
