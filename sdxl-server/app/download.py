"""Download models and LoRAs from CivitAI / HuggingFace at container startup."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [download] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

CIVITAI_TOKEN = os.getenv("CIVITAI_TOKEN", "")
CIVITAI_DL = "https://civitai.com/api/download/models/{id}"

MODELS_DIR = Path("/models")
LORAS_DIR = Path(os.getenv("LORAS_DIR", "/loras"))
BASE_PATH = MODELS_DIR / "base.safetensors"

CHUNK = 8 * 1024 * 1024  # 8 MB


def _download(url: str, dest: Path, label: str) -> None:
    if dest.exists():
        size_gb = dest.stat().st_size / 1024**3
        log.info("SKIP %s — already exists at %s (%.2f GB)", label, dest, size_gb)
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")

    headers = {}
    if "civitai.com" in url and CIVITAI_TOKEN:
        headers["Authorization"] = f"Bearer {CIVITAI_TOKEN}"

    log.info("START %s → %s", label, dest)
    t0 = time.monotonic()

    try:
        with requests.get(url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            log.info("  size: %.2f GB", total / 1024**3 if total else 0)

            downloaded = 0
            last_log = 0.0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=CHUNK):
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    # Log progress every 10 seconds
                    if now - last_log >= 10:
                        if total:
                            pct = downloaded / total * 100
                            speed = downloaded / (now - t0) / 1024**2
                            log.info("  %.1f%%  %.2f / %.2f GB  (%.1f MB/s)",
                                     pct, downloaded / 1024**3, total / 1024**3, speed)
                        last_log = now

        tmp.rename(dest)
        elapsed = time.monotonic() - t0
        final_gb = dest.stat().st_size / 1024**3
        log.info("DONE %s — %.2f GB in %.0fs", label, final_gb, elapsed)

    except Exception as exc:
        tmp.unlink(missing_ok=True)
        log.error("FAILED %s: %s", label, exc)
        return


def _civitai_url(version_id: str) -> str:
    url = CIVITAI_DL.format(id=version_id.strip())
    if CIVITAI_TOKEN:
        url += f"?token={CIVITAI_TOKEN}"
    return url


def _hf_url(repo_path: str) -> str:
    parts = repo_path.split("/", 2)
    if len(parts) < 3:
        raise ValueError(f"HF path must be 'owner/repo/filename', got: {repo_path}")
    owner, repo, filename = parts[0], parts[1], parts[2]
    token_param = f"?token={os.getenv('HF_TOKEN')}" if os.getenv("HF_TOKEN") else ""
    return f"https://huggingface.co/{owner}/{repo}/resolve/main/{filename}{token_param}"


def main() -> None:
    log.info("=== startup download begin ===")

    # ── Base checkpoint ──────────────────────────────────────────────────────
    civitai_base = os.getenv("BASE_CIVITAI_ID", "").strip()
    if civitai_base:
        log.info("Base model: CivitAI version %s", civitai_base)
        _download(_civitai_url(civitai_base), BASE_PATH, "base model")
        Path("/tmp/env_extra").write_text(f"BASE_MODEL_PATH={BASE_PATH}\n")
        log.info("BASE_MODEL_PATH=%s", BASE_PATH)
    else:
        log.info("BASE_CIVITAI_ID not set — server will load from HuggingFace at runtime")

    # ── CivitAI LoRAs ────────────────────────────────────────────────────────
    lora_ids_raw = os.getenv("LORA_CIVITAI_IDS", "").strip()
    if lora_ids_raw:
        for entry in lora_ids_raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                log.warning("Skipping bad LORA_CIVITAI_IDS entry (expected name:id): %s", entry)
                continue
            name, version_id = entry.split(":", 1)
            dest = LORAS_DIR / f"{name.strip()}.safetensors"
            _download(_civitai_url(version_id.strip()), dest, f"LoRA {name.strip()}")
    else:
        log.info("LORA_CIVITAI_IDS not set — no CivitAI LoRAs to download")

    # ── HuggingFace LoRAs ────────────────────────────────────────────────────
    lora_hf_raw = os.getenv("LORA_HF_REPOS", "").strip()
    if lora_hf_raw:
        for entry in lora_hf_raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                log.warning("Skipping bad LORA_HF_REPOS entry (expected name:owner/repo/file): %s", entry)
                continue
            name, repo_path = entry.split(":", 1)
            dest = LORAS_DIR / f"{name.strip()}.safetensors"
            _download(_hf_url(repo_path.strip()), dest, f"LoRA {name.strip()} (HF)")
    else:
        log.info("LORA_HF_REPOS not set — no HuggingFace LoRAs to download")

    log.info("=== startup download complete ===")


if __name__ == "__main__":
    main()
