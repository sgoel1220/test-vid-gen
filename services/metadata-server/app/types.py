"""Custom SQLAlchemy TypeDecorators."""

from __future__ import annotations

from typing import Any, Generic, Optional, Type, TypeVar

from pydantic import BaseModel
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

M = TypeVar("M", bound=BaseModel)


class PydanticJSONB(TypeDecorator[M], Generic[M]):
    """Stores a Pydantic model as a JSONB column.

    Usage::

        col: Mapped[MyModel] = mapped_column(PydanticJSONB(MyModel))
    """

    impl = JSONB
    cache_ok = True

    def __init__(self, model: Type[M]) -> None:
        super().__init__()
        self._model = model

    def process_bind_param(self, value: Optional[M], dialect: Dialect) -> Optional[Any]:
        if value is None:
            return None
        return value.model_dump(mode="json")

    def process_result_value(self, value: Optional[Any], dialect: Dialect) -> Optional[M]:
        if value is None:
            return None
        return self._model.model_validate(value)
