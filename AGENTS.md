# Agent Instructions

This is the canonical repo instruction file. `CLAUDE.md` is a symlink to this file.

## Response Style

- Keep responses short by default.
- Expand only when explicitly asked.

## Python Typing Standards

**All Python code in this repo MUST be statically typed.** This is a non-negotiable requirement.

### Type Annotation Requirements

1. **All functions MUST have type hints:**
   ```python
   def process_audio(data: bytes, sample_rate: int) -> AudioChunk:
       ...
   ```

2. **All function parameters MUST be typed:**
   - No bare `def foo(x):` — always `def foo(x: str):`
   - Use specific types, not `Any` unless absolutely necessary

3. **All return types MUST be annotated:**
   - Include `-> None` for functions that don't return a value
   - Use `-> ReturnType` for all other functions

4. **All variables with non-obvious types MUST be annotated:**
   ```python
   chunks: list[AudioChunk] = []
   metadata: dict[str, Any] = {}
   ```

### Pydantic Models

**CRITICAL RULE: NEVER use `dict` as a return type. ALWAYS create a Pydantic model.**

1. **Use Pydantic for ALL data structures:**
   - API request/response models
   - Configuration objects
   - Domain models
   - Internal data transfer objects
   - **Function return values with structured data**

2. **Never use raw dicts or tuples for structured data:**
   ```python
   # ❌ BAD - dict return type
   def create_job(data: dict) -> dict:
       return {"id": 123, "status": "pending"}

   # ❌ BAD - dict[str, str] is also prohibited
   async def get_status() -> dict[str, str]:
       return {"status": "ok"}

   # ✅ GOOD - Pydantic model
   class StatusResponse(BaseModel):
       status: str

   async def get_status() -> StatusResponse:
       return StatusResponse(status="ok")

   # ✅ GOOD - JobResponse model
   def create_job(data: JobRequest) -> JobResponse:
       return JobResponse(id=123, status=JobStatus.PENDING)
   ```

3. **Pydantic model requirements:**
   - Use `BaseModel` for all structured data
   - Use `Field()` for validation and documentation
   - Use enums for constrained string values
   - Use `ConfigDict` for model configuration
   - Create response models even for simple endpoints

### Type-Checking Enforcement

1. **Run mypy on all new/modified code:**
   ```bash
   python3 -m mypy path/to/module.py --strict
   ```

2. **mypy must pass with no errors before commit**

3. **Prefer `--strict` mode settings:**
   - `disallow_untyped_defs = True`
   - `disallow_any_unimported = True`
   - `warn_return_any = True`
   - `strict_optional = True`

### Import Standards

1. **Use modern type hint syntax:**
   ```python
   from typing import Protocol, TypeVar, Generic
   from collections.abc import Sequence, Mapping
   ```

2. **For Python 3.9+, prefer built-in generics:**
   ```python
   # ✅ GOOD (Python 3.9+)
   def process(items: list[str]) -> dict[str, int]:
       ...

   # ❌ BAD (old style)
   from typing import List, Dict
   def process(items: List[str]) -> Dict[str, int]:
       ...
   ```

### Validation Rules

1. **Never trust external input:**
   - Use Pydantic validation for all API inputs
   - Use type guards for runtime type checking when needed

2. **Make impossible states unrepresentable:**
   ```python
   # Use unions and enums instead of optional fields
   class JobComplete(BaseModel):
       status: Literal[JobStatus.COMPLETE]
       result: AudioResult

   class JobFailed(BaseModel):
       status: Literal[JobStatus.FAILED]
       error: str

   JobResult = JobComplete | JobFailed
   ```

### Critical Rules

- **NEVER merge code without type hints**
- **NEVER use `Any` without a comment explaining why**
- **NEVER use `# type: ignore` without a specific reason**
- **ALWAYS validate external input with Pydantic**
- **ALWAYS prefer Pydantic models over dicts/tuples**
- **ALWAYS run mypy before committing**

## Filing Beads During Implementation

**When working on a bead, you WILL discover issues that are out of scope for the current bead.** Do NOT ignore them or fix them inline — file them as beads immediately so they are tracked and can be prioritized.

### What to file

