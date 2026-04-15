"""Script create-or-get endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from creepy_pasta_protocol.scripts import CreateScriptRequest, ScriptDTO

from app.auth import require_api_key
from app.converters import script_to_dto
from app.db import DbSession
from app.services import scripts as scripts_svc

router = APIRouter(prefix="/v1/scripts", tags=["scripts"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=ScriptDTO)
async def create_or_get_script(body: CreateScriptRequest, session: DbSession) -> ScriptDTO:
    script = await scripts_svc.create_or_get(session, body.text)
    await session.commit()
    return script_to_dto(script)
