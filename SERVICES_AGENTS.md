# Service Notes

The canonical repo instructions live in `AGENTS.md`.

## Current Runtime Shape

- `services/tts-server` is a minimal stateless TTS pod (Chatterbox model, port 8005).
- `services/image-server` is a minimal stateless image generation pod (SDXL Lightning, port 8006).
- `services/creepy-brain` owns orchestration, persistence, text processing, audio validation, retry behavior, image generation orchestration, and final stitching. Uses a **custom workflow engine** (`app/engine/`).

## TTS Server

Active runtime files:

- `services/tts-server/minimal_server.py`
- `services/tts-server/Dockerfile`
- `services/tts-server/voices/` (reference voice WAV files)

Endpoints:

- `GET /health`
- `GET /ready`
- `POST /synthesize` — `{text, voice, seed}` → WAV bytes

Quality gate:

```bash
cd services/tts-server
python3 -m py_compile minimal_server.py
```

## Image Server

Active runtime files:

- `services/image-server/server.py`
- `services/image-server/Dockerfile`

Endpoints:

- `GET /health`
- `GET /ready`
- `POST /generate` — `{prompt, width, height}` → PNG bytes

Quality gate:

```bash
cd services/image-server
python3 -m py_compile server.py
```

## creepy-brain

Active runtime: `services/creepy-brain/app/`

Key subsystems:

| Subsystem | Location | Purpose |
|-----------|----------|---------|
| Workflow engine | `app/engine/` | Custom DAG executor with pause/resume, step retry |
| Content pipeline | `app/workflows/content_pipeline.py` | story → tts → image → stitch → cleanup |
| Story pipeline | `app/pipeline/` | LLM architect → writer → reviewer loop |
| LLM client | `app/llm/client.py` | Anthropic + OpenRouter providers |
| Text processing | `app/text/` | Normalization, chunking, scene grouping |
| Audio processing | `app/audio/` | Validation (numpy), WAV→MP3 encoding |
| GPU lifecycle | `app/gpu/` | RunPod provider, DB-tracked pod management |
| Recon | `app/workflows/recon.py` | Orphaned pod cleanup cron |

Quality gate:

```bash
cd services/creepy-brain
python3 -m pytest tests/ -v
python3 -m mypy app/ --strict
```
