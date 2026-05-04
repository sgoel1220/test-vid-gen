"""SRT caption generation from TTS chunk data."""

from pydantic import BaseModel, Field

from app.text.chunking import split_into_sentences


class CaptionChunk(BaseModel):
    text: str
    duration_sec: float = Field(gt=0)


def format_srt_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format ``HH:MM:SS,mmm``."""
    total_ms = round(seconds * 1000)
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(chunks: list[CaptionChunk]) -> str:
    """Generate an SRT subtitle string from a list of caption chunks.

    Each chunk's duration is distributed proportionally across its sentences
    by character count.  Timestamps are sequential with no gaps.

    Args:
        chunks: Ordered list of caption chunks with text and duration.

    Returns:
        Full SRT content as a string.  Returns ``""`` for empty input.
    """
    entries: list[str] = []
    index = 1
    cursor = 0.0

    for chunk in chunks:
        sentences = split_into_sentences(chunk.text)
        if not sentences:
            cursor += chunk.duration_sec
            continue

        chunk_text_len = sum(len(s) for s in sentences)

        for sentence in sentences:
            if chunk_text_len > 0:
                sentence_duration = chunk.duration_sec * len(sentence) / chunk_text_len
            else:
                sentence_duration = chunk.duration_sec / len(sentences)

            start = cursor
            end = cursor + sentence_duration
            entries.append(
                f"{index}\n"
                f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n"
                f"{sentence}"
            )
            index += 1
            cursor = end

    return "\n\n".join(entries)
