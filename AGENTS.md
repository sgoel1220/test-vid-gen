# Agent Instructions

This is the canonical repo instruction file. `CLAUDE.md` is a symlink to this file.

## Response Style

- Keep responses short by default.
- Expand only when explicitly asked.

## Bead Workflow

When implementing beads (work items tracked in the `.beads/` system), **ALWAYS** follow this workflow:

1. **Pick a bead** - Choose a ready bead (no blockers)
2. **Implement** - Complete all required changes
3. **Test** - Thoroughly verify everything works
4. **Commit** - Create a proper git commit with descriptive message
5. **Merge** - Exit worktree and merge branch back to main
6. **Mark done** - Close the bead with `mcp__beads__close`
7. **Push** - Push changes to remote with `git push`

**CRITICAL RULES:**
- NEVER mark a bead as done before committing, merging, and pushing
- Work is NOT complete until `git push` succeeds
- Test thoroughly before committing
- Only close the bead after all changes are pushed to remote

## Current Product Shape

This repo is a **monorepo** containing multiple services for the Creepy Pasta audio production pipeline:

- **tts-server** — FastAPI TTS backend with OpenAI-compatible API and web UI
- **metadata-server** — Run metadata storage and audio blob service
- **story-engine** — LLM-powered story generation pipeline

## Project Structure

```
Chatterbox-TTS-Server/
├── services/
│   ├── tts-server/           # Main TTS service
│   │   ├── app.py            # FastAPI app factory
│   │   ├── lite_clone_server.py  # Entrypoint
│   │   ├── routes.py         # Core API handlers
│   │   ├── engine.py         # Model loading & synthesis
│   │   ├── audio/            # Audio encoding, processing
│   │   ├── text/             # Text chunking, normalization
│   │   ├── image/            # Image generation
│   │   ├── persistence/      # SQLite outbox + httpx client
│   │   ├── lite_ui/          # Web frontend
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── metadata-server/      # Run metadata storage
│   │   ├── app/
│   │   ├── alembic/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   │
│   └── story-engine/         # LLM story generation
│       ├── app/
│       ├── Dockerfile
│       └── pyproject.toml
│
├── creepy_pasta_protocol/    # Shared Pydantic models
├── AGENTS.md                 # This file
├── CLAUDE.md -> AGENTS.md
└── README.md
```

## TTS Server Architecture

| File / Dir | Role |
|------------|------|
| `app.py` | FastAPI app factory, middleware, static file mounts, lifespan |
| `lite_clone_server.py` | Entrypoint — re-exports `app` from `app.py` for backward compat |
| `routes.py` | Core API route handlers |
| `routes_history.py` | History proxy routes — forward `/api/history/*` to metadata-svc |
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
| `persistence/` | SQLite outbox + typed httpx client for the metadata server |

## API Endpoints (TTS Server)

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
| GET | `/api/history` | List TTS runs (proxied from metadata-svc) |
| GET | `/api/history/{run_id}` | Get a single run detail (proxied) |
| GET | `/api/history/audio/{blob_id}` | Stream audio from metadata-svc |

## Commands

```bash
# Install dependencies (from repo root)
cd services/tts-server && python3 -m pip install -r requirements.txt
python3 -m pip install -e ./creepy_pasta_protocol

# Start the TTS server
cd services/tts-server && python3 lite_clone_server.py

# Syntax-check TTS server modules
cd services/tts-server && python3 -m py_compile app.py config.py cpu_runtime.py engine.py enums.py files.py job_store.py lite_clone_server.py models.py routes.py routes_history.py run_orchestrator.py utils.py && echo OK

# Type-check persistence layer and protocol (from repo root)
python3 -m mypy services/tts-server/persistence creepy_pasta_protocol/src
```

## GPU Rules

- **Always use CUDA directly.** Never use `device_map="auto"` or `accelerate` — this server runs on a single RunPod GPU. Load models with `.to("cuda")` instead.
- Models cannot coexist in VRAM. When swapping between TTS and SDXL, always unload one before loading the other.

## Deploy on RunPod

Build and push from the **repo root** (`Chatterbox-TTS-Server/`). **Always specify `--platform linux/amd64`** — RunPod runs on amd64 and a Mac arm64 build will fail with "no matching manifest" at pod start.

### TTS Server
```bash
docker buildx build --platform linux/amd64 \
  -f services/tts-server/Dockerfile \
  -t shubh67678/chatterbox-tts-server:latest \
  --push .
```

### Metadata Server
```bash
docker buildx build --platform linux/amd64 \
  -f services/metadata-server/Dockerfile \
  -t shubh67678/metadata-server:latest \
  --push .
```

### Story Engine
```bash
docker buildx build --platform linux/amd64 \
  -f services/story-engine/Dockerfile \
  -t shubh67678/story-engine:latest \
  --push .
```

Docker Hub images:
- `shubh67678/chatterbox-tts-server:latest` — TTS server
- `shubh67678/metadata-server:latest` — Metadata server
- `shubh67678/story-engine:latest` — Story engine

RunPod template: `chatterbox-lite` · port 8005 · Nvidia GPU · 25 GB container disk · ≥20 GB volume disk (to persist model cache across restarts).

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
