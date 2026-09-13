from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid as uuid_mod
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from nekotodo import extraction
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


def _preview(text: str, limit: int = 600) -> str:
    """Single-line preview of a document digest, for the {files} block."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


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

        storage = storage_mod.get_storage()

        # 1) 文档抽取:docx/pptx/pdf/文本 → 有序摘要;内嵌图片落成独立的 SourceFile 行。
        #    结果写入 extracted_text,重拆不重跑(与 VLM 描述的缓存方式一致)。
        async with session_factory() as s:
            documents = [
                f for f in await tools.list_source_files(s, run.user_id, run.source_info_id)
                if f.kind == "document" and not f.extracted_text
            ]
            for document in documents:
                try:
                    data = storage.read(run.user_id, document.file_uuid)
                    extracted = await asyncio.to_thread(
                        extraction.extract_document, data, document.filename, document.mime
                    )
                except Exception as exc:  # noqa: BLE001 — 记录到摘要,不让整次拆解失败
                    log.exception("源文件抽取失败 file=%s", document.id)
                    document.extracted_text = f"(抽取失败: {type(exc).__name__}: {exc})"
                    await s.commit()
                    continue
                image_ids: list[int | None] = []
                for embedded in extraction.dump_images(extracted):
                    image_uuid = str(uuid_mod.uuid4())
                    storage.save(run.user_id, image_uuid, embedded.data)
                    row = await tools.create_source_file(
                        s,
                        run.user_id,
                        run.source_info_id,
                        image_uuid,
                        len(embedded.data),
                        kind="image",
                        filename=embedded.label,
                        mime=embedded.mime,
                        order_index=document.order_index,
                    )
                    image_ids.append(row.id if row else None)
                document.extracted_text = extraction.render_digest(extracted, image_ids)
                log.info(
                    "源文件抽取完成 file=%s (%s, %d 字符)",
                    document.id, document.filename, len(document.extracted_text),
                )
                await s.commit()

        # 2) VLM 描述图片(直接上传的 + 文档内嵌的)。无 VLM 时降级跳过并注明,不让整次拆解失败。
        async with session_factory() as s:
            files = await tools.list_source_files(s, run.user_id, run.source_info_id)
            images = [f for f in files if f.kind == "image"]
            documents = [f for f in files if f.kind == "document"]
            for image in images:
                if image.description:
                    continue
                if manager.vlm_client is None:
                    image.description = "(VLM 未配置,图片未解析)"
                    await s.commit()
                    continue
                data = storage.read(run.user_id, image.file_uuid)
                mime = image.mime or storage_mod.detect_image_mime(data)
                if mime is None:
                    image.description = "(无法识别的图片格式)"
                    await s.commit()
                    continue
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
            item_task_counts = await tools.count_tasks_by_source_item(
                s, run.user_id, [item.id for item in source_items]
            )
            task_overview = await tools.task_overview(s, run.user_id, timezone=tz)
        files_block = "\n".join(
            f"- 文件(id {document.id}) {document.filename}: "
            f"{len(document.extracted_text)} 字符,开头如下 → "
            f"{_preview(document.extracted_text)}"
            for document in documents
        ) or "(无文档)"
        images_block = "\n".join(
            f"- 图片(id {image.id}, 文件 {image.file_uuid}): {image.description or '(无描述)'}"
            for image in images
        ) or "(无图片)"
        source_items_block = "\n".join(
            f"- [{item.id}] {item.content}（已有任务 {item_task_counts.get(item.id, 0)} 个）"
            for item in source_items
        ) or "(尚未切分条目)"
        context = {
            "source_info_id": str(source_info.id),
            "current_time": now_local.isoformat(),
            "timezone": user.timezone or "UTC",
            "source_content": source_info.content or "(无文本内容)",
            "source_items": source_items_block,
            "files": files_block,
            "images": images_block,
            "task_overview": task_overview,
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
                    details=args.get("details"),
                    timezone=tz,
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
                    details=args.get("details"),
                    timezone=tz,
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
                    timezone=tz,
                )
                return json.dumps([tools.task_to_dict(t) for t in tasks], ensure_ascii=False)

        async def handler_update_task(args: dict) -> str:
            # 维护时只能动 details/status/deadline;描述与分类不由拆解修改。
            kwargs = {}
            if "status" in args:
                kwargs["status"] = args["status"]
            if "details" in args:
                kwargs["details"] = args["details"]
            if "deadline" in args:
                kwargs["deadline"] = _parse_deadline(args["deadline"], tz)
            async with session_factory() as s:
                task = await tools.update_task(
                    s, run.user_id, args["task_id"], timezone=tz, **kwargs
                )
                await s.commit()
                if task is None:
                    return json.dumps({"error": "task not found"})
                return _task_result(task)

        async def handler_move_task(args: dict) -> str:
            async with session_factory() as s:
                task = await tools.move_task(
                    s, run.user_id, args["task_id"], args["to_position"], timezone=tz
                )
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
                    source_file_id=args.get("source_file_id"),
                )
                await s.commit()
                if item is None:
                    return json.dumps({"error": "source info not found"})
                return json.dumps(tools.source_item_to_dict(item), ensure_ascii=False)

        async def handler_read_source_file(args: dict) -> str:
            async with session_factory() as s:
                result = await tools.read_source_file_text(
                    s,
                    run.user_id,
                    args["source_file_id"],
                    offset=args.get("offset") or 0,
                    limit=args.get("limit") or 4000,
                )
                if result is None:
                    return json.dumps({"error": "source file not found"})
                return json.dumps(result, ensure_ascii=False)

        async def handler_list_source_items(args: dict) -> str:
            async with session_factory() as s:
                items = await tools.list_source_items(s, run.user_id, args["source_info_id"])
                return json.dumps(
                    [tools.source_item_to_dict(i) for i in items], ensure_ascii=False
                )

        handlers = {
            "create_task": handler_create_task,
            "insert_task": handler_insert_task,
            "list_tasks": handler_list_tasks,
            "update_task": handler_update_task,
            "move_task": handler_move_task,
            "create_source_item": handler_create_source_item,
            "list_source_items": handler_list_source_items,
            "read_source_file": handler_read_source_file,
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
