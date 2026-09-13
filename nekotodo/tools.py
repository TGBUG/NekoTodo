from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nekotodo.models import SourceFile, SourceInfo, SourceItem, Task, User
from nekotodo.storage import get_storage

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
        "status": task.status,
        "details": task.details,
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
        "source_file_id": item.source_file_id,
    }


def source_file_to_dict(source_file: SourceFile) -> dict[str, Any]:
    """API projection — deliberately omits ``extracted_text`` (can be huge);
    the Agent reads it through the read_source_file tool instead."""
    return {
        "id": source_file.id,
        "source_info_id": source_file.source_info_id,
        "kind": source_file.kind,
        "filename": source_file.filename,
        "mime": source_file.mime,
        "file_uuid": source_file.file_uuid,
        "size": source_file.size,
        "order_index": source_file.order_index,
        "description": source_file.description,
        "has_text": bool(source_file.extracted_text),
        "text_chars": len(source_file.extracted_text or ""),
    }


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def _resolve_tz(tz) -> ZoneInfo:
    if isinstance(tz, ZoneInfo):
        return tz
    try:
        return ZoneInfo(str(tz)) if tz else ZoneInfo("UTC")
    except Exception:  # noqa: BLE001 — unknown zone name in user preferences
        return ZoneInfo("UTC")


def deadline_day(deadline: datetime | None, tz) -> date | None:
    """User-local calendar day of a deadline (stored as naive UTC); None if undated."""
    if deadline is None:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline.astimezone(_resolve_tz(tz)).date()


def _order_key(task: Task, tz) -> tuple:
    day = deadline_day(task.deadline, tz)
    if day is None:
        return (1, date.max, task.priority)
    return (0, day, task.priority)


def sort_tasks(tasks: list[Task], tz) -> list[Task]:
    """Order by deadline day (undated last), then by in-group priority."""
    return sorted(tasks, key=lambda task: _order_key(task, tz))


async def _all_tasks(session: AsyncSession, user_id: int) -> list[Task]:
    result = await session.execute(select(Task).where(Task.user_id == user_id))
    return list(result.scalars().all())


async def _group_tasks(session: AsyncSession, user_id: int, day, tz) -> list[Task]:
    """Tasks sharing a deadline day (None = the undated group), ordered by priority."""
    tasks = [t for t in await _all_tasks(session, user_id) if deadline_day(t.deadline, tz) == day]
    tasks.sort(key=lambda task: task.priority)
    return tasks


async def _compact_priorities(session: AsyncSession, user_id: int, tz) -> None:
    """Re-number priorities 1..k inside each deadline-day group, closing gaps."""
    groups: dict[date | None, list[Task]] = {}
    for task in await _all_tasks(session, user_id):
        groups.setdefault(deadline_day(task.deadline, tz), []).append(task)
    for group in groups.values():
        group.sort(key=lambda task: task.priority)
        for position, task in enumerate(group, start=1):
            task.priority = position


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
    timezone=None,
) -> list[Task]:
    stmt = select(Task).where(Task.user_id == user_id)
    if category is not None:
        stmt = stmt.where(Task.category == category)
    if source_item_id is not None:
        stmt = stmt.where(Task.source_item_id == source_item_id)
    if completed_only:
        stmt = stmt.where(Task.status == "completed")
    result = await session.execute(stmt)
    return sort_tasks(list(result.scalars().all()), timezone)


def _clip(text: str, limit: int = 24) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


async def count_tasks_by_source_item(
    session: AsyncSession, user_id: int, source_item_ids: list[int]
) -> dict[int, int]:
    """Map source_item_id -> task count, for annotating the item list."""
    if not source_item_ids:
        return {}
    rows = await session.execute(
        select(Task.source_item_id, func.count(Task.id))
        .where(Task.user_id == user_id, Task.source_item_id.in_(source_item_ids))
        .group_by(Task.source_item_id)
    )
    return {int(item_id): int(count) for item_id, count in rows}


