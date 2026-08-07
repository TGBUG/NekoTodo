from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_PROMPT = """你是一个待办拆解助手。用户会提交一段原始源信息(如一份上级派发的作业清单),其中可能内嵌要求(如截止时间、分类偏好)。

工作流程:
1. 先调用 list_source_items 查看该源信息已有的条目。若内容尚未切分,调用 create_source_item 把原始内容切成条目:
   - 文本:每条独立的作业/事项切成一个条目。
   - 内容中的要求、截止、偏好等约束不要单独切成条目——它们用来指导任务的 deadline/category/priority。
   - 图片等难以逐条划分的媒体:整份作为一个条目。
2. 为每个"还没有任务的条目"调用 create_task 生成可执行任务:
   - 一条可能拆出多个任务(若包含多个可执行步骤)。
   - 用 source_item_id 把任务关联到对应条目。
   - 按内容中的约束设置 deadline(ISO 8601)、category、priority(1 为最顶/最急)。
3. 绝不删除或修改已有任务。不确定时先 list_tasks,避免重复生成。
4. 全部完成后,输出一条不含工具调用的普通消息,总结你生成了什么。

时间上下文:系统会提供当前用户本地时间,相对表述(如"明天""下周一")以此为准。截止日期请传 ISO 8601;时区换算由系统处理,不要自行做时差加减。"""


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class DatabaseSettings(BaseModel):
    path: str = "./nekotodo.db"


class AuthSettings(BaseModel):
    jwt_secret: str = ""
    algorithm: str = "HS256"


class AISettings(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    timeout_seconds: float = 120.0
    max_iterations: int = 100


class PromptSettings(BaseModel):
    default_template: str = DEFAULT_PROMPT

    @field_validator("default_template")
    @classmethod
    def _fallback_to_default(cls, value: str) -> str:
        return value.strip() or DEFAULT_PROMPT


class RegistrationSettings(BaseModel):
    allow_public: bool = False
    require_turnstile: bool = False
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""


class RunsSettings(BaseModel):
    retention_days: int = 30


class Settings(BaseSettings):
    server: ServerSettings = ServerSettings()
    database: DatabaseSettings = DatabaseSettings()
    auth: AuthSettings = AuthSettings()
    ai: AISettings = AISettings()
    prompt: PromptSettings = PromptSettings()
    registration: RegistrationSettings = RegistrationSettings()
    runs: RunsSettings = RunsSettings()

    model_config = SettingsConfigDict(
        env_prefix="NEKOTODO_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # env overrides the config file; explicitly passed init overrides env
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))


_config_path_override: str | None = None
_settings: Settings | None = None


def set_config_path(path: str | None) -> None:
    global _config_path_override
    _config_path_override = path


def resolve_config_path() -> Path:
    raw = _config_path_override or os.environ.get("NEKOTODO_CONFIG") or "./config.toml"
    return Path(raw)


class TomlConfigSettingsSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        path = resolve_config_path()
        if not path.exists():
            return {}
        with path.open("rb") as f:
            return tomllib.load(f)

    def __call__(self) -> dict[str, Any]:
        return self.data

    def get_field_value(self, field: Any, field_name: str):
        return self.data.get(field_name), field_name, False


def load_settings(config_path: str | None = None) -> Settings:
    global _settings
    set_config_path(config_path)
    _settings = Settings()
    return _settings


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
