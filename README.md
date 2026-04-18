# Chatterbox TTS Lite

Monorepo for the creepy pasta audio/video production pipeline. Generates horror stories via LLM, synthesizes narration via TTS, generates scene images via SDXL, and stitches everything into a final audio/video artifact.

## Services

| Service | Purpose | Port |
|---------|---------|------|
| `services/tts-server` | Stateless Chatterbox TTS GPU pod — `POST /synthesize` | 8005 |
| `services/image-server` | Stateless SDXL Lightning GPU pod — `POST /generate` | 8006 |
| `services/creepy-brain` | Orchestrator: story generation, workflow engine, text processing, audio validation, image orchestration, stitching, and storage | — |

## Architecture

**creepy-brain** uses a custom workflow engine (`app/engine/`) to run a content pipeline:

1. **Story** — Generate horror story via LLM (architect → writer → reviewer loop)
2. **TTS** — Normalize text, chunk into sentences, synthesize each chunk via TTS pod with retry
3. **Image** — Group chunks into scenes, generate image prompts via LLM, synthesize via image pod
4. **Stitch** — Concatenate WAV chunks → MP3, optionally create video with images
5. **Cleanup** — Terminate GPU pods

LLM providers: Anthropic + OpenRouter. GPU provider: RunPod.

## API

### TTS Server

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Process health |
| `GET` | `/ready` | Whether the Chatterbox model has loaded |
| `POST` | `/synthesize` | Stateless single-shot TTS, returning WAV bytes |

```bash
curl -X POST http://localhost:8005/synthesize \
  -H "Content-Type: application/json" \
  -o output.wav \
  -d '{
    "text": "Hello world",
    "voice": "Gianna.wav",
    "seed": 1234
  }'
```

### Image Server

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Process health |
| `GET` | `/ready` | Whether SDXL model has loaded (503 if loading) |
| `POST` | `/generate` | Image generation, returning PNG bytes |

```bash
curl -X POST http://localhost:8006/generate \
  -H "Content-Type: application/json" \
  -o output.png \
  -d '{
    "prompt": "A dark abandoned house at night",
    "width": 1024,
    "height": 1024
  }'
```

## Deployment

GitHub Actions builds and pushes container images on pushes to `main`, `docker-release`, tags, and PRs:

- `ghcr.io/sgoel1220/tts-server:main`
- `ghcr.io/sgoel1220/image-server:main`
- `ghcr.io/sgoel1220/creepy-brain:main`

## Local Checks

```bash
# TTS server
cd services/tts-server && python3 -m py_compile minimal_server.py

# Image server
cd services/image-server && python3 -m py_compile server.py

# creepy-brain
cd services/creepy-brain && python3 -m pytest tests/ -v
cd services/creepy-brain && python3 -m mypy app/ --strict
```