def _overview_key(task: Task, tz) -> tuple:
    """Incomplete first, then earliest deadline day, then in-group priority."""
    day = deadline_day(task.deadline, tz)
    return (task.status != "incomplete", day is None, day or date.max, task.priority)


async def task_overview(
    session: AsyncSession,
    user_id: int,
    *,
    per_category: int = 3,
    max_chars: int = 800,
    timezone=None,
) -> str:
    """Compact digest of the user's existing tasks for the decomposition prompt.

    Categories with per-status counts, plus a few sampled titles each. Samples
    prefer incomplete tasks, then the earliest deadline day. The output is
    explicitly labelled as an excerpt so the model knows to fall back to
    ``list_tasks`` for the authoritative list, rather than treating a category
    with no sampled title as empty.
    """
    tz = _resolve_tz(timezone)
    rows = await session.execute(
        select(Task.category, Task.status, func.count(Task.id))
        .where(Task.user_id == user_id)
        .group_by(Task.category, Task.status)
    )
    counts: dict[str | None, dict[str, int]] = {}
    for category, status, count in rows:
        counts.setdefault(category, {})[status] = int(count)
    if not counts:
        return "(暂无已有任务)"

    total = sum(sum(by_status.values()) for by_status in counts.values())
    ordered = sorted(counts, key=lambda c: -sum(counts[c].values()))

    header = (
        f"共 {total} 项已有任务,{len(counts)} 个分类"
        "(每类仅列节选标题,非全量;要准确清单请调用 list_tasks(category=...)):"
    )
    lines: list[str] = []
    for category in ordered:
        by_status = counts[category]
        sample_stmt = select(Task).where(Task.user_id == user_id)
        if category is None:
            sample_stmt = sample_stmt.where(Task.category.is_(None))
        else:
            sample_stmt = sample_stmt.where(Task.category == category)
        candidates = list((await session.execute(sample_stmt)).scalars().all())
        candidates.sort(key=lambda task: _overview_key(task, tz))
        extra = len(candidates) - per_category
        titles = "; ".join(_clip(task.description) for task in candidates[:per_category])
        suffix = f" (+{extra})" if extra > 0 else ""
        lines.append(
            f"- {category or '未分类'} "
            f"(未完成 {by_status.get('incomplete', 0)}/已完成 {by_status.get('completed', 0)}): "
            f"{titles}{suffix}"
        )

    kept: list[str] = []
    used = len(header)
    for line in lines:
        if used + len(line) + 1 > max_chars:
            kept.append(f"(概览过长,已省略 {len(lines) - len(kept)} 个分类)")
            break
        kept.append(line)
        used += len(line) + 1
    return header + "\n" + "\n".join(kept)


async def create_task(
    session: AsyncSession,
    user_id: int,
    description: str,
    *,
    deadline: datetime | None = None,
    priority: int | None = None,
    category: str | None = None,
    source_item_id: int | None = None,
    details: str | None = None,
    timezone=None,
) -> Task:
    if not description or not description.strip():
        raise ValueError("description must not be empty")
    tz = _resolve_tz(timezone)
    group = await _group_tasks(session, user_id, deadline_day(deadline, tz), tz)
    if priority is None:
        priority = max((task.priority for task in group), default=0) + 1
    else:
        priority = max(1, min(int(priority), len(group) + 1))
        for other in group:
            if other.priority >= priority:
                other.priority += 1
    task = Task(
        user_id=user_id,
        description=description,
        details=details or "",
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
    status: Any = _UNSET,
    details: Any = _UNSET,
    deadline: Any = _UNSET,
    category: Any = _UNSET,
    timezone=None,
) -> Task | None:
    task = await get_task(session, user_id, task_id)
    if task is None:
        return None
    tz = _resolve_tz(timezone)
    old_day = deadline_day(task.deadline, tz)
    if description is not _UNSET:
        if not description or not str(description).strip():
            raise ValueError("description must not be empty")
        task.description = str(description)
    if status is not _UNSET:
        if status not in ("incomplete", "completed"):
            raise ValueError("status must be 'incomplete' or 'completed'")
        task.status = status
    if details is not _UNSET:
        task.details = details or ""
    if deadline is not _UNSET:
        task.deadline = deadline  # None clears it
    if category is not _UNSET:
        task.category = category
    if deadline is not _UNSET and deadline_day(task.deadline, tz) != old_day:
        # Moving to another deadline day re-slots the task at the end of that group.
        new_group = [
            t for t in await _all_tasks(session, user_id)
            if t is not task and deadline_day(t.deadline, tz) == deadline_day(task.deadline, tz)
        ]
        task.priority = max((t.priority for t in new_group), default=0) + 1
        await _compact_priorities(session, user_id, tz)
    await session.flush()
    return task


