from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo.models import SourceInfo, SourceItem, Task

_UNSET = object()


def parse_deadline(value: str | None, tz=None) -> datetime | None:
    """Parse an ISO 8601 string into a naive UTC datetime.

    Naive input is interpreted as ``tz`` when given (agent path: user-local
    time), otherwise as UTC (API path: frontend already converted). Offset
    input is converted to UTC.
    """
    if value is None or value == "":
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "description": task.description,
        "progress": task.progress,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "priority": task.priority,
        "category": task.category,
        "source_item_id": task.source_item_id,
    }


def source_info_to_dict(source_info: SourceInfo) -> dict[str, Any]:
    return {
        "id": source_info.id,
        "content": source_info.content,
        "created_at": source_info.created_at.isoformat() if source_info.created_at else None,
        "updated_at": source_info.updated_at.isoformat() if source_info.updated_at else None,
    }


def source_item_to_dict(item: SourceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_info_id": item.source_info_id,
        "content": item.content,
    }


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


async def _count_tasks(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(select(func.count(Task.id)).where(Task.user_id == user_id))
    return int(result.scalar_one())


async def get_task(session: AsyncSession, user_id: int, task_id: int) -> Task | None:
    result = await session.execute(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    return result.scalar_one_or_none()


async def list_tasks(
    session: AsyncSession,
    user_id: int,
    *,
    category: str | None = None,
    source_item_id: int | None = None,
    completed_only: bool = False,
) -> list[Task]:
    stmt = select(Task).where(Task.user_id == user_id)
    if category is not None:
        stmt = stmt.where(Task.category == category)
    if source_item_id is not None:
        stmt = stmt.where(Task.source_item_id == source_item_id)
    if completed_only:
        stmt = stmt.where(Task.progress == 100)
    stmt = stmt.order_by(Task.priority)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_task(
    session: AsyncSession,
    user_id: int,
    description: str,
    *,
    deadline: datetime | None = None,
    priority: int | None = None,
    category: str | None = None,
    source_item_id: int | None = None,
) -> Task:
    if not description or not description.strip():
        raise ValueError("description must not be empty")
    count = await _count_tasks(session, user_id)
    if priority is None:
        priority = count + 1
    else:
        priority = max(1, min(int(priority), count + 1))
        await session.execute(
            update(Task)
            .where(Task.user_id == user_id, Task.priority >= priority)
            .values(priority=Task.priority + 1)
        )
    task = Task(
        user_id=user_id,
        description=description,
        deadline=deadline,
        priority=priority,
        category=category,
        source_item_id=source_item_id,
    )
    session.add(task)
    await session.flush()
    return task


async def update_task(
    session: AsyncSession,
    user_id: int,
    task_id: int,
    *,
    description: Any = _UNSET,
    progress: Any = _UNSET,
    deadline: Any = _UNSET,
    category: Any = _UNSET,
) -> Task | None:
    task = await get_task(session, user_id, task_id)
    if task is None:
        return None
    if description is not _UNSET:
        if not description or not str(description).strip():
            raise ValueError("description must not be empty")
        task.description = str(description)
    if progress is not _UNSET:
        progress_int = int(progress)
        if not 0 <= progress_int <= 100:
            raise ValueError("progress must be between 0 and 100")
        task.progress = progress_int
    if deadline is not _UNSET:
        task.deadline = deadline  # None clears it
    if category is not _UNSET:
        task.category = category
    await session.flush()
    return task


async def _renormalize_priorities(session: AsyncSession, user_id: int) -> None:
    result = await session.execute(
        select(Task).where(Task.user_id == user_id).order_by(Task.priority)
    )
    for position, task in enumerate(result.scalars().all(), start=1):
        task.priority = position


async def delete_task(session: AsyncSession, user_id: int, task_id: int) -> bool:
    task = await get_task(session, user_id, task_id)
    if task is None:
        return False

    source_info_id: int | None = None
    if task.source_item_id is not None:
        item = await session.get(SourceItem, task.source_item_id)
        if item is not None:
            source_info_id = item.source_info_id

    await session.delete(task)
    await session.flush()
    await _renormalize_priorities(session, user_id)
    if source_info_id is not None:
        await _delete_orphaned_source_info(session, source_info_id)
    return True


async def move_task(session: AsyncSession, user_id: int, task_id: int, to_position: int) -> Task | None:
    task = await get_task(session, user_id, task_id)
    if task is None:
        return None
    count = await _count_tasks(session, user_id)
    to_position = max(1, min(int(to_position), count))
    from_position = task.priority
    if from_position == to_position:
        return task
    if from_position < to_position:
        await session.execute(
            update(Task)
            .where(
                Task.user_id == user_id,
                Task.priority > from_position,
                Task.priority <= to_position,
            )
            .values(priority=Task.priority - 1)
        )
    else:
        await session.execute(
            update(Task)
            .where(
                Task.user_id == user_id,
                Task.priority >= to_position,
                Task.priority < from_position,
            )
            .values(priority=Task.priority + 1)
        )
    task.priority = to_position
    await session.flush()
    return task


# ---------------------------------------------------------------------------
# SourceInfo & SourceItems
# ---------------------------------------------------------------------------


async def get_source_info(
    session: AsyncSession, user_id: int, source_info_id: int
) -> SourceInfo | None:
    result = await session.execute(
        select(SourceInfo).where(SourceInfo.id == source_info_id, SourceInfo.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_source_infos(session: AsyncSession, user_id: int) -> list[SourceInfo]:
    result = await session.execute(
        select(SourceInfo).where(SourceInfo.user_id == user_id).order_by(SourceInfo.id)
    )
    return list(result.scalars().all())


async def create_source_info(
    session: AsyncSession, user_id: int, content: str
) -> SourceInfo:
    if not content or not content.strip():
        raise ValueError("content must not be empty")
    source_info = SourceInfo(user_id=user_id, content=content)
    session.add(source_info)
    await session.flush()
    return source_info


async def create_source_item(
    session: AsyncSession, user_id: int, source_info_id: int, content: str
) -> SourceItem | None:
    source_info = await get_source_info(session, user_id, source_info_id)
    if source_info is None:
        return None
    if not content or not content.strip():
        raise ValueError("source item content must not be empty")
    item = SourceItem(source_info_id=source_info_id, user_id=user_id, content=content)
    session.add(item)
    await session.flush()
    return item


async def list_source_items(
    session: AsyncSession, user_id: int, source_info_id: int
) -> list[SourceItem]:
    result = await session.execute(
        select(SourceItem)
        .where(SourceItem.source_info_id == source_info_id, SourceItem.user_id == user_id)
        .order_by(SourceItem.id)
    )
    return list(result.scalars().all())


async def list_tasks_for_source_info(
    session: AsyncSession, user_id: int, source_info_id: int
) -> list[Task]:
    items = await list_source_items(session, user_id, source_info_id)
    item_ids = [item.id for item in items]
    if not item_ids:
        return []
    result = await session.execute(
        select(Task)
        .where(Task.user_id == user_id, Task.source_item_id.in_(item_ids))
        .order_by(Task.priority)
    )
    return list(result.scalars().all())


async def _count_referencing_tasks(session: AsyncSession, source_item_ids: list[int]) -> int:
    if not source_item_ids:
        return 0
    result = await session.execute(
        select(func.count(Task.id)).where(Task.source_item_id.in_(source_item_ids))
    )
    return int(result.scalar_one())


async def _delete_orphaned_source_info(session: AsyncSession, source_info_id: int) -> None:
    result = await session.execute(
        select(SourceItem.id).where(SourceItem.source_info_id == source_info_id)
    )
    item_ids = [row[0] for row in result]
    if await _count_referencing_tasks(session, item_ids) == 0:
        await session.execute(sa_delete(SourceInfo).where(SourceInfo.id == source_info_id))


async def delete_source_info(session: AsyncSession, user_id: int, source_info_id: int) -> bool:
    source_info = await get_source_info(session, user_id, source_info_id)
    if source_info is None:
        return False
    result = await session.execute(
        select(SourceItem.id).where(SourceItem.source_info_id == source_info_id)
    )
    item_ids = [row[0] for row in result]
    if item_ids:
        await session.execute(sa_delete(Task).where(Task.source_item_id.in_(item_ids)))
    await session.delete(source_info)  # ORM cascade removes SourceItems
    await _renormalize_priorities(session, user_id)
    return True
