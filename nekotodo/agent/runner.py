from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from nekotodo import tools
from nekotodo.agent.manager import AgentManager
from nekotodo.agent.prompts import build_system_prompt, build_user_message
from nekotodo.config import Settings
from nekotodo.models import DecompositionRun, SourceInfo, User

_manager: AgentManager | None = None
_session_factory: async_sessionmaker | None = None
_settings: Settings | None = None


class ConcurrentRunError(Exception):
    def __init__(self, source_info_id: int):
        super().__init__(f"an active run already exists for source info {source_info_id}")


def init_agent(settings: Settings, session_factory) -> None:
    global _manager, _session_factory, _settings
    _manager = AgentManager(settings)
    _session_factory = session_factory
    _settings = settings


def _deps():
    if _manager is None or _session_factory is None or _settings is None:
        raise RuntimeError("agent not initialized")
    return _manager, _session_factory, _settings


def _parse_deadline(value, tz: ZoneInfo):
    if value is None or value == "":
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _task_result(task) -> str:
    return json.dumps(tools.task_to_dict(task), ensure_ascii=False)


async def submit_decomposition(
    session, user_id: int, source_info_id: int
) -> DecompositionRun:
    source_info = await tools.get_source_info(session, user_id, source_info_id)
    if source_info is None:
        raise LookupError("source info not found")
    result = await session.execute(
        select(func.count(DecompositionRun.id)).where(
            DecompositionRun.source_info_id == source_info_id,
            DecompositionRun.status.in_(["pending", "running"]),
        )
    )
    if result.scalar_one() > 0:
        raise ConcurrentRunError(source_info_id)
    run = DecompositionRun(user_id=user_id, source_info_id=source_info_id, status="pending")
    session.add(run)
    await session.commit()
    await session.refresh(run)
    asyncio.get_running_loop().create_task(_execute_run(run.id))
    return run


async def _execute_run(run_id: str) -> None:
    manager, session_factory, settings = _deps()
    try:
        async with session_factory() as session:
            run = await session.get(DecompositionRun, run_id)
            if run is None:
                return
            source_info = await session.get(SourceInfo, run.source_info_id)
            user = await session.get(User, run.user_id)
            run.status = "running"
            await session.commit()

        if source_info is None or user is None:
            raise RuntimeError("source info or user missing")

        try:
            tz = ZoneInfo(user.timezone or "UTC")
        except Exception:  # noqa: BLE001 — bad timezone in preferences
            tz = ZoneInfo("UTC")
        now_local = datetime.now(tz)

        system_prompt = build_system_prompt(
            settings.prompt.default_template,
            user.custom_prompt_template,
            user.timezone or "UTC",
            now_local,
        )

        created_ids: list[int] = []

        async def handler_create_task(args: dict) -> str:
            async with session_factory() as s:
                task = await tools.create_task(
                    s,
                    run.user_id,
                    args["description"],
                    deadline=_parse_deadline(args.get("deadline"), tz),
                    priority=args.get("priority"),
                    category=args.get("category"),
                    source_item_id=args.get("source_item_id"),
                )
                await s.commit()
                created_ids.append(task.id)
                return _task_result(task)

        async def handler_insert_task(args: dict) -> str:
            async with session_factory() as s:
                task = await tools.create_task(
                    s,
                    run.user_id,
                    args["description"],
                    deadline=_parse_deadline(args.get("deadline"), tz),
                    priority=args["priority"],
                    category=args.get("category"),
                    source_item_id=args.get("source_item_id"),
                )
                await s.commit()
                created_ids.append(task.id)
                return _task_result(task)

        async def handler_list_tasks(args: dict) -> str:
            async with session_factory() as s:
                tasks = await tools.list_tasks(
                    s,
                    run.user_id,
                    category=args.get("category"),
                    source_item_id=args.get("source_item_id"),
                )
                return json.dumps([tools.task_to_dict(t) for t in tasks], ensure_ascii=False)

        async def handler_update_task(args: dict) -> str:
            kwargs = {}
            if "description" in args:
                kwargs["description"] = args["description"]
            if "progress" in args:
                kwargs["progress"] = args["progress"]
            if "deadline" in args:
                kwargs["deadline"] = _parse_deadline(args["deadline"], tz)
            if "category" in args:
                kwargs["category"] = args["category"]
            async with session_factory() as s:
                task = await tools.update_task(s, run.user_id, args["task_id"], **kwargs)
                await s.commit()
                if task is None:
                    return json.dumps({"error": "task not found"})
                return _task_result(task)

        async def handler_move_task(args: dict) -> str:
            async with session_factory() as s:
                task = await tools.move_task(s, run.user_id, args["task_id"], args["to_position"])
                await s.commit()
                if task is None:
                    return json.dumps({"error": "task not found"})
                return _task_result(task)

        async def handler_create_source_item(args: dict) -> str:
            async with session_factory() as s:
                item = await tools.create_source_item(
                    s, run.user_id, args["source_info_id"], args["content"]
                )
                await s.commit()
                if item is None:
                    return json.dumps({"error": "source info not found"})
                return json.dumps(
                    {"id": item.id, "source_info_id": item.source_info_id, "content": item.content},
                    ensure_ascii=False,
                )

        async def handler_list_source_items(args: dict) -> str:
            async with session_factory() as s:
                items = await tools.list_source_items(
                    s, run.user_id, args["source_info_id"]
                )
                return json.dumps(
                    [{"id": i.id, "content": i.content} for i in items], ensure_ascii=False
                )

        handlers = {
            "create_task": handler_create_task,
            "insert_task": handler_insert_task,
            "list_tasks": handler_list_tasks,
            "update_task": handler_update_task,
            "move_task": handler_move_task,
            "create_source_item": handler_create_source_item,
            "list_source_items": handler_list_source_items,
        }

        async with session_factory() as s:
            source_items = await tools.list_source_items(s, run.user_id, run.source_info_id)
        user_message = build_user_message(source_info, source_items)

        tool_calls_used, _final = await manager.run(system_prompt, user_message, handlers)
        if tool_calls_used == 0:
            raise RuntimeError("agent made no tool calls")

        async with session_factory() as session:
            run = await session.get(DecompositionRun, run_id)
            run.status = "completed"
            run.created_task_ids = created_ids
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — record any failure on the run
        async with session_factory() as session:
            run = await session.get(DecompositionRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
                await session.commit()
