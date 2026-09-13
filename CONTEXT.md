# NekoTodo

An AI-assisted todo system. Users either manage executable todos manually, or submit raw source information (e.g. a homework list handed down by a superior, with any requirements like deadlines embedded in the text), and an agent decomposes it into concrete, executable todos.

## Language

**Account**:
A user's credentials for logging in (username, password hash). Multiple valid access tokens may be issued for a single Account at once — one per device — so the system is designed for multi-device sync from the start. An Account is linked to exactly one User. Tokens do not expire: once issued they stay valid until the auth version is bumped (on password change, or via the revoke-all-tokens endpoint), which invalidates every previously issued token at once. Per-token revocation is explicitly out of scope for now.
_Avoid_: login, 账户 (when meaning the data-owning User)

**User**:
The data-owning entity behind an Account: owns Tasks, SourceInfos, and User Preferences.
_Avoid_: person, profile

**Task**:
A single executable todo item — the unit of work a user tracks. Has a description, a status (`incomplete` | `completed`), optional free-text details holding any extra task details (requirement notes, "已背前 3 段", etc.), an optional ISO 8601 deadline, and a Priority. A Task may optionally carry a Category; a generated Task always carries a Source, a manually added Task may have none.
_Avoid_: Todo, item, progress (as a numeric 0–100 scale)

**SourceInfo**:
The raw source information a user submits to the Agent in a single submission — e.g. a homework list handed down by a superior. Requirements (deadlines, categorization preferences) are embedded in its content, not stored separately. Persisted as a first-class entity, holding one or more SourceItems and zero or more SourceFiles.
_Avoid_: Assignment, source info (as a one-off), task list, 作业清单 (when meaning SourceInfo)

**SourceItem**:
An entry within a SourceInfo. For text content, one item per entry. Tasks are generated from SourceItems. A SourceItem may reference a SourceFile (`source_file_id`) when it was derived from an image.
_Avoid_: source entry, line item

**SourceFile**:
A file attached to a SourceInfo, stored on disk under a UUID. Its `kind` is either `image` (the VLM preprocesses it into `description`; the language model never sees the raw image) or `document` (extracted into an ordered digest in `extracted_text`, with images embedded in the document becoming SourceFiles of kind `image` and referenced inline as `[图片 id …]`). Extraction is cached: re-running a decomposition never re-extracts. Support is deliberately narrow — images, `.docx`, `.pptx`, `.pdf`, and plain text; legacy binary `.doc`/`.ppt`/`.xls` are rejected.
_Avoid_: attachment, 图片 (when meaning any SourceFile), content_type (the `mime` is stored, but detection falls back to sniffing magic bytes)

**Source**:
The relationship from a Task back to the SourceItem it was decomposed from. A single SourceItem may yield many Tasks, so the relationship is many-to-one. Optional — manually added Tasks have no Source.
_Avoid_: origin (when meaning Source)

**Category**:
A single free-form string key a user assigns to a Task for grouping (e.g. `工作`, `学习`). The backend stores it as opaque data; the frontend uses it to group tasks when listing. Not a controlled vocabulary at the backend.
_Avoid_: tag, label, list

**Priority**:
A Task's position *within its deadline-day group* — the set of the user's Tasks sharing the same local due date; undated Tasks form their own group, ordered last. 1 is the top of the group, and positions are contiguous within a group, so inserting, moving, or deleting a Task shifts the others in that group only. Deadline is the primary ordering key and Priority exists to break ties between Tasks due on the same day; the Agent may reorder Tasks to express same-day urgency.
_Avoid_: urgency level, rank, priority score, global position

**Deadline**:
A Task's optional due timestamp, stored as ISO 8601 in UTC, and the primary key for ordering task lists. Its meaning is relative to the user's local clock, so it is interpreted through the user's Timezone; the ordering day is the local calendar day. Overdue is derived, not stored: `deadline < now` while the Task is incomplete.
_Avoid_: due date (when meaning an instant), DDL

**Timezone**:
The user's local timezone, part of their preferences. The backend stores UTC timestamps and converts to/from the user's local time; the Agent never performs timezone arithmetic — it works in user-local terms and the Tools layer applies the conversion.
_Avoid_: time zone, offset (when meaning the full setting)

**User Preferences**:
The user's personalization settings: a custom prompt template (overriding the system default) and their Timezone.
_Avoid_: settings, profile

**DecompositionRun**:
A single asynchronous execution of the Agent decomposing a SourceInfo into Tasks. Identified by a UUID returned to the frontend for polling. Has a status (`pending` | `running` | `completed` | `failed`) and, on completion, references the Tasks it created.
_Avoid_: decomposition job, 拆解任务 (when meaning the API record)
