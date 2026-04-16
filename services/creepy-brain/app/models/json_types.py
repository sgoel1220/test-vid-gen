"""Custom SQLAlchemy types for Pydantic models in JSONB columns."""

from typing import Any, Optional, Type, TypeVar

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

    def __init__(self, pydantic_model: Type[T], **kwargs: Any) -> None:
        self.pydantic_model = pydantic_model
        super().__init__(**kwargs)

    def process_bind_param(self, value: Optional[T], dialect: Any) -> Optional[dict[str, Any]]:
        """Convert Pydantic model to dict for storage."""
        if value is None:
            return None
        if isinstance(value, dict):
            # If already a dict, validate and convert to model first
            value = self.pydantic_model.model_validate(value)
        return value.model_dump(mode="json")

    def process_result_value(self, value: Optional[dict[str, Any]], dialect: Any) -> Optional[T]:
        """Convert dict from database to Pydantic model."""
        if value is None:
            return None
        return self.pydantic_model.model_validate(value)
