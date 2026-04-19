# Chatterbox TTS Lite — Architecture Overview

## What It Is
Creepy Pasta audio/video production pipeline. Generates horror stories via LLM, narrates via TTS, generates scene images via SDXL, stitches into final audio/video.

## Three Services (monorepo)
1. **tts-server** (`services/tts-server/minimal_server.py`) — Stateless Chatterbox TTS on RunPod GPU. `POST /synthesize {text, voice, seed} → WAV`. Port 8005.
2. **image-server** (`services/image-server/server.py`) — Stateless SDXL Lightning on RunPod GPU. `POST /generate {prompt, width, height} → PNG`. Port 8006.
3. **creepy-brain** (`services/creepy-brain/app/`) — Central orchestrator (FastAPI + SQLAlchemy + Postgres).

## Custom Workflow Engine (NOT Hatchet)
Custom engine:
- `app/engine/engine.py` — WorkflowEngine: trigger, retry_step, pause, resume, cancel
- `app/engine/runner.py` — WorkflowRunner: topo-sort DAG execution, DB state persistence
- `app/engine/scheduler.py` — CronScheduler for periodic workflows
- `app/engine/models.py` — StepDef, WorkflowDef, StepContext

## Content Pipeline Steps
1. **story** (`workflows/steps/story.py`) — LLM story generation via pipeline/orchestrator
2. **tts** (`workflows/steps/tts.py`) — Normalize text, chunk sentences, per-chunk synthesis with seed-increment retry
3. **image** (`workflows/steps/image.py`) — Group chunks into scenes, LLM image prompts, per-scene image generation
4. **stitch** (`workflows/steps/stitch.py`) — Concat WAV → MP3, optional video with images
5. **cleanup** (`workflows/steps/cleanup.py`) — Terminate GPU pods

## LLM Providers
- `app/llm/client.py` — AnthropicProvider and OpenRouterProvider (NOT OpenAI directly)
- `generate_structured()` and `generate_text()` with retry logic

## Story Pipeline
- `app/pipeline/orchestrator.py` — run_pipeline() with architect → writer → reviewer loop
- `app/pipeline/architect.py` — Outline generation
- `app/pipeline/writer.py` — Act-by-act writing
- `app/pipeline/reviewer.py` — Quality scoring with revision loop

## Text Processing
- `app/text/normalization.py` — LLM-based normalization for TTS readability
- `app/text/chunking.py` — Sentence-based chunking
- `app/text/scene_grouping.py` — Group chunks into scenes for image generation

## Audio
- `app/audio/validation.py` — RMS, peak, voiced-ratio checks (numpy)
- `app/audio/encoding.py` — WAV → MP3 (soundfile)

## GPU Management
- `app/gpu/base.py` — GpuProvider protocol, GpuPodSpec, GpuPod dataclasses
- `app/gpu/runpod.py` — RunPod implementation
- `app/gpu/lifecycle.py` — DB-tracked pod create/wait/terminate
- `app/workflows/recon.py` — Orphaned pod cleanup cron

## Database
- SQLAlchemy async with asyncpg, Postgres
- Alembic migrations (10 versions as of April 2026)
- Models: Workflow, WorkflowStep, WorkflowChunk (with scenes), Story, StoryAct, Run, RunChunk, Voice, GpuPod
- Blobs stored in Postgres BYTEA

## Key Enums (`app/models/enums.py`)
WorkflowType, WorkflowStatus, StepName, StepStatus, ChunkStatus, GpuProvider, GpuPodStatus, BlobType, StoryStatus, RunStatus

## CI/CD
- GitHub Actions builds Docker images on push to main/tags
- GHCR: ghcr.io/sgoel1220/{tts-server,image-server,creepy-brain}:main
- RunPod deployment: community cloud, spot instances, no volumes

## Recent Features (April 2026)
- Pause/resume API at workflow level
- Step-level retry
- Sub-unit resume for TTS, stitch, and story steps
