"""LLM-based text normalisation with disk cache."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "v1"
_DEFAULT_CACHE_DIR = Path("outputs") / "text_norm_cache"

_SYSTEM_PROMPT = (
    "You are a text normalization assistant. Rewrite the user's text so it sounds "
    "natural when spoken aloud by a text-to-speech system. Rules:\n"
    "1. Expand dates: '07-02-2017' → 'July second, twenty seventeen'. "
    "Use context to determine month/day order.\n"
    "2. Flatten dotted identifiers that are file names, technical tokens, or "
    "proper nouns with dots used as separators (e.g. 'Left.Right.AS' → "
    "'Left Right AS'). Do NOT flatten standard abbreviations like 'U.S.', "
    "'Mr.', 'e.g.', 'Dr.', 'vs.' — leave those exactly as they are.\n"
    "3. Expand other abbreviations or symbols only when their spoken form is "
    "unambiguous (e.g. '$100' → 'one hundred dollars').\n"
    "4. Do NOT rephrase, summarize, or change the meaning. Preserve all content.\n"
    "5. Output ONLY the rewritten text with no explanation, preamble, or markdown."
)

# Lazy-loaded model globals (module-level singleton)
_model = None
_tokenizer = None
_loaded_model_id: Optional[str] = None


def _unload_model():
    """Unload the Qwen model and free GPU memory."""
    global _model, _tokenizer, _loaded_model_id
    if _model is None:
        return
    import gc
    logger.info("Unloading LLM normalisation model…")
    del _model
    del _tokenizer
    _model = None
    _tokenizer = None
    _loaded_model_id = None
    gc.collect()
    gc.collect()  # Second pass for circular refs
    try:
        import torch as _torch
        if _torch.cuda.is_available():
            _torch.cuda.empty_cache()
            _torch.cuda.synchronize()
    except Exception:
        pass
    logger.info("LLM normalisation model unloaded.")


def _cache_key(text: str, model_id: str) -> str:
    payload = f"{_PROMPT_VERSION}|{model_id}|{text}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _load_model(model_id: str):
    global _model, _tokenizer, _loaded_model_id
    if _model is not None and _loaded_model_id == model_id:
        return _model, _tokenizer
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch as _torch
        logger.info("Loading LLM normalisation model: %s", model_id)
        _tokenizer = AutoTokenizer.from_pretrained(model_id)
        device = "cuda" if _torch.cuda.is_available() else "cpu"
        _model = AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype=_torch.float16,
        ).to(device)
        _loaded_model_id = model_id
        logger.info("LLM normalisation model loaded: %s", model_id)
        return _model, _tokenizer
    except Exception as exc:
        logger.error("Failed to load LLM normalisation model '%s': %s", model_id, exc)
        return None, None


def normalize_text_with_llm(
    text: str,
    model_id: str = "Qwen/Qwen2.5-1.5B-Instruct",
    max_new_tokens: int = 2048,
    cache_dir: Optional[Path] = None,
) -> Tuple[str, bool]:
    """Normalise text for TTS via LLM. Returns (normalised_text, was_cached)."""
    if not text or not text.strip():
        return text, False

    cache_dir = cache_dir or _DEFAULT_CACHE_DIR
    key = _cache_key(text, model_id)
    cached_path = cache_dir / f"{key}.json"

    if cached_path.exists():
        try:
            payload = json.loads(cached_path.read_text(encoding="utf-8"))
            cached = payload.get("normalized_text", "")
            if cached:
                logger.debug("Normalisation cache hit (%s…).", key[:12])
                return cached, True
        except Exception as exc:
            logger.warning("Failed to read normalisation cache %s: %s", cached_path, exc)

    try:
        model, tokenizer = _load_model(model_id)
        if model is None or tokenizer is None:
            logger.warning("Normalisation LLM unavailable — returning original text.")
            return text, False

        import torch as _torch

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        if hasattr(tokenizer, "apply_chat_template"):
            formatted = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            formatted = f"<|system|>{_SYSTEM_PROMPT}</s><|user|>{text}</s><|assistant|>"

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
        normalized = tokenizer.decode(output_ids[0][input_len:], skip_special_tokens=True).strip()

        if not normalized:
            logger.warning("LLM normalisation returned empty output.")
            return text, False
        if len(normalized) < max(1, len(text) // 4):
            logger.warning("LLM normalisation output suspiciously short.")
            return text, False
        if len(normalized) > len(text) * 4:
            logger.warning("LLM normalisation output suspiciously long.")
            return text, False

        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached_path.write_text(
                json.dumps(
                    {"normalized_text": normalized, "source_text_len": len(text),
                     "model_id": model_id, "prompt_version": _PROMPT_VERSION},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to write normalisation cache: %s", exc)

        logger.info("Text normalised (%d → %d chars).", len(text), len(normalized))
        return normalized, False

    except Exception as exc:
        logger.warning("LLM text normalisation failed (%s) — returning original.", exc, exc_info=True)
        return text, False
