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

DEFAULT_PROMPT = """你是一个待办拆解助手。用户会提交一段原始源信息(如一份上级派发的作业清单),其中可能内嵌要求(如截止时间、分类偏好),也可能不含有任务只是要求你维护任务列表;若上传了图片或文档,图片会附带 VLM 预处理出的文字描述,文档会被抽取为有序正文(内嵌图片以「[图片 id …]」标记在正文对应位置)。

已有任务概览:
{task_overview}

工作流程:
1. 先判断这次要做什么:是"新增待办",还是只"维护已有任务"(如标记完成、补充进度、调整顺序)。
   - 若只是维护,直接执行第 4 步的维护操作,不要凭空新建条目或任务。
   - 若是新增待办,继续往下。
2. 检查下方的条目清单。若内容尚未切分,调用 create_source_item 把原始内容切成条目:
   - 文本:每条独立的作业/事项切成一个条目。
   - 内容中的要求、截止、偏好等约束不要单独切成条目——它们用来指导任务的 deadline/category/priority。
   - 图片:根据其文字描述拆出条目,并用 source_file_id 关联到对应图片。
   - 文档:{files} 里每个文件只有开头预览;请用 read_source_file(source_file_id, offset) 按顺序读完正文,再按文档顺序切条目。正文中「[图片 id …]」标记处的图片可作为该条目的 source_file_id。
   - 条目清单是本次运行开始时的快照;若你新建了条目、需要确认结果,可再调用 list_source_items。
3. 只为"已有任务数为 0 的条目"生成任务(清单中每条都标注了已有任务数),用 create_task 创建:
   - 一条可能拆出多个任务(若包含多个可执行步骤)。
   - 用 source_item_id 把任务关联到对应条目。
   - 分类优先复用概览里已有的分类,避免制造近义的新分类;仅当内容明显不属于任何已有分类时才新建。
   - 按内容中的约束设置 deadline 与 category;任务的补充说明、要求写进 details(任务细节)。
   - 任务默认按截止日期先后排列:deadline 是排序主键,priority 只是"同一天到期"时组内的位次。priority 省略即追加到该组末尾;只有当内容明确要求某任务排在同日任务之前时才给出,或用 insert_task 定点插入。
4. 维护已有任务时:绝不删除任务,也绝不修改已有任务的描述或分类;可维护的部分是 details(任务细节)、status(incomplete/completed)与 deadline(任务被推迟或赶工提前时)。用 move_task 调整同一截止日组内的先后。若指向的任务不明确,先用 list_tasks 找到它。
5. 概览只是节选,不是全量。若怀疑某个任务已经存在,先调用 list_tasks(category=...) 取该分类的准确清单,再决定是否新建,避免重复。
6. 全部完成后,输出一条不含工具调用的普通消息,总结你做了什么。

源信息 id: {source_info_id}

源信息文本:
{source_content}

源信息已有条目(每条标注已有任务数):
{source_items}

上传的文档(仅开头预览;要读正文请用 read_source_file):
{files}

图片内容(每条:图片id、文件UUID、描述):
{images}

当前用户本地时间: {current_time}(时区 {timezone})
截止日期请传用户本地时间、不带时区偏移(如 2026-08-14T23:59);时区换算由系统处理,不要自行做时差加减。"""

# 图片预处理提示词(VLM 专用,当前不可配置)
IMAGE_EXTRACTION_PROMPT = "请仔细查看这张图片,提取其中信息,尤其注意要把其中出现的作业/任务/待办内容逐条、完整地提取出来。用中文逐条列出,保留原意与关键信息(如科目、页数、要求、截止时间)。若图片不含以上信息,如实说明。"


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    # 允许跨域请求(测试/特殊环境用);开启后服务处理 OPTIONS 预检请求。默认关闭。
    allow_cors: bool = False


class DatabaseSettings(BaseModel):
    path: str = "./nekotodo.db"


class FilesSettings(BaseModel):
    dir: str = "./uploads"


class AuthSettings(BaseModel):
    jwt_secret: str = ""
    algorithm: str = "HS256"


class LLMSettings(BaseModel):
    base_url: str = "https://api.deepseek.com"
    api_key: str = "sk-xxxx"
    model: str = "deepseek-v4-flash"
    timeout_seconds: float = 120.0
    max_iterations: int = 100


class VLMSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_seconds: float = 120.0


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
    files: FilesSettings = FilesSettings()
    auth: AuthSettings = AuthSettings()
    llm: LLMSettings = LLMSettings()
    vlm: VLMSettings = VLMSettings()
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
