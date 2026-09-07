from __future__ import annotations

import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo import storage as storage_mod
from nekotodo.api.deps import get_current_user, get_session
from nekotodo.models import SourceImage, User

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{file_uuid}")
async def get_file(
    file_uuid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        uuid_mod.UUID(file_uuid)
    except ValueError:
        raise HTTPException(status_code=404, detail="file not found")
    result = await session.execute(
        select(SourceImage).where(
            SourceImage.file_uuid == file_uuid, SourceImage.user_id == user.id
        )
    )
    image = result.scalar_one_or_none()
    if image is None:
        raise HTTPException(status_code=404, detail="file not found")
    try:
        data = storage_mod.get_storage().read(user.id, file_uuid)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file missing on disk")
    mime = storage_mod.detect_image_mime(data) or "application/octet-stream"
    return Response(content=data, media_type=mime)
