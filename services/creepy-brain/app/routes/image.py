"""Proxy routes for ComfyUI RunPod Serverless image generation."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/api/image", tags=["image"])

_RUNPOD_BASE = "https://api.runpod.ai/v2"


def _headers() -> dict[str, str]:
    key = settings.comfyui_api_key or settings.runpod_api_key
    if not key:
        raise HTTPException(status_code=500, detail="No RunPod API key configured")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _endpoint_url(path: str) -> str:
    return f"{_RUNPOD_BASE}/{settings.comfyui_endpoint_id}/{path}"


class GenerateRequest(BaseModel):
    workflow: dict[str, Any]


class GenerateResponse(BaseModel):
    id: str
    status: str
    output: Any | None = None


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """Submit a ComfyUI workflow to RunPod Serverless."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            _endpoint_url("runsync"),
            headers=_headers(),
            json={"input": {"workflow": req.workflow}},
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data = resp.json()
    return GenerateResponse(
        id=data.get("id", ""),
        status=data.get("status", "UNKNOWN"),
        output=data.get("output"),
    )


@router.get("/status/{job_id}", response_model=GenerateResponse)
async def status(job_id: str) -> GenerateResponse:
    """Poll job status from RunPod Serverless."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            _endpoint_url(f"status/{job_id}"),
            headers=_headers(),
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data = resp.json()
    return GenerateResponse(
        id=data.get("id", job_id),
        status=data.get("status", "UNKNOWN"),
        output=data.get("output"),
    )
