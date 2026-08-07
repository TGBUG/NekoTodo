from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo import tools
from nekotodo.agent import runner
from nekotodo.api.deps import get_current_user, get_session
from nekotodo.models import User
from nekotodo.schemas import SourceInfoCreate, SourceInfoUpdate

router = APIRouter(prefix="/source-infos", tags=["source-infos"])


@router.post("")
async def create_source_info(
    payload: SourceInfoCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        source_info = await tools.create_source_info(session, user.id, payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await session.commit()
    return tools.source_info_to_dict(source_info)


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
    tasks = await tools.list_tasks_for_source_info(session, user.id, source_info_id)
    return {
        **tools.source_info_to_dict(source_info),
        "source_items": [tools.source_item_to_dict(item) for item in items],
        "tasks": [tools.task_to_dict(task) for task in tasks],
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
    if "content" in data:
        source_info.content = data["content"]
    await session.commit()
    return tools.source_info_to_dict(source_info)


@router.delete("/{source_info_id}")
async def delete_source_info(
    source_info_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    deleted = await tools.delete_source_info(session, user.id, source_info_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="source info not found")
    await session.commit()
    return {"ok": True}


@router.post("/{source_info_id}/decompose")
async def decompose(
    source_info_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        run = await runner.submit_decomposition(session, user.id, source_info_id)
    except runner.ConcurrentRunError:
        raise HTTPException(
            status_code=409,
            detail="an active decomposition run already exists for this source info",
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="source info not found")
    return {"run_id": run.id}
