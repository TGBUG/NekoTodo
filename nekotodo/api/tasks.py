from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo import tools
from nekotodo.api.deps import get_current_user, get_session
from nekotodo.models import User
from nekotodo.schemas import TaskCreate, TaskMove, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    category: str | None = None,
    source_item_id: int | None = None,
    completed: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    tasks = await tools.list_tasks(
        session,
        user.id,
        category=category,
        source_item_id=source_item_id,
        completed_only=completed,
    )
    return [tools.task_to_dict(task) for task in tasks]


@router.post("")
async def create_task(
    payload: TaskCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        task = await tools.create_task(
            session,
            user.id,
            payload.description,
            deadline=tools.parse_deadline(payload.deadline),
            priority=payload.priority,
            category=payload.category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await session.commit()
    return tools.task_to_dict(task)


@router.get("/{task_id}")
async def get_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    task = await tools.get_task(session, user.id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return tools.task_to_dict(task)


@router.patch("/{task_id}")
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kwargs = payload.model_dump(exclude_unset=True)
    if "deadline" in kwargs:
        kwargs["deadline"] = tools.parse_deadline(kwargs["deadline"])
    try:
        task = await tools.update_task(session, user.id, task_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    await session.commit()
    return tools.task_to_dict(task)


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    deleted = await tools.delete_task(session, user.id, task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="task not found")
    await session.commit()
    return {"ok": True}


@router.post("/{task_id}/move")
async def move_task(
    task_id: int,
    payload: TaskMove,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    task = await tools.move_task(session, user.id, task_id, payload.to_position)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    await session.commit()
    return tools.task_to_dict(task)
