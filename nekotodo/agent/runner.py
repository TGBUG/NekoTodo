from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from nekotodo import storage as storage_mod
from nekotodo import tools
from nekotodo.agent.manager import AgentManager
from nekotodo.agent.prompts import build_system_prompt, build_user_message
from nekotodo.config import IMAGE_EXTRACTION_PROMPT, Settings
from nekotodo.models import DecompositionRun, SourceInfo, User

_manager: AgentManager | None = None
_session_factory: async_sessionmaker | None = None
_settings: Settings | None = None

log = logging.getLogger("nekotodo.runner")


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
    log.info("run %s queued (user=%s, source_info=%s)", run.id, user_id, source_info_id)
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
            log.info("run %s started (source_info=%s)", run.id, run.source_info_id)

        if source_info is None or user is None:
            raise RuntimeError("source info or user missing")

        try:
            tz = ZoneInfo(user.timezone or "UTC")
        except Exception:  # noqa: BLE001 — bad timezone in preferences
            tz = ZoneInfo("UTC")
        now_local = datetime.now(tz)

        # VLM preprocess images; reuse descriptions already stored from a prior run.
        async with session_factory() as s:
            images = await tools.list_source_images(s, run.user_id, run.source_info_id)
            for image in images:
                if not image.description:
                    if manager.vlm_client is None:
                        raise RuntimeError("VLM is not configured; cannot process images")
                    data = storage_mod.get_storage().read(run.user_id, image.file_uuid)
                    mime = storage_mod.detect_image_mime(data)
                    if mime is None:
                        raise RuntimeError(f"stored file {image.file_uuid} is not a valid image")
                    data_uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
                    image.description = await manager.extract_image(IMAGE_EXTRACTION_PROMPT, data_uri)
                    log.info(
                        "VLM 预处理完成 run=%s image=%s file=%s (%d 字符)",
                        run.id, image.id, image.file_uuid, len(image.description),
                    )
                    await s.commit()

        # Build the system prompt from the template via placeholders (no fallback).
        async with session_factory() as s:
            source_items = await tools.list_source_items(s, run.user_id, run.source_info_id)
        images_block = "\n".join(
            f"- 图片(id {image.id}, 文件 {image.file_uuid}): {image.description or '(无描述)'}"
            for image in images
        ) or "(无图片)"
        source_items_block = "\n".join(
            f"- [{item.id}] {item.content}" for item in source_items
        ) or "(尚未切分条目)"
        context = {
            "source_info_id": str(source_info.id),
            "current_time": now_local.isoformat(),
            "timezone": user.timezone or "UTC",
            "source_content": source_info.content or "(无文本内容)",
            "source_items": source_items_block,
            "images": images_block,
        }
        system_prompt = build_system_prompt(
            settings.prompt.default_template, user.custom_prompt_template, context
        )
        user_message = build_user_message()

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
            if "status" in args:
                kwargs["status"] = args["status"]
            if "details" in args:
                kwargs["details"] = args["details"]
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
                    s,
                    run.user_id,
                    args["source_info_id"],
                    args["content"],
                    source_image_id=args.get("source_image_id"),
                )
                await s.commit()
                if item is None:
                    return json.dumps({"error": "source info not found"})
                return json.dumps(
                    {
                        "id": item.id,
                        "source_info_id": item.source_info_id,
                        "content": item.content,
                        "source_image_id": item.source_image_id,
                    },
                    ensure_ascii=False,
                )

        async def handler_list_source_items(args: dict) -> str:
            async with session_factory() as s:
                items = await tools.list_source_items(s, run.user_id, args["source_info_id"])
                return json.dumps(
                    [
                        {
                            "id": i.id,
                            "source_info_id": i.source_info_id,
                            "content": i.content,
                            "source_image_id": i.source_image_id,
                        }
                        for i in items
                    ],
                    ensure_ascii=False,
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

        tool_calls_used, _final = await manager.run(system_prompt, user_message, handlers)
        if tool_calls_used == 0:
            raise RuntimeError("agent made no tool calls")

        async with session_factory() as session:
            run = await session.get(DecompositionRun, run_id)
            run.status = "completed"
            run.created_task_ids = created_ids
            await session.commit()
        log.info("run %s completed, created %d tasks", run_id, len(created_ids))
    except Exception as exc:  # noqa: BLE001 — record any failure on the run
        log.exception("run %s failed", run_id)
        async with session_factory() as session:
            run = await session.get(DecompositionRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
                await session.commit()
