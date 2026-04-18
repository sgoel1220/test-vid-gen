# Project Overview

## Purpose
Creepy Pasta audio production pipeline — generates horror stories, synthesizes TTS audio, creates images, and stitches final videos.

## Tech Stack
- Python 3.9+, FastAPI, Pydantic v2, SQLAlchemy (async), Alembic
- Hatchet (workflow orchestration), RunPod (GPU pods), Claude API (LLM)
- Docker, GitHub Actions CI, GHCR images

## Structure
Monorepo with 3 services:
- `services/tts-server/` — Minimal stateless TTS GPU pod (FastAPI)
- `services/creepy-brain/` — Orchestration: story gen, TTS, image gen, stitching (FastAPI + Hatchet)
- `services/image-server/` — SDXL image generation GPU pod (FastAPI)

## Key Conventions
- All code statically typed (mypy --strict)
- Pydantic BaseModel for ALL structured data, NEVER dict returns
- Enums for all status/state values (str, Enum mixin for JSON compat)
- Services flush only, callers commit (except CostService which self-commits)
- Modern Python syntax (list[] not List[], dict[] not Dict[])
