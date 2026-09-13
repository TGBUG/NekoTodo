from __future__ import annotations

import uuid as uuid_mod

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo import extraction
from nekotodo import storage as storage_mod
from nekotodo import tools
from nekotodo.agent import runner
from nekotodo.api.deps import get_current_user, get_session
from nekotodo.config import Settings, get_settings
from nekotodo.models import User
from nekotodo.schemas import SourceInfoUpdate

router = APIRouter(prefix="/source-infos", tags=["source-infos"])


def _vlm_configured(settings: Settings) -> bool:
    return bool(settings.vlm.base_url and settings.vlm.model)


async def _save_uploads(session: AsyncSession, user_id: int, source_info_id: int, files: list[UploadFile]) -> None:
    storage = storage_mod.get_storage()
    for order_index, upload in enumerate(files):
        data = await upload.read()
        if len(data) > extraction.MAX_FILE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"file too large: '{upload.filename}' (max {extraction.MAX_FILE_BYTES // (1024 * 1024)}MB)",
            )
        detected = extraction.detect_kind(data, upload.filename or "")
        if detected is None:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported file type: '{upload.filename}'. {extraction.SUPPORTED_HINT}",
            )
        kind, mime = detected
        if kind == "image" and not _vlm_configured(get_settings()):
            raise HTTPException(
                status_code=400,
                detail="VLM is not configured; cannot process image files",
            )
        file_uuid = str(uuid_mod.uuid4())
        storage.save(user_id, file_uuid, data)
        await tools.create_source_file(
            session,
            user_id,
            source_info_id,
            file_uuid,
            len(data),
            kind=kind,
            filename=upload.filename or "",
            mime=mime,
            order_index=order_index,
        )


@router.post("")
async def create_source_info(
    content: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    files = files or []
    if not (content or files):
        raise HTTPException(status_code=400, detail="content or at least one file is required")
    source_info = await tools.create_source_info(session, user.id, content or "")
    await _save_uploads(session, user.id, source_info.id, files)
    await session.flush()
    run = await runner.submit_decomposition(session, user.id, source_info.id)
    return {"source_info_id": source_info.id, "run_id": run.id}


@router.get("")
async def list_source_infos(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    source_infos = await tools.list_source_infos(session, user.id)
    return [tools.source_info_to_dict(source_info) for source_info in source_infos]


@router.get("/{source_info_id}")
async def get_source_info(
    source_info_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    source_info = await tools.get_source_info(session, user.id, source_info_id)
    if source_info is None:
        raise HTTPException(status_code=404, detail="source info not found")
    items = await tools.list_source_items(session, user.id, source_info_id)
    tasks = await tools.list_tasks_for_source_info(
        session, user.id, source_info_id, timezone=user.timezone
    )
    source_files = await tools.list_source_files(session, user.id, source_info_id)
    return {
        **tools.source_info_to_dict(source_info),
        "source_items": [tools.source_item_to_dict(item) for item in items],
        "tasks": [tools.task_to_dict(task) for task in tasks],
        "source_files": [tools.source_file_to_dict(f) for f in source_files],
    }


@router.patch("/{source_info_id}")
async def update_source_info(
    source_info_id: int,
    payload: SourceInfoUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    source_info = await tools.get_source_info(session, user.id, source_info_id)
    if source_info is None:
        raise HTTPException(status_code=404, detail="source info not found")
    data = payload.model_dump(exclude_unset=True)
    if "content" not in data:
        raise HTTPException(status_code=400, detail="content is required")
    source_info.content = data["content"]
    await session.flush()
    try:
        run = await runner.submit_decomposition(session, user.id, source_info_id)
    except runner.ConcurrentRunError:
        raise HTTPException(
            status_code=409,
            detail="an active decomposition run already exists for this source info",
        )
    return {"source_info_id": source_info.id, "run_id": run.id}


@router.delete("/{source_info_id}")
async def delete_source_info(
    source_info_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    deleted = await tools.delete_source_info(session, user.id, source_info_id, timezone=user.timezone)
    if not deleted:
        raise HTTPException(status_code=404, detail="source info not found")
    await session.commit()
    return {"ok": True}
