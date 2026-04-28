"""Tests for BaseStepParams.ui_schema() and UIField metadata."""

from __future__ import annotations

from typing import Literal

from app.models.step_params import BaseStepParams, UIField


class _AlwaysOnParams(BaseStepParams):
    enabled: Literal[True] = True
    visible: int = UIField(default=5, ge=1, le=10, ui_group="tuning")
    hidden: str = "internal"


class _ToggleOnly(BaseStepParams):
    enabled: bool = UIField(default=False, description="toggle me")


def test_ui_schema_includes_only_uifield_and_enabled() -> None:
    schema = _AlwaysOnParams.ui_schema()
    props = schema["properties"]
    assert "enabled" in props, "enabled must always be present"
    assert "visible" in props, "UIField fields must be present"
    assert "hidden" not in props, "plain fields must be excluded"


def test_ui_schema_preserves_x_ui_metadata() -> None:
    schema = _AlwaysOnParams.ui_schema()
    meta = schema["properties"]["visible"].get("x-ui")
    assert meta is not None
    assert meta["group"] == "tuning"
    assert meta["expose"] is True


def test_ui_schema_const_enabled() -> None:
    schema = _AlwaysOnParams.ui_schema()
    assert schema["properties"]["enabled"].get("const") is True


def test_ui_schema_toggle_only() -> None:
    schema = _ToggleOnly.ui_schema()
    assert "enabled" in schema["properties"]
    assert schema["properties"]["enabled"]["type"] == "boolean"


def test_real_step_params_schemas() -> None:
    from app.models.json_schemas import (
        ImageStepParams,
        StoryStepParams,
        TtsStepParams,
    )

    story = StoryStepParams.ui_schema()
    assert "max_revisions" in story["properties"]
    assert "target_word_count" in story["properties"]
    assert story["properties"]["enabled"].get("const") is True

    tts = TtsStepParams.ui_schema()
    assert tts["properties"]["enabled"].get("const") is True

    image = ImageStepParams.ui_schema()
    assert image["properties"]["enabled"]["type"] == "boolean"
