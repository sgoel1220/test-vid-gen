"""Base class and helpers for per-step configurable parameters.

Use ``UIField`` instead of ``Field`` on any step-param attribute that should
appear in the dashboard form.  Fields declared with plain ``Field`` (or bare
annotations) are **hidden** from the UI by default — they still exist on the
Pydantic model and are accepted/validated in JSON, they just won't be rendered
in the frontend schema-form.

Example
-------
::

    class TtsStepParams(BaseStepParams):
        enabled: Literal[True] = True
        chunk_size: int = UIField(
            default=5, ge=1, le=20,
            description="Sentences per TTS chunk",
            ui_group="chunking",
        )
        # Internal-only: no UIField → hidden from dashboard
        _internal_retry_budget: int = Field(default=3)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo


# ---------------------------------------------------------------------------
# UI metadata key stored inside ``json_schema_extra``
# ---------------------------------------------------------------------------
_UI_META_KEY = "x-ui"


class UIFieldMeta(BaseModel):
    """Metadata blob embedded in a field's JSON Schema under ``x-ui``."""

    expose: bool = Field(default=True, description="Show this field in the dashboard form")
    group: str | None = Field(default=None, description="Optional visual group header")
    order: int = Field(default=0, description="Sort priority within its group (lower = first)")


# ---------------------------------------------------------------------------
# UIField helper — drop-in replacement for ``pydantic.Field``
# ---------------------------------------------------------------------------
def UIField(  # noqa: N802 – intentional PascalCase to mirror ``Field``
    default: Any = ...,
    *,
    ui_expose: bool = True,
    ui_group: str | None = None,
    ui_order: int = 0,
    **kwargs: Any,
) -> Any:
    """Pydantic ``Field`` with embedded UI metadata.

    Parameters
    ----------
    ui_expose:
        If *False*, the field is stripped from the UI schema (but still
        accepted on the Pydantic model).
    ui_group:
        Optional group name — the frontend can render a ``<fieldset>``
        header for each distinct group.
    ui_order:
        Sort key within a group.  Lower values appear first.
    **kwargs:
        Forwarded verbatim to ``pydantic.Field`` (``ge``, ``le``,
        ``description``, ``title``, etc.).
    """
    ui_meta = UIFieldMeta(expose=ui_expose, group=ui_group, order=ui_order)

    # Merge into any caller-supplied json_schema_extra
    extra: dict[str, Any] = dict(kwargs.pop("json_schema_extra", None) or {})
    extra[_UI_META_KEY] = ui_meta.model_dump(exclude_none=True)

    return Field(default, json_schema_extra=extra, **kwargs)


# ---------------------------------------------------------------------------
# Helpers to read UI metadata back from a FieldInfo / JSON Schema dict
# ---------------------------------------------------------------------------
def _field_is_ui_exposed(info: FieldInfo) -> bool:
    """Return True if a FieldInfo was created with ``UIField`` and is exposed."""
    extra = info.json_schema_extra
    if not isinstance(extra, dict):
        return False
    meta = extra.get(_UI_META_KEY)
    if not isinstance(meta, dict):
        return False
    return bool(meta.get("expose", True))


def _has_ui_meta(info: FieldInfo) -> bool:
    """Return True if the field carries any ``x-ui`` metadata at all."""
    extra = info.json_schema_extra
    if not isinstance(extra, dict):
        return False
    return _UI_META_KEY in extra


# ---------------------------------------------------------------------------
# BaseStepParams
# ---------------------------------------------------------------------------
class BaseStepParams(BaseModel):
    """Base for per-step configurable parameters.

    Every step param model inherits this.  The ``enabled`` field is always
    UI-exposed (as a toggle).
    """

    enabled: bool = UIField(default=True, description="Whether this step should run")

    # ----- UI schema generation -----
    @classmethod
    def ui_schema(cls) -> dict[str, Any]:
        """Return a JSON Schema containing **only** UI-exposed fields.

        Fields created with ``UIField`` and ``ui_expose=True`` (the default)
        are included.  The ``enabled`` field is **always** included (even
        when overridden as ``Literal[True]``) so the frontend can render
        its always-on / toggle logic.

        The ``x-ui`` metadata is preserved in each property so the frontend
        can read ``group`` / ``order``.
        """
        full = cls.model_json_schema()
        props: dict[str, Any] = full.get("properties", {})
        required: list[str] = list(full.get("required", []))

        exposed_keys: set[str] = {"enabled"}  # always include the toggle
        for name, info in cls.model_fields.items():
            if _has_ui_meta(info) and _field_is_ui_exposed(info):
                exposed_keys.add(name)

        filtered_props = {k: v for k, v in props.items() if k in exposed_keys}
        filtered_required = [r for r in required if r in exposed_keys]

        out = dict(full)
        out["properties"] = filtered_props
        if filtered_required:
            out["required"] = filtered_required
        else:
            out.pop("required", None)
        return out
