"""Sentence-aware text chunking for TTS synthesis."""

from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

# Abbreviations that should not be treated as sentence boundaries
_ABBREVIATIONS: set[str] = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "rev.", "hon.", "st.", "etc.",
    "e.g.", "i.e.", "vs.", "approx.", "apt.", "dept.", "fig.", "gen.",
    "gov.", "inc.", "jr.", "sr.", "ltd.", "no.", "p.", "pp.", "vol.",
    "op.", "cit.", "ca.", "cf.", "ed.", "esp.", "et.", "al.", "ibid.",
    "id.", "inf.", "sup.", "viz.", "sc.", "fl.", "d.", "b.", "r.", "c.",
    "v.", "u.s.", "u.k.", "a.m.", "p.m.", "a.d.", "b.c.",
}

_NUMBER_DOT_NUMBER = re.compile(r"(?<!\d\.)\d*\.\d+")
_VERSION = re.compile(r"[vV]?\d+(\.\d+)+")
_POTENTIAL_END = re.compile(r'([.!?])(["\']?)(\s+|$)')
_BULLET_POINT = re.compile(r"(?:^|\n)\s*([-•*]|\d+\.)\s+")
_NON_VERBAL_CUE = re.compile(r"(\([\w\s'-]+\))")


def _is_valid_sentence_end(text: str, period_idx: int) -> bool:
    scan_start = max(0, period_idx - 10)
    word_start = period_idx - 1
    while word_start >= scan_start and not text[word_start].isspace():
        word_start -= 1
    word = text[word_start + 1 : period_idx + 1].lower()
    if word in _ABBREVIATIONS:
        return False
    ctx_start = max(0, period_idx - 10)
    ctx = text[ctx_start : min(len(text), period_idx + 10)]
    rel = period_idx - ctx_start
    for pat in (_NUMBER_DOT_NUMBER, _VERSION):
        for m in pat.finditer(ctx):
            if m.start() <= rel < m.end():
                if not (
                    rel == m.end() - 1
                    and (period_idx + 1 == len(text) or text[period_idx + 1].isspace())
                ):
                    return False
    return True


def _split_by_punctuation(text: str) -> list[str]:
    sentences: list[str] = []
    last = 0
    for m in _POTENTIAL_END.finditer(text):
        punc_idx = m.start(1)
        punc = text[punc_idx]
        end = m.start(1) + 1 + len(m.group(2) or "")
        if punc in "!?":
            s = text[last:end].strip()
            if s:
                sentences.append(s)
            last = m.end()
        elif punc == ".":
            if (punc_idx > 0 and text[punc_idx - 1] == ".") or (
                punc_idx < len(text) - 1 and text[punc_idx + 1] == "."
            ):
                continue
            if _is_valid_sentence_end(text, punc_idx):
                s = text[last:end].strip()
                if s:
                    sentences.append(s)
                last = m.end()
    remainder = text[last:].strip()
    if remainder:
        sentences.append(remainder)
    sentences = [s for s in sentences if s]
    return sentences if sentences else ([text.strip()] if text.strip() else [])


def _split_into_sentences(text: str) -> list[str]:
    if not text or text.isspace():
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    bullet_matches = list(_BULLET_POINT.finditer(text))
    if not bullet_matches:
        return _split_by_punctuation(text)
    result: list[str] = []
    pos = 0
    for i, bm in enumerate(bullet_matches):
        bstart = bm.start()
        if i == 0 and bstart > pos:
            result.extend(s for s in _split_by_punctuation(text[pos:bstart].strip()) if s)
        next_start = bullet_matches[i + 1].start() if i + 1 < len(bullet_matches) else len(text)
        item = text[bstart:next_start].strip()
        if item:
            result.append(item)
        pos = next_start
    if pos < len(text):
        result.extend(s for s in _split_by_punctuation(text[pos:].strip()) if s)
    return [s for s in result if s]


def _segment_text(full_text: str) -> list[tuple[Optional[str], str]]:
    if not full_text or full_text.isspace():
        return []
    segments: list[tuple[Optional[str], str]] = []
    for part in _NON_VERBAL_CUE.split(full_text):
        if not part or part.isspace():
            continue
        if _NON_VERBAL_CUE.fullmatch(part):
            segments.append((None, part.strip()))
        else:
            segments.extend((None, s) for s in _split_into_sentences(part.strip()) if s)
    if not segments and full_text.strip():
        segments.append((None, full_text.strip()))
    return segments


def chunk_text_by_sentences(full_text: str, chunk_size: int = 1000) -> list[str]:
    """Chunk *full_text* into TTS-ready pieces respecting sentence boundaries.

    Args:
        full_text: The text to chunk.
        chunk_size: Maximum character length per chunk. 0 means no limit.

    Returns:
        List of text chunks suitable for sequential TTS synthesis.
    """
    if not full_text or full_text.isspace():
        return []
    if chunk_size <= 0:
        chunk_size = 10_000_000

    segments = _segment_text(full_text)
    if not segments:
        return []

    chunks: list[str] = []
    current: list[str] = []
    cur_len = 0

    for _, seg in segments:
        seg_len = len(seg)
        if not current:
            current.append(seg)
            cur_len = seg_len
        elif cur_len + 1 + seg_len <= chunk_size:
            current.append(seg)
            cur_len += 1 + seg_len
        else:
            chunks.append(" ".join(current))
            current = [seg]
            cur_len = seg_len

        if cur_len > chunk_size and len(current) == 1:
            chunks.append(" ".join(current))
            current = []
            cur_len = 0

    if current:
        chunks.append(" ".join(current))

    chunks = [c for c in chunks if c.strip()]
    if not chunks and full_text.strip():
        log.warning("chunking produced zero chunks — returning full text as single chunk")
        return [full_text.strip()]

    log.info("text chunking complete: %d chunk(s)", len(chunks))
    return chunks
