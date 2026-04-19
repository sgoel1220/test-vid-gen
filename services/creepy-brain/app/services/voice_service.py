"""Voice service — CRUD for voice references."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.voice import Voice
from app.services.errors import ResourceNotFoundError


class VoiceCreateResult(BaseModel):
    """Result of an idempotent voice create operation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    voice: Voice = Field(description="Existing or newly created voice")
    created: bool = Field(description="True when a new voice row was inserted")


async def get_by_name(session: AsyncSession, name: str) -> Voice | None:
    """Return the Voice with *name*, or None if it does not exist."""
    result = await session.execute(select(Voice).where(Voice.name == name))
    return result.scalar_one_or_none()


async def _clear_default(session: AsyncSession) -> None:
    """Unset is_default on any currently-default voice (flush only).

    Must be called inside the same transaction as setting the new default so
    the partial unique index ``uq_voices_single_default`` is never violated.
    """
    await session.execute(
        update(Voice).where(Voice.is_default.is_(True)).values(is_default=False)
    )


async def create(
    session: AsyncSession,
    name: str,
    audio_path: str | None = None,
    audio_blob_id: uuid.UUID | None = None,
    description: str | None = None,
    is_default: bool = False,
) -> Voice:
    """Insert a new voice; does NOT check for duplicates (caller must check).

    Args:
        session: Active async session (flush only — caller commits).
        name: Unique voice name.
        audio_path: Filesystem path for a built-in voice file.  Mutually
            exclusive with *audio_blob_id* but both are nullable.
        audio_blob_id: UUID of the workflow_blobs row for an uploaded voice.
        description: Optional human-readable description.
        is_default: Whether this voice should be the system default.  Setting
            this True clears the previous default within the same transaction.
    """
    if is_default:
        await _clear_default(session)
    voice = Voice(
        id=uuid.uuid4(),
        name=name,
        description=description,
        audio_path=audio_path,
        audio_blob_id=audio_blob_id,
        is_default=is_default,
    )
    session.add(voice)
    await session.flush()
    return voice


async def set_default(session: AsyncSession, voice_id: uuid.UUID) -> Voice:
    """Set the given voice as the default, clearing any previous default (flush only).

    Args:
        session: Active async session.
        voice_id: The voice to mark as default.

    Returns:
        The updated Voice.

    Raises:
        ResourceNotFoundError: If the voice is not found.
    """
    result = await session.execute(select(Voice).where(Voice.id == voice_id))
    voice = result.scalar_one_or_none()
    if voice is None:
        raise ResourceNotFoundError("Voice", voice_id)
    await _clear_default(session)
    voice.is_default = True
    await session.flush()
    return voice


async def get_or_create(
    session: AsyncSession,
    name: str,
    audio_path: str | None = None,
    audio_blob_id: uuid.UUID | None = None,
    description: str | None = None,
    is_default: bool = False,
) -> VoiceCreateResult:
    """Return the voice and whether it was created — idempotent by name."""
    existing = await get_by_name(session, name)
    if existing is not None:
        return VoiceCreateResult(voice=existing, created=False)
    voice = await create(
        session,
        name=name,
        audio_path=audio_path,
        audio_blob_id=audio_blob_id,
        description=description,
        is_default=is_default,
    )
    return VoiceCreateResult(voice=voice, created=True)


async def list_all(session: AsyncSession) -> list[Voice]:
    """Return all voices ordered by creation time (newest first)."""
    result = await session.execute(select(Voice).order_by(Voice.created_at.desc()))
    return list(result.scalars().all())