async def delete_task(
    session: AsyncSession, user_id: int, task_id: int, *, timezone=None
) -> bool:
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
    await _compact_priorities(session, user_id, _resolve_tz(timezone))
    if source_info_id is not None:
        await _delete_orphaned_source_info(session, source_info_id)
    return True


async def move_task(
    session: AsyncSession, user_id: int, task_id: int, to_position: int, *, timezone=None
) -> Task | None:
    """Move a task to a position within its own deadline-day group."""
    task = await get_task(session, user_id, task_id)
    if task is None:
        return None
    tz = _resolve_tz(timezone)
    group = await _group_tasks(session, user_id, deadline_day(task.deadline, tz), tz)
    to_position = max(1, min(int(to_position), len(group)))
    from_position = task.priority
    if from_position == to_position:
        return task
    if from_position < to_position:
        for other in group:
            if from_position < other.priority <= to_position:
                other.priority -= 1
    else:
        for other in group:
            if to_position <= other.priority < from_position:
                other.priority += 1
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
    # content may be empty for pure-image submissions; the API enforces
    # "content or at least one file".
    source_info = SourceInfo(user_id=user_id, content=content)
    session.add(source_info)
    await session.flush()
    return source_info


async def create_source_item(
    session: AsyncSession,
    user_id: int,
    source_info_id: int,
    content: str,
    *,
    source_file_id: int | None = None,
) -> SourceItem | None:
    source_info = await get_source_info(session, user_id, source_info_id)
    if source_info is None:
        return None
    if not content or not content.strip():
        raise ValueError("source item content must not be empty")
    item = SourceItem(
        source_info_id=source_info_id,
        user_id=user_id,
        content=content,
        source_file_id=source_file_id,
    )
    session.add(item)
    await session.flush()
    return item


async def create_source_file(
    session: AsyncSession,
    user_id: int,
    source_info_id: int,
    file_uuid: str,
    size: int,
    *,
    kind: str = "image",
    filename: str = "",
    mime: str = "",
    order_index: int = 0,
) -> SourceFile | None:
    source_info = await get_source_info(session, user_id, source_info_id)
    if source_info is None:
        return None
    source_file = SourceFile(
        source_info_id=source_info_id,
        user_id=user_id,
        file_uuid=file_uuid,
        size=size,
        kind=kind,
        filename=filename,
        mime=mime,
        order_index=order_index,
    )
    session.add(source_file)
    await session.flush()
    return source_file


async def list_source_files(
    session: AsyncSession, user_id: int, source_info_id: int
) -> list[SourceFile]:
    result = await session.execute(
        select(SourceFile)
        .where(SourceFile.source_info_id == source_info_id, SourceFile.user_id == user_id)
        .order_by(SourceFile.order_index, SourceFile.id)
    )
    return list(result.scalars().all())


