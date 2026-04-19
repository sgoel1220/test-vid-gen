"""Custom SQLAlchemy types for Pydantic models in JSONB columns."""

from __future__ import annotations

import typing
from typing import Any, TypeVar, cast

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB

T = TypeVar("T", bound=BaseModel)


class PydanticType(TypeDecorator[T]):
    """SQLAlchemy type for storing Pydantic models as JSONB.

    This type handles automatic serialization/deserialization between
    Pydantic models and PostgreSQL JSONB columns.

    Supports both plain BaseModel subclasses and Annotated discriminated
    unions (e.g. ``Annotated[A | B, Field(discriminator=...)]``).
    """

    impl = JSONB
    cache_ok = True

    def __init__(self, pydantic_model: type[T], **kwargs: Any) -> None:
        # For Annotated unions or plain unions, use TypeAdapter.
        origin = typing.get_origin(pydantic_model)
        if origin is not None:
            # Annotated[Union[...], Field(...)] or Union[...]
            self._adapter: TypeAdapter[Any] = TypeAdapter(pydantic_model)
            self._is_union = True
        else:
            self._adapter = TypeAdapter(pydantic_model)
            self._is_union = False
        self.pydantic_model = pydantic_model
        super().__init__(**kwargs)

    def process_bind_param(self, value: T | dict[str, Any] | None, dialect: Any) -> dict[str, Any] | None:
        """Convert Pydantic model to dict for storage."""
        if value is None:
            return None
        if isinstance(value, dict):
            value = self._adapter.validate_python(value)
        return cast(dict[str, Any], self._adapter.dump_python(value, mode="json"))

    def process_result_value(self, value: dict[str, Any] | None, dialect: Any) -> T | None:
        """Convert dict from database to Pydantic model."""
        if value is None:
            return None
        return cast(T, self._adapter.validate_python(value))
