"""Story generation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from creepy_pasta_protocol.common import Frozen
from creepy_pasta_protocol.stories import GenerateStoryRequest

from app.auth import require_api_key
from app.services.generation import start_generation

router = APIRouter(
    prefix="/v1/stories",
    tags=["stories"],
    dependencies=[Depends(require_api_key)],
)


class _GenerateResponse(Frozen):
    story_id: str


@router.post("/generate", response_model=_GenerateResponse)
async def generate_story(body: GenerateStoryRequest) -> _GenerateResponse:
    """Accept a premise and start background story generation.

    Returns the story_id immediately. Poll GET /v1/stories/{id}/status
    for progress.
    """
    story_id = await start_generation(body.premise, body.label)
    return _GenerateResponse(story_id=story_id)
