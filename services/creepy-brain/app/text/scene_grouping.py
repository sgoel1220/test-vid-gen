"""Scene grouping for image generation.

Groups consecutive TTS chunks into scenes for background image generation.
Each scene spans multiple chunks, producing one image per scene instead of per chunk.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Scene(BaseModel):
    """A scene representing a group of consecutive TTS chunks for image generation."""

    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=0, description="Zero-based scene index")
    chunk_indices: list[int] = Field(
        min_length=1, description="Indices of chunks in this scene"
    )
    combined_text: str = Field(
        min_length=1, description="Combined text of all chunks in this scene"
    )


def group_chunks_into_scenes(
    chunks: list[str],
    chunks_per_scene: int = 7,
) -> list[Scene]:
    """Group consecutive text chunks into scenes for image generation.

    Each scene contains a fixed number of chunks (except the last scene which
    may have fewer). This produces one background image per scene rather than
    per chunk, resulting in natural visual pacing for video narration.

    Args:
        chunks: List of TTS chunk strings to group.
        chunks_per_scene: Number of chunks per scene (default 7).
            Must be at least 1.

    Returns:
        List of Scene objects, each containing chunk indices and combined text.
        Returns empty list if chunks is empty.

    Raises:
        ValueError: If chunks_per_scene < 1.

    Examples:
        >>> chunks = ["a", "b", "c", "d", "e"]
        >>> scenes = group_chunks_into_scenes(chunks, chunks_per_scene=2)
        >>> len(scenes)
        3
        >>> scenes[0].chunk_indices
        [0, 1]
        >>> scenes[2].chunk_indices
        [4]
    """
    if chunks_per_scene < 1:
        raise ValueError(f"chunks_per_scene must be >= 1, got {chunks_per_scene}")

    if not chunks:
        return []

    scenes: list[Scene] = []
    num_chunks = len(chunks)

    for scene_idx, start in enumerate(range(0, num_chunks, chunks_per_scene)):
        end = min(start + chunks_per_scene, num_chunks)
        chunk_indices = list(range(start, end))
        combined_text = " ".join(chunks[start:end])

        scenes.append(
            Scene(
                scene_index=scene_idx,
                chunk_indices=chunk_indices,
                combined_text=combined_text,
            )
        )

    return scenes
