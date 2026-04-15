# Agent Instructions

This is the canonical repo instruction file. `CLAUDE.md` is a symlink to this file.

## Response Style

- Keep responses short by default.
- Expand only when explicitly asked.

## Current Product Shape

This repo is the **lite self-hosted Chatterbox TTS server** — a FastAPI backend with an OpenAI-compatible API and a minimal web UI (`lite_ui/`). It is designed to run on RunPod (GPU) via the `lite_runpod/` Docker setup.

The active runtime loop:
- Start `lite_clone_server.py` on a GPU machine
- Generate audio from plain text via the UI or API
- Voice cloning via reference audio upload
- Supports three model variants: original, turbo, and multilingual

## Architecture

| File / Dir | Role |
|------------|------|
| `app.py` | FastAPI app factory, middleware, static file mounts, lifespan |
| `lite_clone_server.py` | Entrypoint — re-exports `app` from `app.py` for backward compat |
| `routes.py` | All API route handlers |
| `engine.py` | Model loading (original/turbo/multilingual) and synthesis orchestration |
| `run_orchestrator.py` | TTS job execution: settings resolution, chunk synthesis, artifact saving |
| `config.py` | `config.yaml` defaults and access helpers |
| `cpu_runtime.py` | CPU/MPS fallback runtime thread configuration |
| `enums.py` | Shared enumerations (AudioFormat, ModelType, DeviceType, JobStatus) |
| `files.py` | Reference audio validation, predefined voice listing, PerformanceMonitor |
| `job_store.py` | Thread-safe in-memory async job state (Repository pattern) |
| `models.py` | Pydantic request/response models |
| `utils.py` | Backward-compat shim — re-exports from `audio/`, `text/`, `files.py`, `models.py` |
| `audio/` | Audio encoding (`encoding.py`), processing (`processing.py`), stitching (`stitching.py`) |
| `text/` | Text chunking (`chunking.py`) and LLM-based normalization (`normalization.py`) |
| `lite_ui/` | Minimal browser-facing frontend (HTML/CSS/JS) |
| `lite_runpod/` | Dockerfile, requirements, entrypoint, config for RunPod deployment |
| `config.yaml` | Runtime configuration source of truth |
| `voices/` | Predefined voice WAV assets (27 built-in voices) |
| `reference_audio/` | Voice-cloning reference inputs |

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Serve lite UI |
| GET | `/api/model-info` | Model status, capabilities, supported languages |
| GET | `/api/reference-audio` | List valid reference audio files |
| POST | `/api/reference-audio/upload` | Upload a .wav/.mp3 reference file |
| POST | `/api/chunks/preview` | Preview text chunking without synthesis |
| POST | `/tts` | Synchronous TTS generation (returns full run response) |
| POST | `/api/jobs` | Create async TTS job (returns job_id + status_url) |
| GET | `/api/jobs/{job_id}` | Poll async job progress and result |

## Commands

```bash
# Install dependencies
python3 -m pip install -r lite_runpod/requirements.txt

# Start the lite server
python3 lite_clone_server.py

# Syntax-check all modules
python3 -m py_compile app.py config.py cpu_runtime.py engine.py enums.py files.py job_store.py lite_clone_server.py models.py routes.py run_orchestrator.py utils.py && echo OK
```

## Deploy on RunPod

Build and push from the repo root (`Chatterbox-TTS-Server/`). **Always specify `--platform linux/amd64`** — RunPod runs on amd64 and a Mac arm64 build will fail with "no matching manifest" at pod start.

```bash
docker buildx build --platform linux/amd64 \
  -f lite_runpod/Dockerfile \
  -t shubh67678/chatterbox-lite-runpod:latest \
  -t shubh67678/chatterbox-tts-server:latest \
  --push .
```

Docker Hub images:
- `shubh67678/chatterbox-lite-runpod:latest` — primary
- `shubh67678/chatterbox-tts-server:latest` — alias (same digest)

RunPod template: `chatterbox-lite` · port 8005 · Nvidia GPU · 25 GB container disk · ≥20 GB volume disk (to persist model cache across restarts).
