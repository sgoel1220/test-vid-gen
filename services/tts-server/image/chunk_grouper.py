"""LLM-based chunk grouping for background image generation."""

from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ChunkGroup(BaseModel):
    """A group of TTS chunks that share a common visual scene."""
    group_index: int = Field(..., ge=0, description="Zero-based group index.")
    chunk_indices: List[int] = Field(..., description="Indices of chunks in this group.")
    combined_text: str = Field(..., description="Combined text from all chunks in group.")
    background_description: str = Field(
        ...,
        description="LLM-generated description of the background scene (setting only, no characters)."
    )


_CHUNK_GROUPING_SYSTEM_PROMPT = """You are a visual scene grouping assistant for creepy pasta narration videos.

Given a list of text chunks from a horror story (each chunk is already separated for TTS), your task is to:
1. Group consecutive chunks (typically 5-6 chunks per group) that share a common SETTING/LOCATION
2. For each group, describe the BACKGROUND SCENE ONLY — describe the environment, location, atmosphere, and setting
3. NEVER include humans, characters, faces, or complex objects in your descriptions
4. Focus on: landscapes, rooms, buildings, nature, weather, lighting, mood, atmosphere

Output a JSON array of objects with these fields:
- "group_index": integer starting at 0
- "chunk_indices": array of chunk indices (e.g., [0, 1, 2, 3, 4])
- "background_description": detailed description of the BACKGROUND/SETTING only (30-60 words)

Rules:
- Group 5-6 chunks together when they share a setting/location
- If the setting changes significantly, start a new group
- Describe ONLY the environment/background — no people, no characters, no complex objects
- Focus on paintable elements: rooms, forests, roads, skies, buildings, weather, lighting
- Output ONLY valid JSON — no markdown fences, no explanation."""


def group_chunks_for_images(
    chunks: List[str],
    chunks_per_group: int = 5,
    model_id: str = "Qwen/Qwen2.5-1.5B-Instruct",
    max_new_tokens: int = 4096,
) -> List[ChunkGroup]:
    """Group TTS chunks and generate background scene descriptions.

    Args:
        chunks: List of TTS text chunks
        chunks_per_group: Target number of chunks per group (soft target)
        model_id: Qwen model to use for grouping
        max_new_tokens: Max tokens for LLM response

    Returns:
        List of ChunkGroup with background descriptions
    """
    if not chunks:
        return []

    # Build input for LLM
    chunk_list = "\n".join(
        f"Chunk {i}: {chunk[:200]}{'...' if len(chunk) > 200 else ''}"
        for i, chunk in enumerate(chunks)
    )

    user_prompt = (
        f"Story chunks ({len(chunks)} total):\n\n{chunk_list}\n\n"
        f"Group these chunks (target {chunks_per_group} chunks per group) and describe "
        f"the BACKGROUND SCENE for each group. Remember: backgrounds only, no people or characters."
    )

    try:
        from text.normalization import _load_model as _load_qwen
        import torch as _torch
        import json

        model, tokenizer = _load_qwen(model_id)
        if model is None or tokenizer is None:
            logger.warning("Qwen LLM unavailable — using fallback chunking.")
            return _fallback_chunk_groups(chunks, chunks_per_group)

        messages = [
            {"role": "system", "content": _CHUNK_GROUPING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        if hasattr(tokenizer, "apply_chat_template"):
            formatted = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            formatted = (
                f"<|system|>{_CHUNK_GROUPING_SYSTEM_PROMPT}</s>"
                f"<|user|>{user_prompt}</s><|assistant|>"
            )

        inputs = tokenizer(formatted, return_tensors="pt").to(model.device)
        input_len = inputs["input_ids"].shape[1]

        with _torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                pad_token_id=tokenizer.eos_token_id,
            )

        raw_output = tokenizer.decode(
            output_ids[0][input_len:],
            skip_special_tokens=True
        ).strip()

        # Parse JSON response
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            first_newline = cleaned.index("\n") if "\n" in cleaned else 3
            cleaned = cleaned[first_newline:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        groups_data = json.loads(cleaned)
        if not isinstance(groups_data, list):
            raise ValueError("Expected JSON array")

        groups: List[ChunkGroup] = []
        for item in groups_data:
            indices = item.get("chunk_indices", [])
            # Combine text from all chunks in this group
            combined = " ".join(chunks[i] for i in indices if 0 <= i < len(chunks))

            groups.append(
                ChunkGroup(
                    group_index=item.get("group_index", len(groups)),
                    chunk_indices=indices,
                    combined_text=combined,
                    background_description=item.get("background_description", ""),
                )
            )

        logger.info("Grouped %d chunks into %d background scenes.", len(chunks), len(groups))
        return groups

    except Exception as exc:
        logger.warning("Chunk grouping failed (%s) — using fallback.", exc, exc_info=True)
        return _fallback_chunk_groups(chunks, chunks_per_group)


def _fallback_chunk_groups(chunks: List[str], chunks_per_group: int) -> List[ChunkGroup]:
    """Fallback grouping when LLM is unavailable — simple sequential grouping."""
    groups: List[ChunkGroup] = []

    for start_idx in range(0, len(chunks), chunks_per_group):
        end_idx = min(start_idx + chunks_per_group, len(chunks))
        indices = list(range(start_idx, end_idx))
        combined = " ".join(chunks[i] for i in indices)

        groups.append(
            ChunkGroup(
                group_index=len(groups),
                chunk_indices=indices,
                combined_text=combined,
                background_description=(
                    f"A dark, eerie background scene from a horror story "
                    f"(scene {len(groups) + 1})"
                ),
            )
        )

    logger.info("Fallback grouping: %d chunks → %d groups.", len(chunks), len(groups))
    return groups
