"""Tests for scene grouping logic."""

import subprocess
import sys

import pytest

from app.text.scene_grouping import Scene, group_chunks_into_scenes


class TestGroupChunksIntoScenes:
    """Tests for group_chunks_into_scenes function."""

    def test_empty_chunks_returns_empty_list(self) -> None:
        """Empty input returns empty output."""
        result = group_chunks_into_scenes([], chunks_per_scene=7)
        assert result == []

    def test_fewer_chunks_than_scene_size(self) -> None:
        """When chunks < chunks_per_scene, all go into one scene."""
        chunks = ["chunk1", "chunk2", "chunk3"]
        result = group_chunks_into_scenes(chunks, chunks_per_scene=7)

        assert len(result) == 1
        assert result[0].scene_index == 0
        assert result[0].chunk_indices == [0, 1, 2]
        assert result[0].combined_text == "chunk1 chunk2 chunk3"

    def test_exact_multiple_of_scene_size(self) -> None:
        """When chunks is exact multiple of chunks_per_scene."""
        chunks = ["a", "b", "c", "d", "e", "f"]
        result = group_chunks_into_scenes(chunks, chunks_per_scene=3)

        assert len(result) == 2
        assert result[0].chunk_indices == [0, 1, 2]
        assert result[0].combined_text == "a b c"
        assert result[1].chunk_indices == [3, 4, 5]
        assert result[1].combined_text == "d e f"

    def test_remainder_in_last_scene(self) -> None:
        """Last scene gets the remainder when not evenly divisible."""
        chunks = ["a", "b", "c", "d", "e", "f", "g"]
        result = group_chunks_into_scenes(chunks, chunks_per_scene=3)

        assert len(result) == 3
        assert result[0].chunk_indices == [0, 1, 2]
        assert result[1].chunk_indices == [3, 4, 5]
        assert result[2].chunk_indices == [6]
        assert result[2].combined_text == "g"

    def test_single_chunk(self) -> None:
        """Single chunk creates single scene."""
        chunks = ["only one"]
        result = group_chunks_into_scenes(chunks, chunks_per_scene=7)

        assert len(result) == 1
        assert result[0].scene_index == 0
        assert result[0].chunk_indices == [0]
        assert result[0].combined_text == "only one"

    def test_chunks_per_scene_equals_one(self) -> None:
        """Each chunk becomes its own scene when chunks_per_scene=1."""
        chunks = ["a", "b", "c"]
        result = group_chunks_into_scenes(chunks, chunks_per_scene=1)

        assert len(result) == 3
        assert result[0].chunk_indices == [0]
        assert result[1].chunk_indices == [1]
        assert result[2].chunk_indices == [2]

    def test_scene_indices_are_sequential(self) -> None:
        """Scene indices are zero-based and sequential."""
        chunks = list("abcdefghij")  # 10 chunks
        result = group_chunks_into_scenes(chunks, chunks_per_scene=3)

        assert [s.scene_index for s in result] == [0, 1, 2, 3]

    def test_default_chunks_per_scene_is_seven(self) -> None:
        """Default value for chunks_per_scene is 7."""
        chunks = list("a" * 20)
        result = group_chunks_into_scenes(chunks)

        assert len(result) == 3  # 7 + 7 + 6
        assert len(result[0].chunk_indices) == 7
        assert len(result[1].chunk_indices) == 7
        assert len(result[2].chunk_indices) == 6

    def test_combined_text_joins_with_spaces(self) -> None:
        """Combined text joins chunks with single spaces."""
        chunks = ["Hello world.", "How are you?", "Fine thanks."]
        result = group_chunks_into_scenes(chunks, chunks_per_scene=3)

        assert result[0].combined_text == "Hello world. How are you? Fine thanks."

    def test_invalid_chunks_per_scene_zero(self) -> None:
        """chunks_per_scene=0 raises ValueError."""
        with pytest.raises(ValueError, match="chunks_per_scene must be >= 1"):
            group_chunks_into_scenes(["a"], chunks_per_scene=0)

    def test_invalid_chunks_per_scene_negative(self) -> None:
        """Negative chunks_per_scene raises ValueError."""
        with pytest.raises(ValueError, match="chunks_per_scene must be >= 1"):
            group_chunks_into_scenes(["a"], chunks_per_scene=-1)


class TestSceneModel:
    """Tests for Scene Pydantic model validation."""

    def test_valid_scene(self) -> None:
        """Valid scene creation."""
        scene = Scene(
            scene_index=0,
            chunk_indices=[0, 1, 2],
            combined_text="Hello world",
        )
        assert scene.scene_index == 0
        assert scene.chunk_indices == [0, 1, 2]
        assert scene.combined_text == "Hello world"

    def test_negative_scene_index_rejected(self) -> None:
        """Negative scene_index is rejected."""
        with pytest.raises(ValueError):
            Scene(scene_index=-1, chunk_indices=[0], combined_text="text")

    def test_empty_chunk_indices_rejected(self) -> None:
        """Empty chunk_indices list is rejected."""
        with pytest.raises(ValueError):
            Scene(scene_index=0, chunk_indices=[], combined_text="text")

    def test_empty_combined_text_rejected(self) -> None:
        """Empty combined_text is rejected."""
        with pytest.raises(ValueError):
            Scene(scene_index=0, chunk_indices=[0], combined_text="")

    def test_extra_fields_forbidden(self) -> None:
        """Extra fields are not allowed."""
        with pytest.raises(ValueError):
            Scene(
                scene_index=0,
                chunk_indices=[0],
                combined_text="text",
                extra_field="bad",  # type: ignore[call-arg]
            )


class TestChunksPerSceneConfig:
    """Tests for chunks_per_scene config validation.

    These tests use subprocess because config.py creates a global Settings()
    at import time, so env vars must be set before Python starts.
    """

    def test_invalid_chunks_per_scene_zero_rejected_at_startup(self) -> None:
        """CHUNKS_PER_SCENE=0 is rejected at settings load time."""
        result = subprocess.run(
            [sys.executable, "-c", "from app.config import settings"],
            env={"PYTHONPATH": ".", "CHUNKS_PER_SCENE": "0"},
            capture_output=True,
            text=True,
            cwd=".",
        )
        assert result.returncode != 0
        assert "greater_than_equal" in result.stderr

    def test_invalid_chunks_per_scene_negative_rejected_at_startup(self) -> None:
        """Negative CHUNKS_PER_SCENE is rejected at settings load time."""
        result = subprocess.run(
            [sys.executable, "-c", "from app.config import settings"],
            env={"PYTHONPATH": ".", "CHUNKS_PER_SCENE": "-5"},
            capture_output=True,
            text=True,
            cwd=".",
        )
        assert result.returncode != 0
        assert "greater_than_equal" in result.stderr
