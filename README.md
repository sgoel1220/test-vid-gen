# Chatterbox TTS Lite

Monorepo for the creepy pasta audio production pipeline.

## Services

| Service | Purpose |
|---------|---------|
| `services/tts-server` | Stateless Chatterbox GPU pod with `/synthesize` only |
| `services/image-server` | Stateless image generation GPU pod |
| `services/creepy-brain` | Story generation, workflow orchestration, chunking, validation, retry, stitching, and storage |

## TTS Server

The TTS image is intentionally minimal. Active runtime files are:

- `services/tts-server/minimal_server.py`
- `services/tts-server/Dockerfile`
- `services/tts-server/voices/`

All text normalization, chunking, audio validation, retry logic, and final stitching live in `creepy-brain`.

### API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Process health |
| `GET` | `/ready` | Whether the Chatterbox model has loaded |
| `POST` | `/synthesize` | Stateless single-shot TTS, returning WAV bytes |

Example:

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

## Deployment

GitHub Actions builds and pushes container images on pushes to `main` and tags:

- `ghcr.io/sgoel1220/tts-server:main`
- `ghcr.io/sgoel1220/image-server:main`
- `ghcr.io/sgoel1220/creepy-brain:main`

## Local Checks

```bash
cd services/tts-server
python3 -m py_compile minimal_server.py

cd ../creepy-brain
python3 -m pytest
```
