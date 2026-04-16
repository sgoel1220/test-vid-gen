"""PersistenceClient — typed httpx wrapper for the metadata server API."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Optional

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from creepy_pasta_protocol.audio import AudioBlobDTO
from creepy_pasta_protocol.chunks import ChunkDTO, ChunkSpec
from creepy_pasta_protocol.common import AudioFormat
from creepy_pasta_protocol.runs import (
    CreateRunRequest,
    PatchRunRequest,
    RunDetailDTO,
    RunSummaryDTO,
)
from creepy_pasta_protocol.scripts import CreateScriptRequest, ScriptDTO
from creepy_pasta_protocol.validation import ChunkValidationSnapshot
from creepy_pasta_protocol.voices import CreateVoiceResponse

from persistence.config import PersistenceSettings
from persistence.errors import PermanentPersistenceError, TransientPersistenceError

_log = logging.getLogger(__name__)

_USER_AGENT = "creepy-pasta-pod/1"


def _log_before_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    _log.warning(
        "Persistence transient error (attempt %d): %s",
        retry_state.attempt_number,
        exc,
    )


# Single retry decorator instance reused across all methods.
_with_retry = retry(
    retry=retry_if_exception_type(TransientPersistenceError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    before_sleep=_log_before_retry,
    reraise=True,
)


class PersistenceClient:
    """Async HTTP client for the Chatterbox metadata server.

    Instantiate only when is_enabled() is True.
    Use as an async context manager or call aclose() when done.
    """

    def __init__(self, settings: PersistenceSettings) -> None:
        assert settings.metadata_api_url is not None, "metadata_api_url is required"
        assert settings.metadata_api_key is not None, "metadata_api_key is required"
        self._http = httpx.AsyncClient(
            base_url=str(settings.metadata_api_url),
            headers={
                "Authorization": f"Bearer {settings.metadata_api_key.get_secret_value()}",
                "User-Agent": _USER_AGENT,
            },
            timeout=settings.metadata_timeout_seconds,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> PersistenceClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        snippet = resp.text[:300]
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientPersistenceError(f"HTTP {resp.status_code}: {snippet}")
        raise PermanentPersistenceError(f"HTTP {resp.status_code}: {snippet}")

    async def _get(self, path: str) -> httpx.Response:
        try:
            resp = await self._http.get(path)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientPersistenceError(str(exc)) from exc
        self._raise_for_status(resp)
        return resp

    async def _post_json(self, path: str, body: object) -> httpx.Response:
        try:
            resp = await self._http.post(
                path,
                content=_to_json_bytes(body),
                headers={"Content-Type": "application/json"},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientPersistenceError(str(exc)) from exc
        self._raise_for_status(resp)
        return resp

    async def _patch_json(self, path: str, body: object) -> httpx.Response:
        try:
            resp = await self._http.patch(
                path,
                content=_to_json_bytes(body),
                headers={"Content-Type": "application/json"},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientPersistenceError(str(exc)) from exc
        self._raise_for_status(resp)
        return resp

    async def _post_multipart(
        self,
        path: str,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
    ) -> httpx.Response:
        try:
            resp = await self._http.post(path, data=data, files=files)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientPersistenceError(str(exc)) from exc
        self._raise_for_status(resp)
        return resp

    # ------------------------------------------------------------------
    # Public API — each method retries on TransientPersistenceError
    # ------------------------------------------------------------------

    @_with_retry
    async def create_or_get_script(self, text: str) -> ScriptDTO:
        req = CreateScriptRequest(text=text)
        resp = await self._post_json("/v1/scripts", req)
        dto = ScriptDTO.model_validate(resp.json())
        _log.info("create_or_get_script ok: id=%s", dto.id)
        return dto

    @_with_retry
    async def upsert_voice(
        self,
        filename: str,
        data: bytes,
        format: AudioFormat,
        sample_rate: int,
        duration_sec: float,
        mime_type: str,
    ) -> CreateVoiceResponse:
        resp = await self._post_multipart(
            "/v1/voices",
            data={
                "filename": filename,
                "format": format.value,
                "sample_rate": str(sample_rate),
                "duration_sec": str(duration_sec),
                "mime_type": mime_type,
            },
            files={"file": (filename, data, mime_type)},
        )
        result = CreateVoiceResponse.model_validate(resp.json())
        _log.info("upsert_voice ok: id=%s created=%s", result.voice.id, result.created)
        return result

    @_with_retry
    async def create_run(self, req: CreateRunRequest) -> RunDetailDTO:
        resp = await self._post_json("/v1/runs", req)
        dto = RunDetailDTO.model_validate(resp.json())
        _log.info("create_run ok: id=%s", dto.id)
        return dto

    @_with_retry
    async def patch_run(self, run_id: str, req: PatchRunRequest) -> RunDetailDTO:
        resp = await self._patch_json(f"/v1/runs/{run_id}", req)
        dto = RunDetailDTO.model_validate(resp.json())
        _log.info("patch_run ok: id=%s status=%s", dto.id, dto.status)
        return dto

    @_with_retry
    async def create_chunks(
        self, run_id: str, specs: list[ChunkSpec]
    ) -> list[ChunkDTO]:
        payload = [s.model_dump(mode="json") for s in specs]
        resp = await self._post_json(f"/v1/runs/{run_id}/chunks", payload)
        dtos = [ChunkDTO.model_validate(c) for c in resp.json()]
        _log.info("create_chunks ok: run_id=%s count=%d", run_id, len(dtos))
        return dtos

    @_with_retry
    async def upload_chunk_audio(
        self,
        run_id: str,
        chunk_index: int,
        data: bytes,
        format: AudioFormat,
        sample_rate: int,
        duration_sec: float,
        mime_type: str,
        attempts_used: int = 0,
        validation: Optional[ChunkValidationSnapshot] = None,
    ) -> AudioBlobDTO:
        form: dict[str, str] = {
            "format": format.value,
            "sample_rate": str(sample_rate),
            "duration_sec": str(duration_sec),
            "mime_type": mime_type,
            "attempts_used": str(attempts_used),
        }
        if validation is not None:
            form["validation_json"] = validation.model_dump_json()
        resp = await self._post_multipart(
            f"/v1/runs/{run_id}/chunks/{chunk_index}/audio",
            data=form,
            files={"file": (f"chunk_{chunk_index}.{format.value}", data, mime_type)},
        )
        dto = AudioBlobDTO.model_validate(resp.json())
        _log.info(
            "upload_chunk_audio ok: run_id=%s chunk=%d blob_id=%s",
            run_id,
            chunk_index,
            dto.id,
        )
        return dto

    @_with_retry
    async def upload_final_audio(
        self,
        run_id: str,
        data: bytes,
        format: AudioFormat,
        sample_rate: int,
        duration_sec: float,
        mime_type: str,
    ) -> AudioBlobDTO:
        resp = await self._post_multipart(
            f"/v1/runs/{run_id}/final_audio",
            data={
                "format": format.value,
                "sample_rate": str(sample_rate),
                "duration_sec": str(duration_sec),
                "mime_type": mime_type,
            },
            files={"file": (f"final.{format.value}", data, mime_type)},
        )
        dto = AudioBlobDTO.model_validate(resp.json())
        _log.info("upload_final_audio ok: run_id=%s blob_id=%s", run_id, dto.id)
        return dto

    # ------------------------------------------------------------------
    # Pod-run-id-based aliases — use these from the outbox
    # ------------------------------------------------------------------

    @_with_retry
    async def patch_run_by_pod(
        self, pod_run_id: str, req: PatchRunRequest
    ) -> RunDetailDTO:
        resp = await self._patch_json(f"/v1/runs/by-pod/{pod_run_id}", req)
        dto = RunDetailDTO.model_validate(resp.json())
        _log.info("patch_run_by_pod ok: pod_run_id=%s status=%s", pod_run_id, dto.status)
        return dto

    @_with_retry
    async def create_chunks_by_pod(
        self, pod_run_id: str, specs: list[ChunkSpec]
    ) -> list[ChunkDTO]:
        payload = [s.model_dump(mode="json") for s in specs]
        resp = await self._post_json(f"/v1/runs/by-pod/{pod_run_id}/chunks", payload)
        dtos = [ChunkDTO.model_validate(c) for c in resp.json()]
        _log.info("create_chunks_by_pod ok: pod_run_id=%s count=%d", pod_run_id, len(dtos))
        return dtos

    @_with_retry
    async def upload_chunk_audio_by_pod(
        self,
        pod_run_id: str,
        chunk_index: int,
        data: bytes,
        format: AudioFormat,
        sample_rate: int,
        duration_sec: float,
        mime_type: str,
        attempts_used: int = 0,
        validation: Optional[ChunkValidationSnapshot] = None,
    ) -> AudioBlobDTO:
        form: dict[str, str] = {
            "format": format.value,
            "sample_rate": str(sample_rate),
            "duration_sec": str(duration_sec),
            "mime_type": mime_type,
            "attempts_used": str(attempts_used),
        }
        if validation is not None:
            form["validation_json"] = validation.model_dump_json()
        resp = await self._post_multipart(
            f"/v1/runs/by-pod/{pod_run_id}/chunks/{chunk_index}/audio",
            data=form,
            files={"file": (f"chunk_{chunk_index}.{format.value}", data, mime_type)},
        )
        dto = AudioBlobDTO.model_validate(resp.json())
        _log.info(
            "upload_chunk_audio_by_pod ok: pod_run_id=%s chunk=%d blob_id=%s",
            pod_run_id,
            chunk_index,
            dto.id,
        )
        return dto

    @_with_retry
    async def upload_final_audio_by_pod(
        self,
        pod_run_id: str,
        data: bytes,
        format: AudioFormat,
        sample_rate: int,
        duration_sec: float,
        mime_type: str,
    ) -> AudioBlobDTO:
        resp = await self._post_multipart(
            f"/v1/runs/by-pod/{pod_run_id}/final_audio",
            data={
                "format": format.value,
                "sample_rate": str(sample_rate),
                "duration_sec": str(duration_sec),
                "mime_type": mime_type,
            },
            files={"file": (f"final.{format.value}", data, mime_type)},
        )
        dto = AudioBlobDTO.model_validate(resp.json())
        _log.info("upload_final_audio_by_pod ok: pod_run_id=%s blob_id=%s", pod_run_id, dto.id)
        return dto

    @_with_retry
    async def list_runs(
        self, limit: int = 50, offset: int = 0
    ) -> list[RunSummaryDTO]:
        resp = await self._get(f"/v1/runs?limit={limit}&offset={offset}")
        dtos = [RunSummaryDTO.model_validate(r) for r in resp.json()]
        _log.info("list_runs ok: count=%d", len(dtos))
        return dtos

    @_with_retry
    async def get_run(self, run_id: str) -> RunDetailDTO:
        resp = await self._get(f"/v1/runs/{run_id}")
        dto = RunDetailDTO.model_validate(resp.json())
        _log.info("get_run ok: id=%s", dto.id)
        return dto

    async def stream_audio(self, audio_blob_id: str) -> AsyncIterator[bytes]:
        """Stream audio bytes from the server.

        Not retried — partial data makes retry semantics ambiguous.
        """
        try:
            async with self._http.stream(
                "GET", f"/v1/audio/{audio_blob_id}"
            ) as resp:
                self._raise_for_status(resp)
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientPersistenceError(str(exc)) from exc


def _to_json_bytes(obj: object) -> bytes:
    """Serialize a Pydantic model or plain object to UTF-8 JSON bytes."""
    from pydantic import BaseModel  # local import keeps top-level imports clean
    if isinstance(obj, BaseModel):
        return obj.model_dump_json().encode()
    return json.dumps(obj).encode()
