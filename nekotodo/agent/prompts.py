from __future__ import annotations

from datetime import datetime

from nekotodo.models import SourceInfo, SourceItem


def build_system_prompt(
    default_template: str,
    user_template: str | None,
    timezone_name: str,
    now_local: datetime,
) -> str:
    template = user_template or default_template
    return "\n\n".join(
        [
            template,
            f"当前用户本地时间: {now_local.isoformat()}(时区 {timezone_name})",
            "设置截止日期时直接传 ISO 8601 字符串即可,时区换算由系统处理,不要自行做时差加减。",
        ]
    )


def build_user_message(source_info: SourceInfo, source_items: list[SourceItem]) -> str:
    items = "\n".join(f"- [{item.id}] {item.content}" for item in source_items)
    if not items:
        items = "(尚未切分条目)"
    return (
        f"请拆解以下源信息(源信息 id = {source_info.id}):\n\n{source_info.content}\n\n"
        f"当前已有条目:\n{items}\n\n"
        "请先切分/确认条目,再为还没有任务的条目生成任务;把源信息中内嵌的要求"
        "(如截止时间、分类偏好)应用到任务的 deadline/category/priority,不要把它们单独切成条目。"
    )
