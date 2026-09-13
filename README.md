# NekoTodo

*Powered by DeepSeek*

AI 加持的待办清单系统后端。既保留经典待办清单的手动操作能力,也能把"上级派发的整份作业清单 + 你的要求"直接丢给智能体,由它拆分成具体可执行的待办事项。

核心闭环:提交源信息 → 异步拆解(Agent 自动切分条目并生成待办)→ 前端轮询结果 → 之后照常手动跟进。

## 核心数据模型:SourceInfo → SourceItem → Task

系统的核心价值是把"原始源信息"变成"可执行待办"。三类实体环环相扣,角色如下:

| 实体 | 角色 | 说明 |
| --- | --- | --- |
| **SourceInfo** 源信息 | 输入 | 用户单次提交的原始信息:一段文本(如作业清单),可选附带多个文件(图片/文档);要求(截止时间、分类偏好等)内嵌在内容中,不单独存储 |
| **SourceFile** 来源文件 | 多模态输入 | 随源信息上传的文件,存于 `[files].dir`。`kind=image` 的图片由 VLM 预处理成文字描述;`kind=document` 的文档(docx/pptx/pdf/纯文本)被抽取为**有序正文**,内嵌图片落成独立的 `kind=image` 行并以「[图片 id …]」标记在正文对应位置 |
| **SourceItem** 来源条目 | 拆解中间产物 | Agent 把源信息切分成的条目(如"数学:练习册p50-52"),可关联到某个 `SourceFile` |
| **Task** 任务 | 输出 | 最终的可执行待办,由条目生成;带描述/状态/details/截止/优先级/分类 |

实体间关系:

```
SourceInfo ──1:N──► SourceItem ──1:N──► Task      (Task 经 source_item_id 指向条目)
SourceInfo ──1:N──► SourceFile                  (文件随源信息上传)
SourceItem ──0..1──► SourceFile                  (条目经 source_file_id 溯源到某个文件)
```

一次拆解的完整链路:

1. 用户提交 `SourceInfo`(multipart:文本 `content` + 多个文件 `files`)
2. 图片由 **VLM 先预处理成文字描述**存入 `SourceFile.description`;文档先**抽取为有序正文**(内嵌图片同样走 VLM 描述)——主语言模型不直接看图,规避视觉模型文本能力弱的问题
3. Agent 把源信息内容(文本 + 文件正文 + 图片描述)切分为 `SourceItem`,需要时关联到 `SourceFile`
4. 为每个**尚无任务**的条目生成 `Task`(只为缺任务的条目补任务,不改已有任务的描述与分类)
5. 前端经 `source_item_id` / `source_file_id` 溯源:"这个待办来自哪条作业、哪个文件/哪张图"

## 技术栈

- Python 3.13+、FastAPI、uv
- SQLite + async SQLAlchemy
- OpenAI 兼容协议接入大模型(端点/Key/模型可配置,支持 DeepSeek、Ollama 等)
- 文档抽取:python-docx(docx)、python-pptx(pptx)、PyMuPDF(pdf,含页面栅格化)
- JWT 鉴权(多端并存、无过期时间、auth_version 撤销)

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 准备配置

复制示例配置并修改:

```bash
cp example-config.toml config.toml
```

