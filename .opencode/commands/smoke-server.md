---
description: Run a light API and UI smoke workflow for the current Chatterbox TTS server.
agent: build
---

Validate the server with a lightweight runtime smoke flow.

Use the repo's documented runtime entrypoint and default config behavior:

1. Start the server with the active Python environment:
   - `python server.py`
2. Confirm the server responds on the default local URL:
   - `http://127.0.0.1:8000/docs`
   - `http://127.0.0.1:8000/api/ui/initial-data`

Requirements:

- Keep validation aligned with the current product shape: TTS generation through the FastAPI server and Web UI.
- Prefer `/api/ui/initial-data` and `/docs` for smoke checks before attempting heavyweight synthesis flows.
- If startup is blocked by missing models, unsupported hardware, or environment setup issues, report that as environmental context.
- Summarize any failure by user-visible impact, not just raw logs.
- If the smoke flow passes, report that the baseline server flow is green.
