# Service Notes

The canonical repo instructions live in `AGENTS.md`.

## Current Runtime Shape

- `services/tts-server` is a minimal stateless TTS pod.
- `services/image-server` is a minimal stateless image generation pod.
- `services/creepy-brain` owns orchestration, persistence, text processing, audio validation, retry behavior, and final stitching.

## TTS Server

Active runtime files:

- `services/tts-server/minimal_server.py`
- `services/tts-server/Dockerfile`
- `services/tts-server/voices/`

Endpoints:

- `GET /health`
- `GET /ready`
- `POST /synthesize`

Quality gate:

```bash
cd services/tts-server
python3 -m py_compile minimal_server.py
```