至少需要设置 `[auth].jwt_secret`(一段足够长的随机字符串)与 `[ai]` 下的端点/Key/模型。完整字段见[配置说明](#配置说明)。

### 3. 注册账户

系统默认**不开放公开注册**,可使用 CLI 手动注册:

```bash
nekotodo register alice --password your-password --timezone Asia/Shanghai
```

其他 CLI 命令见[命令行工具](#命令行工具)。

### 4. 启动服务

```bash
python main.py --config config.toml
```

或通过环境变量指定配置(两种等价):

```bash
NEKOTODO_CONFIG=config.toml uvicorn nekotodo.app:app
```

默认监听 `127.0.0.1:8000`,可用 `[server]` 配置修改。

### 5. 使用前端

目前提供了两个简易的单HTML文件前端，后续开发Android应用等
（图像素材来源于网络，如侵犯了您的权益请联系删除）

## 配置说明

配置文件为 TOML 格式。加载优先级:`--config` 命令行参数 > `NEKOTODO_CONFIG` 环境变量 > 当前目录 `./config.toml`。

| 段 | 字段 | 说明 |
| --- | --- | --- |
| `[server]` | `host` / `port` | 监听地址与端口 |
| `[server]` | `allow_cors` | 允许跨域请求(默认 `false`);开启后服务处理 OPTIONS 预检,允许任意来源,适合测试/特殊环境 |
| `[database]` | `path` | SQLite 文件路径 |
| `[files]` | `dir` | 上传文件存储目录,按用户分目录、UUID 文件名 |
| `[auth]` | `jwt_secret` / `algorithm` | JWT 签名密钥(必填)与算法 |
| `[llm]` | `base_url` / `api_key` / `model` | 语言大模型(拆解循环),OpenAI 兼容端点 |
| `[llm]` | `timeout_seconds` / `max_iterations` | 单次请求超时 / Agent 循环最大轮数 |
| `[vlm]` | `base_url` / `api_key` / `model` | 视觉模型(图片预处理);允许留空——留空时直接提交图片会返回 400,文档内嵌图片则降级跳过 |
| `[prompt]` | `default_template` | 拆解系统提示词模板,支持占位符;留空使用内置模板 |
| `[registration]` | `allow_public` / `require_turnstile` | 是否开放公开注册;是否要求 Cloudflare Turnstile 验证 |
| `[registration]` | `turnstile_site_key` / `turnstile_secret_key` | Turnstile 站点与密钥 |
| `[runs]` | `retention_days` | 拆解运行记录保留窗口(天) |

敏感项可用环境变量覆盖(优先级高于文件):

```bash
NEKOTODO_AUTH__JWT_SECRET=...
NEKOTODO_LLM__API_KEY=...
NEKOTODO_VLM__API_KEY=...
NEKOTODO_REGISTRATION__TURNSTILE_SECRET_KEY=...
```

提示词模板占位符:`{source_info_id}` 源信息 id / `{source_content}` 源信息文本 / `{source_items}` 已有条目(每条标注已有任务数) / `{files}` 上传文档的开头预览(正文由 Agent 用 `read_source_file` 工具按需读取) / `{images}` VLM 提取的图片描述 / `{task_overview}` 已有任务概览(各分类的完成/未完成计数 + 每类少量采样标题,拆解启动时生成) / `{current_time}` 当前用户本地时间 / `{timezone}` 用户时区。用户自定义模板可自由摆放;模板里没有的占位符数据不会注入(不自动追加)。

## 命令行工具

| 命令 | 说明 |
| --- | --- |
| `nekotodo register <用户名> [--password ...] [--timezone ...]` | 手动注册账户 |
| `nekotodo list-accounts` | 列出全部账户 |
| `nekotodo revoke-all <用户名>` | 使该账户所有 token 立即失效 |
| `nekotodo reset-password <用户名> [--password ...]` | 重置密码并撤销所有 token |
| `nekotodo delete-account <用户名> [--yes]` | 删除账户及全部数据(任务/源信息/文件),需二次确认 |

所有命令接受 `--config <path>` 指定配置。

## API 文档

- **Base URL**:`http://<host>:<port>`
- **鉴权**:除 `POST /auth/register` 与 `POST /auth/login` 外,所有请求需携带请求头 `Authorization: Bearer <token>`。
- **多用户隔离**:每个账户只能访问自己的数据;越权访问一律返回 `404`(不暴露是否存在)。
- **错误格式**:非 2xx 响应体统一为 `{"detail": "<原因>"}`。
- **时间**:时间戳一律为 **UTC** 的 ISO 8601 字符串;展示本地时间由前端结合用户时区换算。

### 领域概念速览

| 概念 | 说明 |
| --- | --- |
| `Task` 任务 | `description` / `status`(`incomplete`\|`completed`)/ `details`(自由文本,任务细节)/ `deadline?`(UTC)/ `priority` / `category?` / `source_item_id?` |
| `priority` | **同一截止日组内**的位置序位:1 为组内最前,取值 1..该组任务数,组内无并列无空位。排序以 `deadline` 为主键,`priority` 只在截止日相同时决定先后;无截止的任务自成一组排在最后。插入/移动/删除只在该组内带动其他任务移位 |
| `SourceInfo` 源信息 | 用户单次提交的原始信息(如作业清单);要求内嵌在内容中;可附带多个文件(`source_files`) |
| `SourceFile` 来源文件 | 随源信息上传的文件(`kind`: `image` \| `document`),存于 `[files].dir`;图片由 VLM 描述,文档抽取为有序正文(`extracted_text`)后供拆解 |
| `SourceItem` 来源条目 | 源信息内的一条;Task 通过 `source_item_id` 指向它(多对一);可选 `source_file_id` 溯源到某个文件 |
| `DecompositionRun` 拆解运行 | 一次异步拆解,`pending → running → completed/failed`,返回 UUID 供轮询 |

### 鉴权

#### GET `/auth/registration` — 注册能力说明

无需鉴权。登录窗口用它决定显示方式:是否显示「注册」、是否需要渲染 Turnstile 组件。

返回:

```json
{
  "allow_public": true,          // 是否开放公开注册(关闭时账户仅能由 CLI 创建)
  "require_turnstile": true,     // 注册是否要求人机验证
  "turnstile_site_key": "0x4AAA..."  // 仅在 require_turnstile 时下发;secret 永不下发
}
```

#### POST `/auth/register` — 公开注册
仅在配置 `allow_public = true` 时可用;开启 `require_turnstile` 时必须携带 `turnstile_token`(由前端 Turnstile 组件获得)。

请求体:

```json
{
  "username": "alice",          // 必填,3-255 字符
  "password": "password123",    // 必填,至少 8 字符
  "turnstile_token": "..."      // 选填,启用 Turnstile 时必填
}
```

返回:

```json
{ "token": "<jwt>" }
```

错误:`403` 公开注册未开放;`400` Turnstile 校验失败;`409` 用户名已存在。

#### POST `/auth/login` — 登录

请求体:

```json
{ "username": "alice", "password": "password123" }
```

返回:

```json
{ "token": "<jwt>" }
```

错误:`401` 用户名或密码错误。

#### POST `/auth/revoke-all` — 撤销全部 token(需鉴权)

使当前账户所有已签发 token 立即失效(`auth_version` +1)。

返回:

```json
{ "ok": true }
```

#### POST `/auth/change-password` — 修改密码(需鉴权)

修改成功后同样撤销全部 token。

请求体:

```json
{ "old_password": "old", "new_password": "new-password" }
```

返回:

```json
{ "ok": true }
```

错误:`400` 原密码错误。

#### POST `/auth/delete-account` — 注销账户(需鉴权)

**不可逆**:删除当前账户及其全部数据(任务、源信息、条目、图片、拆解记录与磁盘文件)。需携带当前密码确认。

请求体:

```json
{ "password": "password123" }
```

返回:

```json
{ "ok": true }
```

错误:`400` 密码错误。

#### GET `/auth/me` — 当前用户信息(需鉴权)

返回:

```json
{
  "id": 1,
  "username": "alice",
  "custom_prompt_template": null,
  "timezone": "Asia/Shanghai"
}
```

### 个性化设置

#### GET `/me/preferences`(需鉴权)

返回:

```json
{
  "custom_prompt_template": null,
  "timezone": "UTC"
}
```

#### PATCH `/me/preferences`(需鉴权)

请求体(字段均可选,只更新传入的字段):

```json
{
  "custom_prompt_template": "你是一个贴心的拆解助手……",
  "timezone": "Asia/Shanghai"
}
```

返回:更新后的 `{custom_prompt_template, timezone}`。

### 任务

Task 对象:

```json
{
  "id": 1,
  "description": "买牛奶",
  "status": "incomplete",
  "details": "",
  "deadline": null,
  "priority": 1,
  "category": "生活",
  "source_item_id": null
}
```

#### GET `/tasks`(需鉴权)

查询参数(均可选):

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `category` | string | 按分类过滤 |
| `source_item_id` | integer | 按来源条目过滤 |
| `completed` | boolean | `true` 只返回已完成(status=completed) |

返回:`[Task, ...]`,按 `deadline` 升序(`deadline` 相同时按组内 `priority`);无 `deadline` 的任务排在最后。

#### POST `/tasks` — 创建任务(需鉴权)

请求体:

```json
{
  "description": "买牛奶",        // 必填
  "deadline": "2026-08-10T00:00:00Z",  // 选填,UTC ISO 8601
  "priority": 1,                 // 选填,同一截止日组内位次;缺省追加到该组末尾
  "category": "生活"              // 选填
}
```

返回:新建的 `Task`。错误:`400` 描述为空。

#### GET `/tasks/{task_id}`(需鉴权)

返回:对应 `Task`。错误:`404` 不存在。

#### PATCH `/tasks/{task_id}`(需鉴权)

请求体(字段均可选;传 `null` 表示清空):

```json
{
  "description": "买牛奶和鸡蛋",
  "status": "completed",      // incomplete | completed
  "details": "已买牛奶,还差鸡蛋",
  "deadline": "2026-08-12T00:00:00Z",
  "category": "生活"
}
```

返回:更新后的 `Task`。错误:`400` status 非法或描述为空;`404` 不存在。

> 调整顺序请用 `move`,此处不含 `priority`;改动 `deadline` 会让任务换到新截止日组的末尾。

#### DELETE `/tasks/{task_id}`(需鉴权)

删除任务;若这是该 `SourceInfo` 最后一个被引用的任务,则该 `SourceInfo` 及其 `SourceItem` 会被连带自动清理。

返回:

```json
{ "ok": true }
```

错误:`404` 不存在。

#### POST `/tasks/{task_id}/move` — 移动组内顺序(需鉴权)

只在任务自己的截止日组内移动:目标位次是该组内的位置,不会跨截止日。

请求体:

```json
{ "to_position": 2 }
```

返回:移动后的 `Task`(priority 已更新)。错误:`404` 不存在。

### 源信息

SourceInfo 对象:

```json
{
  "id": 1,
  "content": "寒假作业清单:语文:abcd,数学:efgh,要在8月14日前做完",
  "created_at": "2026-08-05T16:00:00",
  "updated_at": "2026-08-05T16:00:00"
}
```

#### POST `/source-infos` — 提交源信息并拆解(需鉴权)

提交源信息后**立即开始异步拆解**,返回 `run_id` 供轮询。请求体为 **multipart/form-data**:

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `content` | text | 源信息文本(选填,要求内嵌其中) |
| `files` | file(可多个) | 文件(选填)。支持图片(png/jpeg/webp/gif)、`.docx`、`.pptx`、`.pdf` 与纯文本(`.txt`/`.md`/`.csv`/`.json` 等) |

`content` 与 `files` 至少提供其一。**直接上传图片**需要已配置 `[vlm]`,否则返回 400;文档不要求 VLM——文档在拆解时被抽取为有序正文(见下),其中内嵌图片在未配置 `[vlm]` 时**降级跳过并注明**,不会导致整次拆解失败。单个文件上限 20MB。

文档抽取在上传后的异步拆解中进行(不阻塞上传请求),结果缓存在 `extracted_text`,重拆不会重复抽取。抽取上限:60 页/slide、30 张内嵌图、4 万字。

返回:

```json
{ "source_info_id": 1, "run_id": "c90ce119-5baa-4f0c-a219-2a566cdcac9e" }
```

错误:`400` 内容与文件均缺失 / 文件类型不支持(含支持列表)/ 文件过大 / 直接上传图片但 VLM 未配置。

#### GET `/source-infos`(需鉴权)

返回:`[SourceInfo, ...]`。

#### GET `/source-infos/{source_info_id}`(需鉴权)

返回:`SourceInfo` 对象,附加来源条目、任务与文件列表:

```json
{
  "id": 1,
  "content": "...",
  "created_at": "...",
  "updated_at": "...",
  "source_items": [ { "id": 1, "source_info_id": 1, "content": "语文:abcd", "source_file_id": null } ],
  "tasks": [ /* Task 数组,该源信息拆出的全部任务 */ ],
  "source_files": [
    { "id": 1, "source_info_id": 1, "kind": "image", "filename": "photo.jpg", "mime": "image/jpeg",
      "file_uuid": "3f2a...", "size": 20480, "order_index": 0,
      "description": "语文作业:背诵第3课", "has_text": false, "text_chars": 0 },
    { "id": 2, "source_info_id": 1, "kind": "document", "filename": "清单.docx", "mime": "application/vnd...",
      "file_uuid": "9b1c...", "size": 51200, "order_index": 1,
      "description": "", "has_text": true, "text_chars": 1823 }
  ]
}
```

> 文档的正文(`extracted_text`)不出现在此响应里(可能很长);Agent 通过 `read_source_file` 工具按偏移读取。

错误:`404` 不存在。

#### GET `/files/{file_uuid}` — 获取上传文件(需鉴权)

用源信息详情里的 `file_uuid` 换取文件原始数据。仅能获取当前用户自己的文件。存储本身是类型无关的。

返回:文件二进制流,`Content-Type` 取该文件的 `mime`(回退到按内容嗅探,再回退 `application/octet-stream`)。错误:`404` 不存在或无权限。

#### PATCH `/source-infos/{source_info_id}` — 修改内容并重新拆解(需鉴权)

修改内容后**触发重新拆解**(只为尚无任务的条目补任务),并返回新的 `run_id`。

请求体:

```json
{ "content": "新的源信息内容" }
```

返回:

```json
{ "source_info_id": 1, "run_id": "c90ce119-5baa-4f0c-a219-2a566cdcac9e" }
```

错误:`400` 未提供 `content`;`404` 不存在;`409` 该源信息已有进行中的运行(pending/running)。

#### DELETE `/source-infos/{source_info_id}`(需鉴权)

级联删除:该源信息的 `SourceItem`、拆出的全部任务以及相关拆解运行记录一并删除。

返回:

```json
{ "ok": true }
```

错误:`404` 不存在。

### 拆解任务

Run 对象:

```json
{
  "id": "c90ce119-5baa-4f0c-a219-2a566cdcac9e",
  "source_info_id": 1,
  "status": "completed",          // pending | running | completed | failed
  "created_task_ids": [1, 2],
  "error": null,
  "created_at": "2026-08-05T16:00:00",
  "updated_at": "2026-08-05T16:00:10"
}
```

#### GET `/runs`(需鉴权)

返回:`[Run, ...]`,按创建时间倒序。

#### GET `/runs/{run_id}`(需鉴权)

返回:对应 `Run`。错误:`404` 不存在。