async def get_source_file(
    session: AsyncSession, user_id: int, source_file_id: int
) -> SourceFile | None:
    result = await session.execute(
        select(SourceFile).where(SourceFile.id == source_file_id, SourceFile.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def read_source_file_text(
    session: AsyncSession,
    user_id: int,
    source_file_id: int,
    *,
    offset: int = 0,
    limit: int = 4000,
) -> dict[str, Any] | None:
    """Slice of a document's ordered digest, for the read_source_file tool.

    Returning slices of one ordered digest (rather than re-extracting) keeps the
    document's text/image ordering intact across reads.
    """
    source_file = await get_source_file(session, user_id, source_file_id)
    if source_file is None:
        return None
    text = source_file.extracted_text or ""
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 4000), 20_000))
    chunk = text[offset : offset + limit]
    return {
        "source_file_id": source_file.id,
        "filename": source_file.filename,
        "kind": source_file.kind,
        "text": chunk,
        "offset": offset,
        "total_chars": len(text),
        "has_more": offset + limit < len(text),
    }


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
    session: AsyncSession, user_id: int, source_info_id: int, *, timezone=None
) -> list[Task]:
    items = await list_source_items(session, user_id, source_info_id)
    item_ids = [item.id for item in items]
    if not item_ids:
        return []
    result = await session.execute(
        select(Task).where(Task.user_id == user_id, Task.source_item_id.in_(item_ids))
    )
    return sort_tasks(list(result.scalars().all()), timezone)


async def _count_referencing_tasks(session: AsyncSession, source_item_ids: list[int]) -> int:
    if not source_item_ids:
        return 0
    result = await session.execute(
        select(func.count(Task.id)).where(Task.source_item_id.in_(source_item_ids))
    )
    return int(result.scalar_one())


async def _collect_source_files(session: AsyncSession, source_info_id: int) -> list[tuple[int, str]]:
    result = await session.execute(
        select(SourceFile.user_id, SourceFile.file_uuid).where(
            SourceFile.source_info_id == source_info_id
        )
    )
    return [(row[0], row[1]) for row in result]


def _delete_files(files: list[tuple[int, str]]) -> None:
    for user_id, file_uuid in files:
        get_storage().delete(user_id, file_uuid)


async def _delete_orphaned_source_info(session: AsyncSession, source_info_id: int) -> None:
    result = await session.execute(
        select(SourceItem.id).where(SourceItem.source_info_id == source_info_id)
    )
    item_ids = [row[0] for row in result]
    if await _count_referencing_tasks(session, item_ids) == 0:
        files = await _collect_source_files(session, source_info_id)
        await session.execute(sa_delete(SourceInfo).where(SourceInfo.id == source_info_id))
        _delete_files(files)


async def delete_source_info(
    session: AsyncSession, user_id: int, source_info_id: int, *, timezone=None
) -> bool:
    source_info = await get_source_info(session, user_id, source_info_id)
    if source_info is None:
        return False
    files = await _collect_source_files(session, source_info_id)
    result = await session.execute(
        select(SourceItem.id).where(SourceItem.source_info_id == source_info_id)
    )
    item_ids = [row[0] for row in result]
    if item_ids:
        await session.execute(sa_delete(Task).where(Task.source_item_id.in_(item_ids)))
    await session.delete(source_info)  # ORM cascade removes SourceItems + SourceFiles
    await _compact_priorities(session, user_id, _resolve_tz(timezone))
    _delete_files(files)
    return True


async def delete_user(session: AsyncSession, user_id: int) -> bool:
    user = await session.get(User, user_id)
    if user is None:
        return False
    result = await session.execute(
        select(SourceFile.user_id, SourceFile.file_uuid).where(SourceFile.user_id == user_id)
    )
    files = [(row[0], row[1]) for row in result]
    # ORM cascade: Account, Tasks, SourceInfos (-> SourceItems + SourceFiles), DecompositionRuns
    await session.delete(user)
    _delete_files(files)
    get_storage().delete_user_dir(user_id)
    return True
