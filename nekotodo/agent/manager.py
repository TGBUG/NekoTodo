from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

import httpx
from openai import AsyncOpenAI

from nekotodo.config import Settings

ToolHandler = Callable[[dict], Awaitable[str]]

log = logging.getLogger("nekotodo.agent")


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def build_tool_specs() -> list[dict]:
    deadline_prop = {"type": ["string", "null"], "description": "截止日期,ISO 8601 字符串"}
    category_prop = {"type": ["string", "null"], "description": "分类键,如 '工作'/'学习'"}
    return [
        _tool(
            "create_task",
            "新建一个可执行任务。priority 省略时追加到同一截止日组的末尾;给出时插入到该组内的该位置(1 为组内最前)。",
            {
                "description": {"type": "string", "description": "任务描述"},
                "details": {"type": ["string", "null"], "description": "任务细节(补充说明、要求等)"},
                "deadline": deadline_prop,
                "priority": {
                    "type": ["integer", "null"],
                    "description": "同一截止日组内位次 1..k",
                },
                "category": category_prop,
                "source_item_id": {"type": ["integer", "null"], "description": "来源条目 id"},
            },
            ["description"],
        ),
        _tool(
            "insert_task",
            "在指定位置插入一个新任务(priority 必填,指同一截止日组内的位次,1 为最前)。",
            {
                "description": {"type": "string", "description": "任务描述"},
                "details": {"type": ["string", "null"], "description": "任务细节(补充说明、要求等)"},
                "priority": {"type": "integer", "description": "同一截止日组内位次 1..k"},
                "deadline": deadline_prop,
                "category": category_prop,
                "source_item_id": {"type": ["integer", "null"], "description": "来源条目 id"},
            },
            ["description", "priority"],
        ),
        _tool(
            "list_tasks",
            "查看当前已有的任务,用于避免重复生成。可按分类或条目过滤。",
            {
                "category": {"type": ["string", "null"], "description": "分类键"},
                "source_item_id": {"type": ["integer", "null"], "description": "来源条目 id"},
            },
            [],
        ),
        _tool(
            "update_task",
            "维护已有任务的细节或时间安排:可改 details(任务细节)、status('incomplete'/'completed')或 deadline(任务被推迟、赶工提前时)。不得修改任务的描述或分类。",
            {
                "task_id": {"type": "integer", "description": "任务 id"},
                "status": {"type": ["string", "null"], "description": "'incomplete' 或 'completed'"},
                "details": {"type": ["string", "null"], "description": "任务细节,传 null 清空"},
                "deadline": {
                    "type": ["string", "null"],
                    "description": "截止日期,用户本地时间、不带时区偏移;传 null 清空",
                },
            },
            ["task_id"],
        ),
        _tool(
            "move_task",
            "把任务移动到同一截止日组内的指定位置(1 为组内最前),用于调整同一天到期任务的先后。",
            {
                "task_id": {"type": "integer", "description": "任务 id"},
                "to_position": {"type": "integer", "description": "同一截止日组内目标位次 1..k"},
            },
            ["task_id", "to_position"],
        ),
        _tool(
            "create_source_item",
            "把源信息内容切分为一个来源条目。文本源信息每条独立内容一个条目;内容中的要求/截止等约束不要切成条目。若条目来源于某张图片,填 source_file_id。",
            {
                "source_info_id": {"type": "integer", "description": "源信息 id"},
                "content": {"type": "string", "description": "该条目的内容"},
                "source_file_id": {
                    "type": ["integer", "null"],
                    "description": "来源文件 id(若条目来自某张图片)",
                },
            },
            ["source_info_id", "content"],
        ),
        _tool(
            "list_source_items",
            "查看该源信息已有的来源条目。",
            {"source_info_id": {"type": "integer", "description": "源信息 id"}},
            ["source_info_id"],
        ),
        _tool(
            "read_source_file",
            "读取某个上传文档的正文(按字符偏移分页)。{files} 里只有每个文件的预览;需要看完整内容时用本工具继续读。",
            {
                "source_file_id": {"type": "integer", "description": "文件 id(见 {files} 摘要)"},
                "offset": {"type": ["integer", "null"], "description": "起始字符偏移,默认 0"},
                "limit": {"type": ["integer", "null"], "description": "读取字符数,默认 4000"},
            },
            ["source_file_id"],
        ),
    ]


class AgentManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        # The AI endpoints are explicit config; do not silently route them
        # through whatever proxy the ambient environment happens to set.
        self._http_client = httpx.AsyncClient(trust_env=False)
        self.client = AsyncOpenAI(
            base_url=settings.llm.base_url,
            api_key=settings.llm.api_key or "sk-not-set",
            timeout=settings.llm.timeout_seconds,
            http_client=self._http_client,
        )
        self.vlm_client: AsyncOpenAI | None = None
        if settings.vlm.base_url and settings.vlm.model:
            self.vlm_client = AsyncOpenAI(
                base_url=settings.vlm.base_url,
                api_key=settings.vlm.api_key or "sk-not-set",
                timeout=settings.vlm.timeout_seconds,
                http_client=self._http_client,
            )

    async def aclose(self) -> None:
        await self._http_client.aclose()

    async def extract_image(self, prompt: str, data_uri: str) -> str:
        """Run the VLM once to turn an image into text. Data URI is base64."""
        if self.vlm_client is None:
            raise RuntimeError("VLM is not configured")
        log.info("VLM 请求: base_url=%s model=%s", self.settings.vlm.base_url, self.settings.vlm.model)
        try:
            response = await self.vlm_client.chat.completions.create(
                model=self.settings.vlm.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001 — surface endpoint/model with the original error
            log.exception("VLM 调用失败")
            raise RuntimeError(
                f"VLM 调用失败 (base_url={self.settings.vlm.base_url}, model={self.settings.vlm.model}): {exc}"
            ) from exc
        return response.choices[0].message.content or ""

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        handlers: dict[str, ToolHandler],
    ) -> tuple[int, str]:
        """Run the agent loop. Returns (tool_calls_used, final_message).

        The loop ends when the model returns a message with no tool calls.
        """
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        tool_calls_used = 0
        final_message = ""
        log.info("LLM 拆解开始: base_url=%s model=%s", self.settings.llm.base_url, self.settings.llm.model)
        for _ in range(self.settings.llm.max_iterations):
            try:
                response = await self.client.chat.completions.create(
                    model=self.settings.llm.model,
                    messages=messages,
                    tools=build_tool_specs(),
                )
            except Exception as exc:  # noqa: BLE001 — surface endpoint/model with the original error
                log.exception("LLM 调用失败")
                raise RuntimeError(
                    f"LLM 调用失败 (base_url={self.settings.llm.base_url}, model={self.settings.llm.model}): {exc}"
                ) from exc
            message = response.choices[0].message
            if message.tool_calls:
                tool_calls_used += len(message.tool_calls)
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": [tc.model_dump() for tc in message.tool_calls],
                    }
                )
                for tool_call in message.tool_calls:
                    name = tool_call.function.name
                    try:
                        args = json.loads(tool_call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    handler = handlers.get(name)
                    if handler is None:
                        result = f"未知工具: {name}"
                    else:
                        try:
                            result = await handler(args)
                        except Exception as exc:  # noqa: BLE001 — feed back to the model
                            result = f"工具执行失败: {type(exc).__name__}: {exc}"
                    messages.append(
                        {"role": "tool", "tool_call_id": tool_call.id, "content": str(result)}
                    )
                continue
            final_message = message.content or ""
            break
        else:
            raise RuntimeError("agent exceeded max iterations")
        return tool_calls_used, final_message
