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

## Architecture

| File | Role |
|------|------|
| `app.py` | FastAPI app factory, middleware, mounts, lifespan |
| `lite_clone_server.py` | Entrypoint — re-exports `app` from `app.py` for backward compat |
| `routes.py` | All API route handlers |
| `engine.py` | Model loading and synthesis orchestration |
| `config.py` | `config.yaml` defaults and access helpers |
| `cpu_runtime.py` | CPU/MPS fallback runtime |
| `enums.py` | Shared enumerations |
| `files.py` | Reference audio validation and listing |
| `job_store.py` | In-memory async job state |
| `models.py` | Pydantic request/response models |
| `run_orchestrator.py` | TTS job execution logic |
| `utils.py` | Audio and file helpers |
| `text/` | Text chunking and normalization package |
| `lite_ui/` | Minimal browser-facing frontend (HTML/CSS/JS) |
| `lite_runpod/` | Dockerfile, requirements, entrypoint for RunPod deployment |
| `config.yaml` | Runtime configuration source of truth |
| `voices/` | Predefined voice WAV assets |
| `reference_audio/` | Voice-cloning reference inputs |

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