File a bead for any issue you discover that:
- Is out of scope for the current bead (different file area, different concern)
- Represents a genuine bug, gap, or missing piece in the system
- Was flagged by the adversarial review but conflicts with the current bead's intent
- Is a follow-up that blocks future beads (e.g., an API contract that doesn't exist yet)

### How to file

```python
mcp__beads__create(
    title="Short imperative title",
    issue_type="bug" | "task" | "chore",
    priority=1 | 2 | 3,   # 1=critical/blocker, 2=normal, 3=nice-to-have
    labels=["relevant", "tags"],
    description="""
    What the problem is, why it matters, and how to fix it.
    Include acceptance criteria if you know them.
    """,
)
```

### When to file

- **During implementation**: as soon as you discover the issue, before continuing
- **After adversarial review**: for any finding you cannot fix in the current bead
- **At session end**: sweep for anything you noticed but didn't act on

### Critical rule

**NEVER silently leave a known issue unfiled.** If you see a problem and don't fix it, it MUST become a bead. "I'll remember it" is not acceptable — the bead system is the memory.

## Bead Workflow

When implementing beads (work items tracked in the `.beads/` system), **ALWAYS** follow this workflow:

1. **Create worktree** - BEFORE any code changes, create an isolated worktree:
   ```bash
   # Use EnterWorktree tool to create isolated branch
   EnterWorktree(description="Implement bead XYZ")
   ```
2. **Pick a bead** - Choose a ready bead with "open" status (no blockers) using `mcp__beads__ready`
   - **ONLY pick beads with status "open"**
   - **NEVER pick beads with status "in_progress"** - another agent is working on them
3. **Review documentation** - Get high-level understanding BEFORE implementing:
   - Read `docs/CONTENT_PIPELINE_ORCHESTRATION.md` for architecture context
   - Review related documentation for the feature area
   - Understand the "why" behind the implementation
   - **ASK QUESTIONS** if anything is unclear - NEVER assume
4. **Implement** - Complete all required changes in the worktree
   - **Prioritize SIMPLICITY** - choose the simplest solution that works
   - Avoid over-engineering, premature abstractions, or unnecessary complexity
   - Write clear, straightforward code
5. **Test** - Thoroughly verify everything works
6. **Adversarial review** - MANDATORY: inside the worktree, run `/adversarial-review` in the **foreground** (never background):
   - Wait for the review to complete — do NOT proceed while it is still running
   - Implement every actionable finding in the worktree before moving on
   - If findings conflict with the bead's intent, stop and ASK — do not silently ignore them
   - Re-test after applying review fixes
7. **Commit** - Create a proper git commit with descriptive message (review fixes may be the same commit or a follow-up commit in the worktree)
8. **Merge** - Exit worktree and merge branch back to main:
   ```bash
   ExitWorktree(action="keep")
   git merge worktree-<name> --no-edit
   ```
   - If merge conflicts occur, resolve them carefully
   - Test again after resolving conflicts
   - Complete the merge before proceeding
9. **Mark done** - Close the bead with `mcp__beads__close` ONLY after successful merge
10. **Push** - Push changes to remote with `git push origin main`
11. **Clean up the worktree** - ONLY after `git push` succeeds. Run ALL commands from the **repo root** (not inside the worktree):
    ```bash
    git worktree remove .claude/worktrees/<name>   # remove worktree directory first
    git branch -d worktree-<name>                   # then delete local branch
    git worktree prune                              # clean any stale worktree entries
    git status                                      # verify clean state
    ```
    - Use `-d` (safe delete), not `-D`, so git refuses if the branch isn't merged
    - If the branch has an upstream on origin, also delete it: `git push origin --delete worktree-<name>`
    - **IMPORTANT**: You cannot delete a branch while your shell is inside that worktree — always `cd` to repo root first or use `git -C /path/to/repo`

**CRITICAL RULES:**
- NEVER pick a bead with status "in_progress" - another agent is working on it
- ONLY pick beads with status "open"
- NEVER make code changes directly on main - ALWAYS use a worktree
- NEVER start implementing without understanding the context - review docs first
- NEVER assume - ASK QUESTIONS if anything is unclear or ambiguous
- ALWAYS prioritize SIMPLICITY over cleverness or premature optimization
- NEVER skip `/adversarial-review` - it is a required gate before merging
- NEVER run `/adversarial-review` in the background - it MUST run in the foreground so its findings are applied before commit/merge
- NEVER merge before applying adversarial-review findings
- NEVER mark a bead as done before committing, merging, and pushing
- NEVER delete the worktree branch before `git push origin main` succeeds
- Work is NOT complete until `git push` succeeds AND the worktree branch is deleted
- Test thoroughly before committing
- If merge conflicts occur, resolve them before closing the bead
- Only close the bead after all changes are successfully pushed to remote

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
