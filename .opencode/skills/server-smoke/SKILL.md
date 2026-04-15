---
name: server-smoke
description: Run and interpret a lightweight smoke workflow for the FastAPI TTS server and Web UI.
---

## What I do

- Run a baseline runtime smoke flow for the server.
- Keep smoke checks aligned with the current API + Web UI product shape.
- Prefer low-cost health checks before heavyweight synthesis attempts.

## When to use me

Use this skill when:

- validating server startup behavior
- checking FastAPI route availability
- debugging regressions in the baseline UI/API flow

## Product guardrail

The active product surface is intentionally centered on:

1. Starting the TTS server
2. Accessing the Web UI or API
3. Generating TTS with the configured engine / voice mode

Do **not** expand smoke coverage into unrelated product ideas unless that scope change is intentional.

## Repo-specific layout

- `server.py` is the main FastAPI entrypoint
- `config.py` manages `config.yaml`
- `engine.py` owns model loading and synthesis
- `ui/` contains the main browser-facing assets

## Run

```bash
python server.py
```

Then verify:

```bash
curl http://127.0.0.1:8000/api/ui/initial-data
curl http://127.0.0.1:8000/docs
```

## Interpretation rules

- Prefer `/api/ui/initial-data` and `/docs` for smoke checks before full generation.
- If startup is blocked by missing model files, unsupported hardware, or environment setup, call that out clearly.
- Summarize failures by user flow and endpoint impact, not just raw logs.
