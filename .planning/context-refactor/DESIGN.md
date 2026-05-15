# Context 模块统一重构 — Design v3.2

- 日期: 2026-05-15（v3.2 修订）
- 状态: 草稿 v3.2（移除 `source_query_event_id` 外键语义，`user_turn_context` 通过顶层 `invocation_id` 关联用户请求），待作者复核
- 作者: kealdoom + Claude + GPT
- 范围: `matmaster/` 与 `src/services/` 中所有 provider-facing 上下文相关的代码与数据流

---

## 0. 关键变化日志

### 0.1 v3.2 相对 v3.1 的关键变化

v3.2 的核心修正是按"该字段是否影响 provider-facing 输出或 restore 正确性"原则清除冗余 event_id 字段。该字段只服务事件审计或与现有字段重复的，全部删除或下沉到 assembler 内部派生。

| 项 | v3.1 | v3.2 |
|----|------|------|
| `user_turn_context` 与 `User/query` 关联 | payload 内保存 `source_query_event_id`，依赖 DAO inserted id 返回链路 | 通过 events 表顶层 `session_id + spawn_id + invocation_id` 关联，同一 root 用户请求最多一条 `user_turn_context` |
| `TurnInput` 字段 | 含 `source_query_event_id` | 删除；`TurnInput` 只承载会影响 provider-facing user message 的输入与 preflight 边界 |
| `TurnAssemblyRequest` 字段 | 含 `pre_turn_history_event_id`（与 `turn_input.pre_turn_history_event_id` 重复） | 删除该字段；assembler 内部直接读 `request.turn_input.pre_turn_history_event_id`，消除 caller 双写不一致风险 |
| `TurnInput.pre_turn_history_event_id` 类型 | `int \| None = None`（用 None 同时表达"无历史"与"无上界"，语义冲突） | `int = 0`；`0` 表示 session 首轮前无任何 event，`N>0` 表示历史最后一个 event id。"无上界"只能通过 `SessionEventQuery.until_event_id=None` 表达，不再与"无历史"共享 None |
| `CompactionAssemblyRequest.covered_until_event_id` | 必填；caller（compactor）本地派生后显式传入 | 按 intent 分流，assembler 校验：`PREFLIGHT` 可省略，从 `turn_input.pre_turn_history_event_id` 派生；`RUNTIME` **必须**显式传入非 None int（caller/kernel 提供当前事件流 high-water id）。assembler 不为 runtime 隐式派生，避免 checkpoint payload 写出 null 边界破坏 restore |
| `history_checkpoint.v1.covered_until_event_id` | 写入路径未约束（可能 null） | 必须非 null int；restore 端遇 null 视为 checkpoint 损坏，回退 `COMPAT:v0-restore` |
| `AssemblyResult.covered_until_event_id` | — | 新增字段；compaction 路径下 assembler 保证非 None；turn 路径下保持 None。compactor 从该字段读真实边界写入 checkpoint payload，消除"派生规则在两处实现"风险 |
| Phase 0 硬依赖 | DAO 返回 inserted event id + 文件拆分 | 仅保留文件拆分；DAO inserted id 不再是本 spec 前置依赖 |

### 0.2 v3.1 相对 v3 的关键变化

v3.1 的核心目标是消除 v3 末尾仍然散落在 §7.3 / §8.2 / §9.2 的"该用哪些 source 装配 user_turn_context"决策。引入 **Context Assembly Ports + Compositions + Assembler** 三层架构：

| 项 | v3 | v3.1 |
|----|-----|------|
| Source 装配决策 | service 与 compactor 各自手写 `from_sources(...)` 拼装 | 收口到 `matmaster/context/compositions.py`，业务代码不再直接拼 |
| 平台数据获取 | service 与 compactor 各自调 DAO / 文件 / Redis | 通过 `matmaster/context/ports.py` 声明的窄能力 Protocol 注入，`src/services` 实现 |
| AGENT.md 读取 | service helper `_load_user_instructions` 私有读 | `UserInstructionsPort.load_user_instructions` 声明在核心模块，`src/services/context_assembly_ports.py` 实现；同一 turn 内 bundle 必须原样传给 assembler，**禁止二次读取** |
| anchor / continuation 判定 | service 内 inline 判定 | 仍在 service（核心模块不感知迁移兼容策略），但抽出 `matmaster/context/turn_intent.py` 提供纯函数 + `src/services/context_turn_intent.py` 做 events 查询 |
| SessionContextBuilder 归属 | `matmaster/context/session.py`（v3 已定） | 不变，但**重申**：events → sections 的装配规则必须由核心模块完成，不能挪到 port 实现 |
| 装配入口 | `UserTurnContext.from_sources(...)` 直接调用 | `ContextAssembler.assemble_turn` / `assemble_compaction`，内部走 composition；`from_sources` 降级为底层机械合并器，仅 `ContextComposition.apply()` 调用 |
| 硬约束条数 | 11 条 | 12 条（新增"装配产物不得作为 port 返回值"等） |

如果你只读 v3 留下的差异，请直接跳到 §6bis（Composition 装配层）、§7bis（Context Assembly Ports + Assembler）、§8.2、§9.2。

### 0.3 v3 相对 v2 的关键变化（保留）

| 项 | v2 | v3 |
|----|-----|-----|
| 事件模型 | `user_context_snapshot`（每次 LLM 调用前一帧） | `user_turn_context`（每个真实用户 turn 一条），无 snapshot 概念 |
| AGENT.md 响应性 | 冻结到 anchor，下次压缩才更新 | hash 变化即触发新 anchor user_turn_context，**下一轮立即生效** |
| DAO 改造 | 隐含 | v3 曾显式前置；v3.2 后不再作为本 spec 硬依赖，事件关联改用 `invocation_id` |
| 文件拆分 | 未考虑 1000 行限制 | 显式前置为 Phase 0 |
| Case 3（oversized input） | 阶段 2 硬目标 | 拆出，本 spec 仅在 `transform` 字段预留 |
| Fallback (`sliding_window` / `tool_truncation`) | 删除 | 保留，先埋点 |
| Prompt 形态（`<turn_attachments>` 拆分） | 阶段 2 直接落地 | 阶段 2 完成后做 offline A/B，通过再启用 |
| Restore 路径 | 单一新算法 | v0/v1 schema-aware 分流 |
| Sink 错误处理 | 未规定 | user_turn_context fail-fast；history_checkpoint best-effort |
| 不变量校验 | 文档级 | dataclass `__post_init__` + `from_sources` 运行时校验 |
| `UserContextSnapshot` 类型 | 存在 | 删除 |

---

## 1. 背景与问题

当前项目里"provider-facing user context"的组装逻辑分散在 6 个位置，命名与边界混乱：

| 位置 | 现状职责 |
|------|---------|
| [matmaster/core/context_builder.py](../../matmaster/core/context_builder.py) | 混合 system prompt 装配、user request 装配、compact bundle 装配三种职责 |
| [matmaster/core/context_compactor.py](../../matmaster/core/context_compactor.py) | 压缩算法 + 手写 tag 字符串 + checkpoint 边界 |
| [matmaster/manifests/](../../matmaster/manifests/) | 名为 manifests，实际在做"从 events 重建 provider-facing context sections" |
| [matmaster/types/current_input.py](../../matmaster/types/current_input.py) | 当前轮输入的 dataclass，里面写 `<current_instruction>` 标签 |
| [matmaster/types/context.py](../../matmaster/types/context.py) | `PlaygroundContext`，占用了 `context` 这个名字但实际是 playground 运行环境快照 |
| [matmaster/core/agent.py:337-347](../../matmaster/core/agent.py) | kernel 入口处直接拼字符串、构造 UserMessage |

更严重的是，**raw transcript history**（前端回放需要）与 **provider-facing history**（后端续跑、压缩恢复、prompt cache 需要）这两个不同语义的历史，被混在同一份 `User/query.content` 字段里推导，导致：

- UI 想看到原始用户输入；
- backend 想从同一条记录恢复 provider-facing `UserMessage`；
- 但 provider-facing `UserMessage` 已经被系统加了 user instructions、available attachments、compacted summary、current instruction、active tools 等内容；
- 压缩后真实 provider-facing 上下文已经不是原始对话事件的简单回放。

本次重构的目标是把这套混乱的数据流提升为一套**前端回放 / 后端续跑 / 压缩恢复 / prompt cache 四个用例统一的数据模型**。

---

## 2. 核心不变量

```
1. User/query 永远只保存用户原始输入（user_text + files + images + workspace_paths），
   服务前端回放和审计。不承载系统改造后的 LLM prompt。

2. user_turn_context 是一个真实用户 turn 对应的 provider-facing UserMessage 的事实记录。
   每个 session_id + spawn_id + invocation_id 对应**最多一条** user_turn_context。
   不是「每次 LLM 调用前快照」，工具循环内不再写入新事件。

3. history_checkpoint.base_messages 保存压缩生成的 anchor user message。
   该 anchor 只包含 provider-facing user context（不含 SystemMessage）。
   SystemMessage 由 kernel 在恢复时用 spec.system_prompt 重新构造；
   system prompt 不在 checkpoint 中冻结。

4. History restore 分流：
   - frontend display restore: 从 raw transcript (User/query, response, tool events)
     消费现有 ChatHistoryConverter 即可，不引入新路径。
   - backend model restore: schema-aware 分流。
     - v1 checkpoint 存在: checkpoint.base_messages
                          + 后续 user_turn_context + assistant_state
                          + response/run_result + tool_result
     - 无 v1 检查点: 沿用 ChatHistoryConverter.events_to_dialog_messages
```

这四条是本次重构的**硬约束**。所有后续设计决策都要回到这四条来验证。

---

## 3. 事件模型（核心）

本节是 v3 的支柱章节。新模型只引入**一种新事件**，并扩展 `history_checkpoint` payload。

### 3.1 事件清单

| 事件 | 来源 | 频率 | 用途 |
|------|------|------|------|
| `User/query` | API/stream 层 | 每个真实用户请求一条 | 前端回放、审计 |
| `user_turn_context` | service 层（kernel 调用前） | 每个真实用户请求**最多**一条 | backend model restore |
| `assistant_state` | kernel（有 tool_calls 时） | 每次 tool-call 轮一条 | model restore（assistant 侧） |
| `response` / `run_result` | kernel（自然结束时） | 每次自然结束一条 | model restore（assistant 侧）+ 前端回放 |
| `tool_result` | tool runner | 每次 tool 调用一条 | model restore + 前端回放 |
| `history_checkpoint` | compactor（扩展 payload） | 压缩触发时 | model restore 的重启锚点 |

**注意**：`user_turn_context` 写入在 kernel `run_stream` 之前（service 层负责），不在 kernel 内部。kernel 不感知该事件。

### 3.2 `user_turn_context` payload

```json
{
  "schema_version": "user_turn_context.v1",
  "kind": "anchor",
  "message": {
    "role": "user",
    "content": "<user_instructions>...</user_instructions>\n\n<current_instruction>...</current_instruction>",
    "images": [{"url": "...", "mime_type": "image/png", "detail": "auto"}]
  },
  "user_instructions_hash": "sha256:abcdef...",
  "transform": "raw",
  "render_version": "user_context_render.v1"
}
```

字段说明：

- `kind`: `"anchor"` | `"continuation"`
  - `anchor`：装配了完整长尾 sections（UserInstructions + SessionContext sections + TurnInput）。session 首轮 + AGENT.md hash 变化的轮，都生成 anchor。
  - `continuation`：只装配 TurnInput。后续未触发 hash 变化的轮。
- `message`: `UserMessage.model_dump(mode="json")`，含 content + images 全部字段。多模态附件必须完整保留。
- `user_instructions_hash`: AGENT.md 文本的 sha256 hash。anchor 时必填；continuation 时可选（continuation 隐含与最近 anchor 同 hash）。
- `transform`: `"raw"` | `"preflight_compacted"` | `"oversized_summary"`
  - `raw`: 当前轮没触发 preflight，message 是普通装配产物
  - `preflight_compacted`: 当前轮触发了 preflight compaction，message 是压缩后的 runtime user message
  - `oversized_summary`: 当前轮走 oversized 输入路径（Case 3）。**本 spec 阶段不实现，仅预留字段**。
- `schema_version`/`render_version`: 见 §6.6 的版本号策略。

关联说明：`user_turn_context` 与对应的 `User/query` 不在 payload 内保存 DB 行 id。二者通过 events 表顶层 metadata 关联：`session_id + spawn_id + invocation_id`。本 spec 中 `invocation_id` 是一次真实用户请求的稳定标识，不是一次 LLM API call 的标识。

### 3.3 `history_checkpoint` payload v1 扩展

```json
{
  "schema_version": "history_checkpoint.v1",
  "render_version": "user_context_render.v1",
  "covered_until_event_id": 123,
  "base_messages": [
    {"role": "user",
     "content": "<user_instructions>...</user_instructions>\n\n<compacted_history>...</compacted_history>\n\n<session_skills>...</session_skills>\n\n<session_tools>...</session_tools>\n\n<session_attachments>...</session_attachments>",
     "images": []}
  ],
  "reason": "summary",
  "user_instructions_text": "...",
  "user_instructions_hash": "sha256:..."
}
```

新增字段：
- `schema_version` / `render_version`
- `user_instructions_text`: 压缩当时 service 层读到的 AGENT.md 文本
- `user_instructions_hash`: 同上的 hash

旧字段 (`covered_until_event_id` / `base_messages` / `reason`) 保留语义不变。

**`covered_until_event_id` 语义**：checkpoint 等价于「从 session 起点重放到该 event_id 为止的所有 provider-facing 消息」。具体到本 spec：

- 普通 runtime compaction：`covered_until_event_id` 指向当前事件流末尾（含 assistant_state / tool_result，**不含**尚未写入的下一轮 user_turn_context）。由 caller (kernel) 显式提供该 id（kernel 自己写过这些事件、持有 high-water id）；assembler 不查 DB 派生。
- Preflight compaction（运行时触发，对应 `transform=preflight_compacted` 的 user_turn_context）：`covered_until_event_id` 指向 `TurnInput.pre_turn_history_event_id`，**不包含**当前轮的 User/query 和 user_turn_context；checkpoint 之后会有对应的 user_turn_context 事件被恢复追加。

**v1 checkpoint 不允许 null**：写入时 `covered_until_event_id` 必须是确定的事件 id（`int`，可以为 `0` 表示 session 起点）。若 compactor 无法拿到确定边界（理论上不应出现：runtime 由 caller 显式提供，preflight 从 `turn_input` 派生），则不应落 durable v1 checkpoint。restore 端遇到 null `covered_until_event_id` 视为 checkpoint 损坏，回退到 legacy restore 路径（`COMPAT:v0-restore`）。

### 3.4 写入时序

每个真实用户请求的事件序列：

```
普通延续轮（hash 未变）：
  User/query (invocation_id=I) → user_turn_context(kind=continuation, invocation_id=I) → kernel.run_stream → [assistant_state | response/run_result + tool_result]*

AGENT.md 改动后第一轮：
  User/query (invocation_id=I) → user_turn_context(kind=anchor, invocation_id=I, user_instructions_hash=NEW) → kernel.run_stream → ...

Session 首轮：
  User/query (invocation_id=I) → user_turn_context(kind=anchor, invocation_id=I, user_instructions_hash=...) → kernel.run_stream → ...

运行中触发 preflight compaction：
  User/query (invocation_id=I) → history_checkpoint (covered_until_event_id < User/query.event_id) → user_turn_context(kind=anchor, transform=preflight_compacted, invocation_id=I) → kernel.run_stream → ...

运行中触发 runtime compaction（无新用户输入，kernel 工具循环内）：
  ... → history_checkpoint (covered_until_event_id = kernel 持有的当前事件流 high-water id) → (kernel 继续 LLM 调用) → assistant_state/response/tool_result ...
```

注：v3.2 起 `user_turn_context` 与对应 `User/query` 的关联**不**通过 payload 内的外键，而是 events 表顶层 `session_id + spawn_id + invocation_id` 匹配。上面图示中 `invocation_id=I` 是 events 表 metadata 字段，不是 payload 字段。

**关键约束**：tool 循环内不再写任何 `user_turn_context` —— v2 的"每次 LLM 调用前一帧"语义彻底废止。

### 3.5 SSE replay 与 live handler 改造

新增事件 `user_turn_context` 必须同时加到两个过滤器：

- 历史回放：[stream_service.py:_should_emit_event_to_sse](../../src/services/stream_service.py:66) 加 `user_turn_context` 到 hidden list（与现有的 `assistant_state` / `skill_hit` / checkpoint events 一起）。
- 实时流：现有 `matmaster.integration.event_router.SSEHandler._should_skip()` 同步加。

`display_history_restore_service.py` 不建 stub。前端 replay 现状是 `generate_subscribe_stream → get_session_events + filter`，本次不改这条路径，只更新 filter 即可。

### 3.6 Sink 错误处理（fail-fast vs best-effort）

| 写入 | 失败策略 | 理由 |
|------|----------|------|
| `User/query` 写入失败 | fail-fast | API 层已有处理，本次不动 |
| `user_turn_context` 写入失败 | **fail-fast，本轮终止** | 是 model restore 的权威输入；继续跑 LLM 会制造未来无法正确恢复的会话 |
| `history_checkpoint` 写入失败 | **best-effort，记录 failure_reason** | 失败后最多从更老 checkpoint 或 raw events 重放，不影响本轮 LLM 调用 |
| `assistant_state` 写入失败 | best-effort，log | 同上 |

`CompactionResult.failure_reason` 字段保留，专门记录 checkpoint sink 错误信息。

---

## 4. 硬约束清单

放在显眼位置，所有 reviewer 与实现者必读。

### 4.1 事件与编排不变量（v3 保留）

1. 每个 `session_id + spawn_id + invocation_id` 在 events 表中对应**最多一条** `user_turn_context` 事件。多于一条是 bug，不是 dedup 常态。Phase 1 仅 root spawn 写入 `user_turn_context`，因此实际唯一性检查可先按 `session_id + invocation_id + spawn_id IS NULL` 落地。
2. `user_turn_context` 写入失败时，本轮 fail-fast；不允许继续 LLM 调用。
3. `history_checkpoint` 写入失败时，本轮可继续；compaction 路径必须记录 `failure_reason`。
4. 前端 SSE 回放与实时流永远不发 `user_turn_context` / `assistant_state` / `history_checkpoint`。
5. `invocation_id` 明确为一次用户请求的标识，**不是**一次 LLM API call 的标识。
6. `spawn_id` 在本次重构中只保持现有 root/child 过滤语义，不扩展 child checkpoint 语义。
7. AGENT.md 读取设置 size cap（建议 **50KB**），超限走 truncate + warning（不 fail-fast，保持 UX）。
8. `schema_version` 决定 payload codec，`render_version` 决定 message content 的解释方式；restore 优先按 schema 分发，不重新渲染历史 sections。
9. ContextSection 的 view 不变量 `RUNTIME ⊇ CHECKPOINT` 必须在 dataclass `__post_init__` 中校验。
10. `UserTurnContext.from_sources` 必须校验 section `key` 唯一性，冲突时 raise。
11. 渲染层 `wrap_tag` 必须对用户可控内容做最小 escape，防止 `</tag>` 注入破坏 section 边界（具体见 §6.4）。

### 4.2 装配分层不变量（v3.1 新增）

以下 12 条原则定义 `matmaster/context/` 与 `src/services/` 的硬边界，**任一违反都需要先改 spec 再改代码**。

1. 生产代码不得直接手写 `UserTurnContext.from_sources(...)`；唯一例外是 `ContextComposition.apply()`。
2. 生产代码不得直接组合多个 `Source(...)` 实例并拼装；唯一例外是 `matmaster/context/compositions.py` 内的 step 函数与 source 单测。
3. `agent_run_service` 不得知道 anchor / continuation 分别包含哪些 source。
4. `ContextCompactor` 不得知道 compacted context 包含哪些 source。
5. `ContextAssembler` 不负责 anchor / continuation 判定；它只执行 caller 给定的 `ContextAssemblyIntent`。
6. `ContextAssembler` 不读取 latest anchor hash，不处理迁移兼容策略，不写 events。
7. 同一 turn 内，intent 判定使用的 `UserInstructions` 必须原样传给 assembler，**禁止二次读取 AGENT.md**（防止 hash 与文本竞态）。
8. `ContextCompositionInputs` 是 `compositions.py` 内部类型，不作为 service / compactor 的公共调用接口。
9. Port 返回 typed data carrier / event sequence，**不返回**平台 service 对象，**不使用** `Any` / `dict[str, Any]`，也**不返回**核心装配产物：`ContextSection`、`UserMessage`、`UserTurnContext`。装配规则属于 `matmaster/context/`。
10. Event payload 作为存储边界只能用受限的 `JsonObject` 类型别名表达（`JsonValue = str | int | float | bool | None | tuple[JsonValue, ...] | Mapping[str, JsonValue]`），解析与装配规则属于 `matmaster/context/`。
11. Optional port（如 `SessionJobsPort | None`）只表示该 section 能力不可用；assembler 不判断 Bohrium 等具体产品功能是否启用。
12. `SessionContextBuilder` 属于 `matmaster/context/session.py`，负责从 `SessionEvent` 序列装配 session-level sections。port 实现层**不得**返回已装配的 sections。

---

## 5. 模块边界与目录结构

### 5.1 `matmaster/context/` — 新模块（本次重构的主体）

```
matmaster/context/
  __init__.py
  sections.py              # ContextSection, ContextView, SectionOrder
  turn_context.py          # UserTurnContext 聚合根（from_sources 仅 ContextComposition.apply 调用）
  rendering.py             # wrap_tag (含 escape), render_sections
  system_prompt.py         # 原 ContextBuilder.build_system_prompt
  compaction.py            # 原 core/context_compactor.py（保留 fallback；不再自己拼 source）
  session.py               # SessionContextBuilder：从 SessionEvent 序列装配 session-level sections
  history_restore.py       # ModelHistoryRestorer (DI 注入 events 访问)
  scanner.py               # 从 manifests/scanner.py 迁移，底层 events 扫描工具

  # v3.1 新增三件 ── 装配标准化
  ports.py                 # UserInstructions / SessionEvent / SessionEventQuery /
                           # JsonObject 等 typed 数据载体，以及对应 Port Protocol
  compositions.py               # ContextCompositionInputs / ContextComposition / ANCHOR / CONTINUATION /
                           # COMPACTED 三个常量 + step 函数
  assembly.py              # ContextAssemblyIntent / TurnAssemblyRequest /
                           # CompactionAssemblyRequest / AssemblyResult /
                           # ContextAssembler
  turn_intent.py           # 纯函数 decide_turn_context_intent(...)（不读 events、不知迁移兼容策略）

  sources/
    __init__.py
    user_instructions.py
    turn_input.py         # TurnInstructionSource / TurnAttachmentsSource / TurnInput
    compacted_history.py
    attachments.py
    skills.py
    tools.py               # active tools（替代 mcp.py，第一阶段保留 mcp.py shim）
    session_jobs.py        # 占位，留待 bohrium job table 系统建好
    workspace.py           # 占位
    artifacts.py           # 占位
```

**v3 不包含 `snapshot.py`**。`UserContextSnapshot` 类型废弃，事件落到 events 表的 `user_turn_context` 直接序列化 UserMessage。

平台侧新增：

```
src/services/
  context_assembly_ports.py    # AppUserInstructionsPort / AppSessionEventsPort / AppSessionJobsPort
                               # 实现 matmaster/context/ports.py 声明的 Protocol
  context_turn_intent.py       # resolve_turn_context_intent(events_port, ...)
                               # 做 events 查询；内部委托 turn_intent 纯函数
```

### 5.2 `matmaster/core/` 收缩后

只保留"运行时执行 + 编排"职责：

```
matmaster/core/
  agent.py                 # AgentKernel
  tool_runner.py
  tool_scheduler.py
  hooks.py
  exp.py
  playground.py            # 已有文件，本次把 PlaygroundContext / WorkspaceArchivalConfig 收回（不是新建）
  kernel_items.py
  finish_diagnostics.py
  capability_policy.py
  structural_validation.py
  stream_drain.py
  config_loader.py
```

**`core/` 不再 import `context_builder` / `context_compactor`**，统一从 `matmaster.context` 进入。

### 5.3 删除 / 迁移清单

| 旧路径 | 处理 |
|--------|------|
| `matmaster/types/context.py` | 阶段 3 删除。`PlaygroundContext` / `WorkspaceArchivalConfig` 迁入**已有的** `core/playground.py`（注意：v2 误标"新建"，实际为已有文件，需处理反向 import 循环）。阶段 1-2 保留 shim re-export |
| `matmaster/types/current_input.py` | 迁到 `context/sources/turn_input.py`，类型一并重命名 |
| `matmaster/manifests/` | 整目录重写为 `matmaster/context/` 内部模块 |
| `matmaster/core/context_builder.py` | 拆三段（见 §13）|
| `matmaster/core/context_compactor.py` | 迁到 `context/compaction.py`，**保留 fallback 路径**；装配走 `ContextAssembler`（v3.1） |
| `src/services/history_restore_service.py` | 改名 `model_history_restore_service.py`，内部委托 `matmaster/context/history_restore.py` 的 schema-aware 分流。**不**新建 `display_history_restore_service.py`（见 §3.5）|
| `src/services/agent_run_service.py` 的 `_load_user_instructions` helper（v3 §7.3 给的私有函数） | 删除。等价能力迁入 `src/services/context_assembly_ports.py` 的 `AppUserInstructionsPort.load_user_instructions`。AGENT.md 路径约定、size cap 与 hash 计算都属于 port 实现（v3.1） |

---

## 6. 核心类型

### 6.1 `ContextView`

```python
# matmaster/context/sections.py
from __future__ import annotations
from enum import Enum

class ContextView(str, Enum):
    """同一组 sections 投影到 user message 时的视图选择。

    用于 *渲染时机*。一旦渲染产出 UserMessage 字符串，
    结果立即冻结（写入 user_turn_context 事件或 history_checkpoint），
    任何后续恢复都不得依赖 view 重渲染。

    不变量: RUNTIME ⊇ CHECKPOINT。任何在 CHECKPOINT 视图中出现的 section
    必然也在 RUNTIME 视图中出现。
    """
    RUNTIME = "runtime"        # 下一轮 LLM 调用要看到的完整内容（含本轮 TurnInput）
    CHECKPOINT = "checkpoint"  # 写 checkpoint 时的视图（剥离本轮 TurnInput）
```

### 6.2 `ContextSection`（含 `__post_init__` 不变量校验）

```python
# matmaster/context/sections.py
from dataclasses import dataclass
from enum import IntEnum


class SectionOrder(IntEnum):
    USER_INSTRUCTIONS = 10
    COMPACTED_HISTORY = 100
    SESSION_SKILLS = 300
    SESSION_TOOLS = 400
    SESSION_ATTACHMENTS = 500
    SESSION_WORKSPACE = 600
    SESSION_ARTIFACTS = 700
    TURN_INSTRUCTION = 1000        # 普通轮：instruction 在前
    TURN_ATTACHMENTS = 1100
    SESSION_JOBS = 1200
    TURN_INSTRUCTION_LAST = 1300   # 压缩后：instruction 移到末尾，利用 recency bias


@dataclass(frozen=True)
class ContextSection:
    key: str
    tag: str
    content: str
    order: int
    views: frozenset[ContextView]

    def __post_init__(self):
        if ContextView.CHECKPOINT in self.views and ContextView.RUNTIME not in self.views:
            raise ValueError(
                f"Section {self.key!r}: CHECKPOINT view requires RUNTIME view "
                f"(invariant RUNTIME ⊇ CHECKPOINT)"
            )
        if not self.key:
            raise ValueError("ContextSection.key must be non-empty")
        if not self.tag:
            raise ValueError("ContextSection.tag must be non-empty")
```

### 6.3 `UserTurnContext`（含 key 唯一性校验）

```python
# matmaster/context/turn_context.py
from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView
from matmaster.context.rendering import render_sections
from matmaster.types.messages import ImageContentPart, UserMessage


@dataclass(frozen=True)
class UserTurnContext:
    """用户侧 provider-facing 上下文的聚合根。

    组合若干 ContextSection,最终投影为 provider-facing
    matmaster.types.messages.UserMessage。
    """

    sections: tuple[ContextSection, ...]
    images: tuple[ImageContentPart, ...] = ()

    @classmethod
    def from_sources(
        cls,
        *section_groups: Iterable[ContextSection],
        images: Iterable[ImageContentPart] = (),
    ) -> "UserTurnContext":
        merged: list[ContextSection] = []
        seen_keys: set[str] = set()
        for group in section_groups:
            for section in group:
                if section.key in seen_keys:
                    raise ValueError(
                        f"Duplicate section key {section.key!r} in UserTurnContext "
                        f"sources. Keys must be unique across all sources."
                    )
                seen_keys.add(section.key)
                merged.append(section)
        return cls(sections=tuple(merged), images=tuple(images))

    def render(self, view: ContextView) -> str:
        return render_sections(self.sections, view=view)

    def to_message(self, view: ContextView) -> UserMessage:
        return UserMessage(content=self.render(view), images=list(self.images))
```

### 6.4 `rendering.py`（含 tag escape）

```python
# matmaster/context/rendering.py
from __future__ import annotations
from collections.abc import Iterable

from matmaster.context.sections import ContextSection, ContextView


def _escape_close_tag(content: str, tag: str) -> str:
    """防止用户可控内容含 </tag> 关闭当前 section,破坏 prompt 边界。

    这是 prompt convention 防御,不是安全边界。policy:
    - 把 </tag> 替换为 </ tag> 形式(中间加空格)。
    - LLM 仍能理解原意,但不会破坏外层 tag 结构。

    若发现此 escape 触发(content 中含字面 </tag>),log warning,
    便于运营定位被 escape 的 prompt 注入或意外。
    """
    close = f"</{tag}>"
    if close in content:
        import logging
        logging.getLogger(__name__).warning(
            "rendering._escape_close_tag triggered: tag=%r content contains close form, "
            "escaping to avoid breaking section boundary",
            tag,
        )
        content = content.replace(close, f"</ {tag}>")
    return content


def wrap_tag(tag: str, content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    text = _escape_close_tag(text, tag)
    return f"<{tag}>\n{text}\n</{tag}>"


def render_sections(
    sections: Iterable[ContextSection],
    *,
    view: ContextView,
    separator: str = "\n\n",
) -> str:
    visible = [s for s in sections if view in s.views and s.content.strip()]
    visible.sort(key=lambda s: s.order)
    return separator.join(wrap_tag(s.tag, s.content) for s in visible)
```

`rendering.py` 是**唯一**知道 tag 怎么写的地方。

### 6.5 Prompt 形态决策（v3 改动）

v2 把当前 `<current_instruction>` 含 `user_text + [Current attachments]` 拆为 `<turn_attachments>` + `<current_instruction>` 两个顶级 XML 块。这是 prompt 形态变化，有 quality regression 风险。

v3 决策：
- **Phase 1/2 期间保留现状 prompt 形态**（即沿用 `<current_instruction>` 含 attachments 列表的单 block）
- **Phase 3 前**做 offline A/B：
  - A: 现状 `<current_instruction>` 内含 user_text + `[Current attachments]`
  - B: 拆分版 `<turn_attachments>` + `<current_instruction>`
- 评估维度：
  - 是否正确引用本轮附件
  - 是否正确选择 tool
  - 是否把附件当作任务而不是背景
  - 多图片输入是否还能稳定进入 provider
- A/B 通过再切换；不通过则 spec 中关于 `<turn_attachments>` 的设计可选放弃或调整 tag 名

为支持兼容，`TurnAttachmentsSource` 在 sources 中作为独立类型存在（见 §7.3），但默认渲染合并到 `<current_instruction>`，由一个 feature flag 控制是否拆分。flag 默认关闭。

注意：`<session_jobs>` 从 Phase 1 起即作为独立顶级 XML 块存在，不受上述 A/B 测试和 feature flag 影响。它始终以 `SessionJobsSource` 的形式按 `SectionOrder.SESSION_JOBS`（1200）排序，每轮附加在 user context 中。

### 6.6 `schema_version` / `render_version` 演化策略

两个版本号独立演化：

- `schema_version`：仅当 event payload 字段结构改变时升级
  - 新增字段（向下兼容）：minor 升级，旧 codec 读时忽略新字段
  - 删字段或字段语义变更：major 升级，需明确旧 codec 处理路径
- `render_version`：仅当 user message content 的渲染算法变化时升级（tag 名、tag 顺序、separator、escape 规则）

恢复路径：

1. 先看 `schema_version`，决定 payload 反序列化 codec
2. 再看 `render_version`，决定 content 字符串的语义（如果需要解析）
3. **不**重新渲染历史。content 字符串始终被当作权威字节使用

(schema_version, render_version) 不匹配时（例如 schema v2 配 render v1），按各自版本独立处理；不允许混合 codec。

---

## 6bis. Composition 装配层（v3.1 新增）

v3.1 引入。Composition 把 "在某场景下用哪些 source" 的决策从 service 与 compactor 收回到 `matmaster/context/compositions.py`，与 §7bis 的 `ContextAssembler` 配合使用。

### 6bis.1 定位

`UserTurnContext.from_sources(...)`（v3 §6.3）继续存在，但**降级为底层机械合并器**。生产代码不再直接调用它（硬约束 §4.2 #1）。composition 是唯一调用方。

层次：

```
caller (service / compactor)
        |
        v   intent + typed request
[ContextAssembler.assemble_turn / assemble_compaction]  (§7bis)
        |   1. 调 ports 拉数据 (UserInstructions / SessionEvent[] / SessionJobs)
        |   2. 调 SessionContextBuilder 装配 session-level sections
        |   3. 构造 ContextCompositionInputs
        |   4. composition.apply(inputs) -> UserTurnContext
        v
[ContextComposition.apply]
        |   按 step 顺序收集 sections, 调 UserTurnContext.from_sources
        v
[UserTurnContext] -> caller 自己 to_message(view) 取 RUNTIME / CHECKPOINT
```

### 6bis.2 `ContextCompositionInputs`（composition 内部输入类型）

```python
# matmaster/context/compositions.py
from __future__ import annotations
from dataclasses import dataclass, field

from matmaster.context.sections import ContextSection
from matmaster.context.ports import SessionJobs
from matmaster.context.sources.attachments import SessionAttachmentsSource
from matmaster.context.sources.turn_input import TurnInput


@dataclass(frozen=True)
class ContextCompositionInputs:
    """Composition step 函数消费的纯数据载体, 不持有 builder / service / port 对象。

    硬约束 #8: 不是 service / compactor 的公共调用接口, 由 ContextAssembler 内部构造。
    """
    user_instructions_text: str = ""
    compacted_history_summary: str = ""
    turn_input: TurnInput | None = None
    session_sections: tuple[ContextSection, ...] = ()   # 由 SessionContextBuilder 预装配
    session_jobs: SessionJobs = field(default_factory=SessionJobs.empty)
    session_attachments_override: SessionAttachmentsSource | None = None
    defer_turn_instruction: bool = False
```

**关键变化（相对早期讨论稿）**：

- 去掉 `session_builder` / `job_ledger`（composition 不持有 builder/service 对象）
- 去掉 `pre_turn_history_event_id` / `include_session_attachments`（这些是 assembler 调 `SessionContextBuilder.build_sections` 时的参数，不是 composition concern）
- `session_sections` 接收已装配 sections（由核心模块的 `SessionContextBuilder` 生成，符合硬约束 §4.2 #12）
- `session_jobs` 是 typed `SessionJobs` 而非裸 `object`（符合硬约束 §4.2 #9）

### 6bis.3 `ContextComposition`

```python
from collections.abc import Callable

CompositionStep = Callable[[ContextCompositionInputs], tuple[ContextSection, ...]]


@dataclass(frozen=True)
class ContextComposition:
    """声明 "在某场景下,该用哪些 source 装配 user_turn_context"。

    顺序: step 元组的执行顺序只决定 ContextSection 在 sections tuple 中的出生顺序。
    最终渲染顺序由 SectionOrder enum 在 render_sections 内部 sort 决定。
    改 step 顺序不影响最终 prompt 文本; 改 SectionOrder 才影响。
    """
    name: str
    steps: tuple[CompositionStep, ...]

    def apply(self, inputs: ContextCompositionInputs) -> UserTurnContext:
        section_groups = tuple(step(inputs) for step in self.steps)
        images = ()
        if inputs.turn_input is not None:
            images = inputs.turn_input.attachments.images_as_parts()
        return UserTurnContext.from_sources(*section_groups, images=images)
```

### 6bis.4 Step 函数（composition 内部，文件私有）

```python
import dataclasses
from matmaster.context.sources.user_instructions import UserInstructionsSource
from matmaster.context.sources.compacted_history import CompactedHistorySource
from matmaster.context.sources.session_jobs import SessionJobsSource


def _step_user_instructions(inp: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    return UserInstructionsSource(text=inp.user_instructions_text).to_sections()


def _step_compacted_history(inp: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    return CompactedHistorySource(summary=inp.compacted_history_summary).to_sections()


def _step_session_sections(inp: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    return inp.session_sections   # 已由 SessionContextBuilder 装配


def _step_session_attachments_override(inp: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    if inp.session_attachments_override is None:
        return ()
    return inp.session_attachments_override.to_sections()


def _step_turn_input(inp: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    if inp.turn_input is None:
        return ()
    ti = inp.turn_input
    if inp.defer_turn_instruction:
        ti = dataclasses.replace(
            ti,
            instruction=dataclasses.replace(ti.instruction, deferred=True),
        )
    return ti.to_sections()


def _step_session_jobs(inp: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    return SessionJobsSource.from_jobs(inp.session_jobs).to_sections()
```

`_step_session_jobs` 不再检查 `is None`：assembler 在 port 缺失时传入 `SessionJobs.empty()`，`SessionJobsSource.from_jobs` 对空 `SessionJobs` 自然返回空 sections。

### 6bis.5 三个 composition 常量

```python
ANCHOR_COMPOSITION = ContextComposition(
    name="anchor",
    steps=(
        _step_user_instructions,
        _step_session_sections,
        _step_turn_input,
        _step_session_jobs,
    ),
)

CONTINUATION_COMPOSITION = ContextComposition(
    name="continuation",
    steps=(
        _step_turn_input,
        _step_session_jobs,
    ),
)

COMPACTED_COMPOSITION = ContextComposition(
    name="compacted",
    steps=(
        _step_user_instructions,
        _step_compacted_history,
        _step_session_attachments_override,
        _step_session_sections,
        _step_turn_input,
        _step_session_jobs,
    ),
)
```

**关键性质**：

- 三个常量是项目里**唯一**声明 "该用哪些 source" 的地方
- 添加新场景（sub-agent handoff、oversized input、…）= 添加一个新 `XXX_COMPOSITION = ContextComposition(...)`，**不改** `apply()` 也**不改** step 函数
- 添加新 source = 在 `sources/` 加 source、在 `compositions.py` 加 step 函数、把 step 写进相应 composition；**可能**需要扩 `ContextCompositionInputs` 字段

### 6bis.6 与 SectionOrder 的关系

step 元组顺序只决定 sections 进入 `UserTurnContext.from_sources` 的参数顺序。最终 prompt 中 section 排布由 `SectionOrder` enum 与 `render_sections` 内部的 sort 决定（v3 §6.4 / §7.2）。

这一解耦意味着：

- 调整 prompt 顺序（如 Phase 3 的 `<turn_attachments>` A/B）只改 `SectionOrder`，不改 composition
- 调整 "哪些 section 出现"（如 sub-agent handoff 需要新 source）只改 composition，不改 SectionOrder

---

## 7. Source 接口契约与清单

### 7.1 接口

每个 source 是 frozen dataclass，只暴露：

```python
class ContextSource(Protocol):
    def to_sections(self) -> tuple[ContextSection, ...]: ...
```

空内容返回空 tuple。Sources 之间**完全独立**，构造时只接外部数据源（events / 文件 / API / DI loader），互相不 import。

### 7.2 全局 order 与视图分布

| Source | 文件 | order | 视图 | 备注 |
|--------|------|-------|------|------|
| `UserInstructionsSource` | `sources/user_instructions.py` | `SectionOrder.USER_INSTRUCTIONS` (10) | RUNTIME + CHECKPOINT | 通过 DI 注入 AGENT.md 文本 |
| `CompactedHistorySource` | `sources/compacted_history.py` | `SectionOrder.COMPACTED_HISTORY` (100) | RUNTIME + CHECKPOINT | summary LLM 产物 |
| `SessionJobsSource` | `sources/session_jobs.py` | `SectionOrder.SESSION_JOBS` (1200) | RUNTIME + CHECKPOINT | 每轮刷新，末尾附加；无活跃 job 时返回空 |
| `SessionSkillsSource` | `sources/skills.py` | `SectionOrder.SESSION_SKILLS` (300) | RUNTIME + CHECKPOINT | 从 events 重建 |
| `SessionToolsSource` | `sources/tools.py` | `SectionOrder.SESSION_TOOLS` (400) | RUNTIME + CHECKPOINT | provider-facing 工具集 |
| `SessionAttachmentsSource` | `sources/attachments.py` | `SectionOrder.SESSION_ATTACHMENTS` (500) | RUNTIME + CHECKPOINT | 跨轮累积附件清单 |
| `SessionWorkspaceSource` | `sources/workspace.py` | `SectionOrder.SESSION_WORKSPACE` (600) | RUNTIME + CHECKPOINT | 占位 |
| `SessionArtifactsSource` | `sources/artifacts.py` | `SectionOrder.SESSION_ARTIFACTS` (700) | RUNTIME + CHECKPOINT | 占位 |
| `TurnInstructionSource` | `sources/turn_input.py` | `SectionOrder.TURN_INSTRUCTION` (1000) / `TURN_INSTRUCTION_LAST` (1300) | **RUNTIME only** | 本轮 user_text，`deferred=True` 时排到末尾 |
| `TurnAttachmentsSource` | `sources/turn_input.py` | `SectionOrder.TURN_ATTACHMENTS` (1100) | **RUNTIME only** | 本轮附件清单 |

CHECKPOINT 视图自动剥离 `TurnAttachmentsSource` 和 `TurnInstructionSource`。

### 7.3 关键 source 实现

#### `UserInstructionsSource`

```python
# matmaster/context/sources/user_instructions.py
from __future__ import annotations
from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})

# AGENT.md size cap (硬约束 #7)
USER_INSTRUCTIONS_MAX_BYTES = 50 * 1024  # 50KB


@dataclass(frozen=True)
class UserInstructionsSource:
    """工作空间级用户指令 (AGENT.md) 的 provider-facing 上下文 source。

    text 字段由 service 层通过 loader 注入。matmaster/ 不感知任何文件路径,
    不感知 .matmaster/AGENT.md 这个具体约定。size cap 由 service 层强制。
    """
    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.text.strip():
            return ()
        return (ContextSection(
            key="user_instructions",
            tag="user_instructions",
            content=self.text,
            order=SectionOrder.USER_INSTRUCTIONS,
            views=_VIEWS,
        ),)
```

**v3.1 变化**：v3 这一节原有的 service 私有 helper `_load_user_instructions` **已删除**。AGENT.md 路径约定、size cap、hash 计算 都属于平台 port 实现 `src/services/context_assembly_ports.py::AppUserInstructionsPort`（见 §7bis.4）。

`UserInstructionsSource` 自身保持纯净：只接受已加载的 `text`，把它装配成 section。`matmaster/context/` 仍然不感知 `.matmaster/AGENT.md` 路径，不感知 50KB 上限。

#### `turn_input.py`（v3 保留双 source 设计，但默认合并渲染）

```python
# matmaster/context/sources/turn_input.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlparse

from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.types.messages import ImageContentPart

_RUNTIME = frozenset({ContextView.RUNTIME})


def _display_name(value: str) -> str:
    parsed = urlparse(value)
    return PurePosixPath(parsed.path or value).name or value


@dataclass(frozen=True)
class TurnInstructionSource:
    """本轮用户文本指令。仅 RUNTIME 可见。"""
    user_text: str = ""
    deferred: bool = False  # True → 排到末尾（压缩后 recency bias）

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.user_text.strip():
            return ()
        order = (SectionOrder.TURN_INSTRUCTION_LAST
                 if self.deferred
                 else SectionOrder.TURN_INSTRUCTION)
        return (ContextSection(
            key="current_instruction",
            tag="current_instruction",
            content=self.user_text.strip(),
            order=order,
            views=_RUNTIME,
        ),)


@dataclass(frozen=True)
class TurnAttachmentsSource:
    """本轮附带的文件 / 图片 / workspace 路径。仅 RUNTIME 可见。

    Phase 3 前的过渡期: 这里渲染出的 section 与 TurnInstructionSource 输出可以
    合并到同一个 <current_instruction> block。具体由 feature flag 控制
    (见 §6.5 的 A/B 决策)。
    """
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    workspace_paths: tuple[str, ...] = ()

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not (self.files or self.images or self.workspace_paths):
            return ()
        lines: list[str] = []
        for i, v in enumerate(self.files, 1):
            lines.append(f"file_{i} {_display_name(v)} {v}")
        for i, v in enumerate(self.workspace_paths, 1):
            lines.append(f"workspace_{i} {v}")
        for i, v in enumerate(self.images, 1):
            lines.append(f"image_{i} {_display_name(v)} {v}")
        return (ContextSection(
            key="turn_attachments",
            tag="turn_attachments",
            content="\n".join(lines),
            order=SectionOrder.TURN_ATTACHMENTS,
            views=_RUNTIME,
        ),)

    def images_as_parts(self) -> tuple[ImageContentPart, ...]:
        return tuple(ImageContentPart(url=u) for u in self.images)


@dataclass(frozen=True)
class TurnInput:
    """本轮请求的原子单元。

    pre_turn_history_event_id 语义 (v3.2 收紧):
    - 类型 int (非 Optional), service 层必须提供具体值
    - 0 = 本轮 User/query 写入前 session 内无任何 event (session 首轮)
    - N>0 = 本轮 User/query 写入前最后一个 event 的 id; 历史视图查询应当返回 event_id <= N

    禁止用 None 同时表达 "无历史" 和 "无上界" - 后者在 SessionEventQuery 里独立表示。
    """
    instruction: TurnInstructionSource = field(default_factory=TurnInstructionSource)
    attachments: TurnAttachmentsSource = field(default_factory=TurnAttachmentsSource)
    pre_turn_history_event_id: int = 0

    def to_sections(self) -> tuple[ContextSection, ...]:
        return (*self.instruction.to_sections(), *self.attachments.to_sections())

    def has_effective_input(self) -> bool:
        return bool(
            self.instruction.user_text.strip()
            or self.attachments.files
            or self.attachments.images
            or self.attachments.workspace_paths
        )
```

#### `SessionContextBuilder`

与 v2 §5.4 等价，本节略。完整实现见原 v2 spec 第 5.4 节，关键点：
- 替代 `manifests/rehydrator.py` 的 `CompactionRehydrator`
- 删除 v2 标记的 unused `playground_ctx` 参数
- 暴露 `build_sections(until_event_id, include_attachments)` 方法支持 Case 3 旁路
- **v3.1**：构造参数从 `events: list[dict]` 改为 typed `events: tuple[SessionEvent, ...]`（SessionEvent 定义见 §7bis.2）。归属仍在 `matmaster/context/session.py`（硬约束 §4.2 #12）

---

## 7bis. Context Assembly Ports + Assembler（v3.1 新增）

v3.1 引入。本节定义"装配核心模块需要的平台数据入口"以及"装配执行器"。

### 7bis.1 设计原则（重申硬约束 §4.2 #9 / #10 / #11 / #12）

- Port 返回 typed data carrier / event sequence，**不返回**核心装配产物（`ContextSection` / `UserMessage` / `UserTurnContext`），**不返回**平台 service 对象，**不使用** `Any` / `dict[str, Any]`
- Event payload 作为存储边界用受限的 `JsonObject` 类型表达
- Optional port = "该 section 能力不可用"，不做产品功能开关判定
- 装配规则（events → sections）属于 `matmaster/context/`

### 7bis.2 类型定义

```python
# matmaster/context/ports.py
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


# ---- 受限 JSON 类型（硬约束 #10）----

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
JsonObject = Mapping[str, JsonValue]


# ---- user_instructions ----

@dataclass(frozen=True)
class UserInstructions:
    text: str
    hash: str            # "sha256:..."
    truncated: bool = False


class UserInstructionsPort(Protocol):
    async def load_user_instructions(
        self,
        workspace_root: Path,
    ) -> UserInstructions: ...


# ---- session events (typed JSON envelope) ----

@dataclass(frozen=True)
class SessionEvent:
    """DB events 行的 typed envelope。

    content 是 JsonObject 而非 dict[str, Any]; 装配规则属于 matmaster/context (硬约束 #10)。
    """
    id: int
    event_type: str
    source: str | None
    content: JsonObject
    task_id: str | None = None
    invocation_id: str | None = None
    spawn_id: str | None = None


@dataclass(frozen=True)
class SessionEventQuery:
    session_id: str
    spawn_id: str | None
    until_event_id: int | None = None
    event_types: tuple[str, ...] | None = None
    limit: int | None = None
    order: Literal["asc", "desc"] = "asc"


class SessionEventsPort(Protocol):
    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]: ...


# ---- session jobs (Optional)----

@dataclass(frozen=True)
class SessionJobs:
    """本 session 当前可见的 job 列表 (从 job-events 表按边界点查询得出)。

    占位实现, 实际字段在 bohrium job ledger 接入时确定。不是 "snapshot": jobs
    通过事件流持久化, 不存在 "拍照" 这种主动行为。
    """
    active_jobs: tuple[JsonObject, ...] = ()

    @classmethod
    def empty(cls) -> "SessionJobs":
        return cls(active_jobs=())


@dataclass(frozen=True)
class SessionJobsQuery:
    session_id: str


class SessionJobsPort(Protocol):
    async def load_session_jobs(
        self,
        query: SessionJobsQuery,
    ) -> SessionJobs: ...


# ---- assembly ports 组合 ----

@dataclass(frozen=True)
class ContextAssemblyPorts:
    """ContextAssembler 持有的窄能力组合。

    注意: user_instructions 不在此处。原因: service 在 intent 判定时已读一次 bundle,
    必须原样传给 assembler (硬约束 #7), 不允许 assembler 二次读取。
    """
    session_events: SessionEventsPort
    session_jobs: SessionJobsPort | None = None
```

### 7bis.3 Assembler API

```python
# matmaster/context/assembly.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEventQuery,
    SessionJobsQuery,
    SessionJobs,
    UserInstructions,
)
from matmaster.context.compositions import (
    ANCHOR_COMPOSITION, CONTINUATION_COMPOSITION, COMPACTED_COMPOSITION,
    ContextComposition, ContextCompositionInputs,
)
from matmaster.context.session import SessionContextBuilder
from matmaster.context.sources.attachments import SessionAttachmentsSource
from matmaster.context.sources.turn_input import TurnInput
from matmaster.context.turn_context import UserTurnContext


class ContextAssemblyIntent(str, Enum):
    ANCHOR_TURN = "anchor_turn"
    CONTINUATION_TURN = "continuation_turn"
    PREFLIGHT_COMPACTION = "preflight_compaction"
    RUNTIME_COMPACTION = "runtime_compaction"

    @property
    def is_anchor_turn(self) -> bool:
        return self == ContextAssemblyIntent.ANCHOR_TURN

    @property
    def is_compaction(self) -> bool:
        return self in {
            ContextAssemblyIntent.PREFLIGHT_COMPACTION,
            ContextAssemblyIntent.RUNTIME_COMPACTION,
        }


@dataclass(frozen=True)
class TurnAssemblyRequest:
    """普通 turn (anchor / continuation) 的装配 request。

    历史视图边界通过 turn_input.pre_turn_history_event_id 自动获取,
    不在 request 中重复携带 (避免与 turn_input 字段双写不一致)。
    """
    session_id: str
    spawn_id: str | None
    turn_input: TurnInput
    user_instructions: UserInstructions  # 硬约束 #7: UserInstructions 原样传入


@dataclass(frozen=True)
class CompactionAssemblyRequest:
    """Compaction 装配 request。

    compacted_history_summary 必填 (compactor 自己产生, 可能来自 LLM 或 fallback)。
    turn_input 在 runtime compaction (kernel 内循环) 时可 None。

    covered_until_event_id 按 intent 不同有不同要求 (assembler 校验):
    - PREFLIGHT_COMPACTION:
        可省略 (None); assembler 从 turn_input.pre_turn_history_event_id 派生.
        显式 int 时优先使用 (Case 3 oversized 等场景预留).
    - RUNTIME_COMPACTION:
        **必须**显式传入非 None int; caller (kernel) 提供当前事件流 high-water id.
        assembler **不**为 runtime 隐式派生 - 否则 checkpoint payload 写出 null 边界,
        restore 时无法判断覆盖范围, 会重复 replay 已被 summary 覆盖的事件.

    派生后的真实值会写入 AssemblyResult.covered_until_event_id (compaction 路径下保证非 None),
    caller (compactor) 从中取出写入 history_checkpoint.v1 payload (该 payload 字段不允许 null).
    """
    session_id: str
    spawn_id: str | None
    user_instructions: UserInstructions
    compacted_history_summary: str
    turn_input: TurnInput | None = None
    covered_until_event_id: int | None = None  # PREFLIGHT 可省略; RUNTIME 必填
    session_attachments_override: SessionAttachmentsSource | None = None


@dataclass(frozen=True)
class AssemblyResult:
    user_turn_context: UserTurnContext          # caller 自己 to_message(view)
    user_instructions_text: str                 # compactor 写 history_checkpoint payload 用
    user_instructions_hash: str                 # service 写 user_turn_context payload 用
    used_composition: str                       # 调试 / 埋点
    covered_until_event_id: int | None = None   # compaction 路径下 assembler 保证非 None; turn 路径下保持 None


_INTENT_COMPOSITION_MAP: dict[ContextAssemblyIntent, ContextComposition] = {
    ContextAssemblyIntent.ANCHOR_TURN: ANCHOR_COMPOSITION,
    ContextAssemblyIntent.CONTINUATION_TURN: CONTINUATION_COMPOSITION,
    ContextAssemblyIntent.PREFLIGHT_COMPACTION: COMPACTED_COMPOSITION,
    ContextAssemblyIntent.RUNTIME_COMPACTION: COMPACTED_COMPOSITION,
}


class ContextAssembler:
    def __init__(self, ports: ContextAssemblyPorts) -> None:
        self._ports = ports

    async def assemble_turn(
        self,
        intent: ContextAssemblyIntent,
        request: TurnAssemblyRequest,
    ) -> AssemblyResult:
        if intent not in {
            ContextAssemblyIntent.ANCHOR_TURN,
            ContextAssemblyIntent.CONTINUATION_TURN,
        }:
            raise ValueError(f"assemble_turn does not accept intent {intent!r}")

        composition = _INTENT_COMPOSITION_MAP[intent]
        session_sections: tuple = ()
        jobs = await self._load_jobs_or_empty(request.session_id)

        if intent == ContextAssemblyIntent.ANCHOR_TURN:
            history_boundary = request.turn_input.pre_turn_history_event_id
            events = await self._ports.session_events.load_events(SessionEventQuery(
                session_id=request.session_id,
                spawn_id=request.spawn_id,
                until_event_id=history_boundary,
                order="asc",
            ))
            session_sections = SessionContextBuilder(events=events).build_sections(
                until_event_id=history_boundary,
                include_attachments=True,
            )

        inputs = ContextCompositionInputs(
            user_instructions_text=request.user_instructions.text,
            turn_input=request.turn_input,
            session_sections=session_sections,
            session_jobs=jobs,
        )
        user_turn_context = composition.apply(inputs)

        return AssemblyResult(
            user_turn_context=user_turn_context,
            user_instructions_text=request.user_instructions.text,
            user_instructions_hash=request.user_instructions.hash,
            used_composition=composition.name,
        )

    async def assemble_compaction(
        self,
        intent: ContextAssemblyIntent,
        request: CompactionAssemblyRequest,
    ) -> AssemblyResult:
        if not intent.is_compaction:
            raise ValueError(f"assemble_compaction does not accept intent {intent!r}")

        composition = _INTENT_COMPOSITION_MAP[intent]
        # 派生 covered_until 按 intent 分流, 不允许隐式 None (会破坏 checkpoint restore):
        #   PREFLIGHT: 显式值 > turn_input.pre_turn_history_event_id (后者 0 即首轮空历史, 合法)
        #   RUNTIME: caller 必须显式传入当前事件流 high-water id (assembler 不查 DB 派生)
        if intent == ContextAssemblyIntent.RUNTIME_COMPACTION:
            if request.covered_until_event_id is None:
                raise ValueError(
                    "RUNTIME_COMPACTION requires explicit covered_until_event_id "
                    "(kernel must pass current event stream high-water mark); "
                    "implicit None would write a null boundary into history_checkpoint payload "
                    "and break restore coverage semantics."
                )
            covered_until = request.covered_until_event_id
        else:  # PREFLIGHT_COMPACTION
            if request.covered_until_event_id is not None:
                covered_until = request.covered_until_event_id
            elif request.turn_input is not None:
                covered_until = request.turn_input.pre_turn_history_event_id
            else:
                raise ValueError(
                    "PREFLIGHT_COMPACTION requires turn_input or explicit covered_until_event_id"
                )
        events = await self._ports.session_events.load_events(SessionEventQuery(
            session_id=request.session_id,
            spawn_id=request.spawn_id,
            until_event_id=covered_until,
            order="asc",
        ))
        session_sections = SessionContextBuilder(events=events).build_sections(
            until_event_id=covered_until,
            include_attachments=(request.session_attachments_override is None),
        )
        jobs = await self._load_jobs_or_empty(request.session_id)

        inputs = ContextCompositionInputs(
            user_instructions_text=request.user_instructions.text,
            compacted_history_summary=request.compacted_history_summary,
            turn_input=request.turn_input,
            session_sections=session_sections,
            session_jobs=jobs,
            session_attachments_override=request.session_attachments_override,
            defer_turn_instruction=True,   # 压缩后 instruction 移末尾, recency bias
        )
        user_turn_context = composition.apply(inputs)

        return AssemblyResult(
            user_turn_context=user_turn_context,
            user_instructions_text=request.user_instructions.text,
            user_instructions_hash=request.user_instructions.hash,
            used_composition=composition.name,
            covered_until_event_id=covered_until,  # 派生结果回传, compactor 写入 checkpoint payload
        )

    async def _load_jobs_or_empty(self, session_id: str) -> SessionJobs:
        """Optional port 语义: 无 port = 空 SessionJobs, 不判断 Bohrium 是否启用 (硬约束 #11)。"""
        if self._ports.session_jobs is None:
            return SessionJobs.empty()
        return await self._ports.session_jobs.load_session_jobs(
            SessionJobsQuery(session_id=session_id),
        )
```

### 7bis.4 Port 平台实现（`src/services/context_assembly_ports.py`）

```python
# src/services/context_assembly_ports.py
import hashlib
import logging
from pathlib import Path

from matmaster.context.ports import (
    SessionEvent, SessionEventQuery, SessionEventsPort,
    SessionJobsQuery, SessionJobs, SessionJobsPort,
    UserInstructions, UserInstructionsPort,
)

logger = logging.getLogger(__name__)
USER_INSTRUCTIONS_MAX_BYTES = 50 * 1024


class AppUserInstructionsPort:
    """AGENT.md 路径约定, size cap, hash 计算的归属。"""

    async def load_user_instructions(
        self, workspace_root: Path,
    ) -> UserInstructions:
        path = workspace_root / ".matmaster" / "AGENT.md"
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return UserInstructions(text="", hash=_hash(""), truncated=False)

        truncated = False
        if len(raw.encode("utf-8")) > USER_INSTRUCTIONS_MAX_BYTES:
            logger.warning(
                "AGENT.md exceeds %d bytes, truncating", USER_INSTRUCTIONS_MAX_BYTES,
            )
            raw = raw.encode("utf-8")[:USER_INSTRUCTIONS_MAX_BYTES].decode(
                "utf-8", errors="ignore",
            )
            truncated = True
        return UserInstructions(text=raw, hash=_hash(raw), truncated=truncated)


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class AppSessionEventsPort:
    def __init__(self, events_service: "ChatEventsService") -> None:
        self._events_service = events_service

    async def load_events(
        self, query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]:
        rows = self._events_service.query(
            session_id=query.session_id,
            spawn_id=query.spawn_id,
            until_event_id=query.until_event_id,
            event_types=query.event_types,
            limit=query.limit,
            order=query.order,
        )
        return tuple(
            SessionEvent(
                id=r.id,
                event_type=r.event_type,
                source=r.source,
                content=r.content,        # ChatEventsTable 已返回 JSON dict
                task_id=r.task_id,
                invocation_id=r.invocation_id,
                spawn_id=r.spawn_id,
            )
            for r in rows
        )


class AppSessionJobsPort:
    """占位: bohrium job ledger 接入时实现。"""
    async def load_session_jobs(
        self, query: SessionJobsQuery,
    ) -> SessionJobs:
        return SessionJobs.empty()
```

### 7bis.5 `turn_intent.py` 双层（核心纯函数 + service helper）

```python
# matmaster/context/turn_intent.py
from matmaster.context.assembly import ContextAssemblyIntent


def decide_turn_context_intent(
    *,
    current_hash: str,
    latest_anchor_hash: str | None,
) -> ContextAssemblyIntent:
    """纯函数: 仅依据 hash 比对判定 anchor / continuation。

    不读 events, 不知迁移兼容策略。
    """
    if latest_anchor_hash is None or latest_anchor_hash != current_hash:
        return ContextAssemblyIntent.ANCHOR_TURN
    return ContextAssemblyIntent.CONTINUATION_TURN
```

```python
# src/services/context_turn_intent.py
from matmaster.context.assembly import ContextAssemblyIntent
from matmaster.context.ports import SessionEvent, SessionEventQuery, SessionEventsPort
from matmaster.context.turn_intent import decide_turn_context_intent


async def resolve_turn_context_intent(
    *,
    instructions_hash: str,
    session_id: str,
    spawn_id: str | None,
    events_port: SessionEventsPort,
) -> ContextAssemblyIntent:
    """Service-side helper: events 查询 + 纯判定。

    Phase 1 不引入 runtime 分流开关。新 turn 一律走 user_turn_context
    写入路径；兼容性只保留在 restore/codec 读取旧数据时。
    """
    events = await events_port.load_events(SessionEventQuery(
        session_id=session_id,
        spawn_id=spawn_id,
        event_types=("user_turn_context", "history_checkpoint"),
        limit=50,
        order="desc",
    ))
    latest_hash = _latest_anchor_hash_from_events(events)
    return decide_turn_context_intent(
        current_hash=instructions_hash,
        latest_anchor_hash=latest_hash,
    )


def _latest_anchor_hash_from_events(
    events: tuple[SessionEvent, ...],
) -> str | None:
    for ev in events:    # already DESC
        if ev.event_type == "user_turn_context":
            if ev.content.get("kind") == "anchor":
                return ev.content.get("user_instructions_hash")
        elif ev.event_type == "history_checkpoint":
            return ev.content.get("user_instructions_hash")
    return None
```

### 7bis.6 调用图

```
agent_run_service._prepare_and_dispatch:
  1. events_service.add_history_event(User/query)
  2. ports.user_instructions.load(workspace_root)   → UserInstructions  ← 一次读取
  3. context_turn_intent.resolve_turn_context_intent(bundle.hash, events_port)
                                                    → ContextAssemblyIntent
  4. assembler.assemble_turn(intent, TurnAssemblyRequest(..., user_instructions=bundle))
                                                    → AssemblyResult (assembler 不再读 AGENT.md)
  5. events_service.add_history_event(user_turn_context, fail-fast)

ContextCompactor.apply_compaction_plan:
  1. compactor 自己产生 summary (LLM 或 fallback)
  2. assembler.assemble_compaction(intent, CompactionAssemblyRequest(..., user_instructions=bundle))
                                                    → AssemblyResult
  3. messages[:] = [system_msg, result.to_message(RUNTIME)]
  4. base_messages = [result.to_message(CHECKPOINT).model_dump(...)]
  5. 写 history_checkpoint 含 user_instructions_text / hash
```

注意 service 调 events_port 两次（intent resolver 一次扫最近 50 条；assembler 内部一次按时间升序取全部），两次查询字段不同（前者过滤 type + 倒序 + limit；后者完整 + 升序），重叠不可避免。可以在 port 实现层加 per-request 简单 cache，但属于平台优化，不在 spec 范围。

---

## 8. AGENT.md 处理（hash-triggered anchor）

v3 核心新设计章节。回应"AGENT.md 改动响应性回退"的 review 意见。

### 8.1 设计目标

- 用户改 AGENT.md 后，**下一轮请求立即生效**
- 不依赖 compaction 触发
- 不每轮都重复装载长 prefix（hash 未变时复用旧 anchor）
- restore 路径自然 work，不需要状态机或字符串替换魔法

### 8.2 写入决策（service 层每轮，v3.1 改写）

v3 这一节展示了 service 内 inline 拼装。v3.1 改为：service 只判定 intent + 调 assembler + 写事件，不感知 source 拼装细节。

```python
# src/services/agent_run_service.py 改造后片段

from matmaster.context.assembly import (
    ContextAssembler, ContextAssemblyIntent, TurnAssemblyRequest,
)
from matmaster.context.sections import ContextView
from src.services.context_turn_intent import resolve_turn_context_intent


async def _prepare_and_dispatch(req: SendMessageRequest) -> ...:
    # 1. 写 raw User/query。user_turn_context 与该事件通过 invocation_id 关联，
    #    不依赖 DAO 返回 inserted row id。
    await events_service.add_history_event(
        session_id,
        payload={
            "source": "User",
            "type": "query",
            "content": req.content,
            "files": req.files,
            "images": req.images,
            "workspace_paths": req.workspace_paths,
            "task_id": task_id,
            "invocation_id": invocation_id,
        },
        user_id=user_id,
    )

    # 2. 读 AGENT.md (一次读取贯穿整个 turn, 硬约束 #7)
    instructions = await user_instructions_port.load_user_instructions(workspace_root)

    # 3. 判定 intent (service helper; Phase 1 直接走新路径, 无 runtime 分流)
    intent = await resolve_turn_context_intent(
        instructions_hash=instructions.hash,
        session_id=session_id,
        spawn_id=spawn_id,
        events_port=session_events_port,
    )

    # 4. 构造 TurnInput
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text=req.content),
        attachments=TurnAttachmentsSource(
            files=tuple(req.files),
            images=tuple(req.images),
            workspace_paths=tuple(req.workspace_paths),
        ),
        pre_turn_history_event_id=pre_query_scope_event_id,
    )

    # 5. 调 assembler 装配 (instructions bundle 原样传入, 禁止二次读)
    # 注: 历史视图边界 pre_query_scope_event_id 已经塞进 turn_input.pre_turn_history_event_id,
    # 不再向 TurnAssemblyRequest 重复传递 (v3.2 简化)
    assembly = await context_assembler.assemble_turn(
        intent=intent,
        request=TurnAssemblyRequest(
            session_id=session_id,
            spawn_id=spawn_id,
            turn_input=turn_input,
            user_instructions=instructions,
        ),
    )

    # 6. 写 user_turn_context (fail-fast, 硬约束 #2)
    rendered_message = assembly.user_turn_context.to_message(ContextView.RUNTIME)
    try:
        await events_service.add_history_event(
            session_id,
            payload={
                "source": "matmaster",
                "type": "user_turn_context",
                "content": {
                    "schema_version": "user_turn_context.v1",
                    "kind": "anchor" if intent.is_anchor_turn else "continuation",
                    "message": rendered_message.model_dump(mode="json"),
                    "user_instructions_hash": (
                        assembly.user_instructions_hash
                        if intent.is_anchor_turn else None
                    ),
                    "transform": "raw",
                    "render_version": "user_context_render.v1",
                },
                "task_id": task_id,
                "invocation_id": invocation_id,
            },
            user_id=user_id,
        )
    except Exception:
        logger.exception("user_turn_context write failed; aborting turn")
        raise

    # 7. 调 kernel
    model_messages = await model_history_restore_service.restore(
        session_id, spawn_id=spawn_id,
    )
    async for event in exp.run_stream(
        pg_ctx,
        model_messages=model_messages,
        cancel_token=cancel_token,
        skills=...,
    ):
        ...
```

关键变化（相对 v3）：

- 步骤 1（读 AGENT.md）拆出去, 走 `UserInstructionsPort` → 一份 typed `UserInstructions`
- 步骤 3（判定 kind）改为调 `resolve_turn_context_intent` helper（在 `src/services/context_turn_intent.py`），只负责查询 events 与调用纯函数；**不引入 runtime 分流开关**
- 步骤 5（装配）消失，改为 `assembler.assemble_turn(intent, request)`；service 不再知道 source 列表
- `instructions_text` / `instructions_hash` 不再单独传，统一打包为 `UserInstructions`，原样传入 assembler（硬约束 §4.2 #7）

### 8.3 latest anchor hash 查询（v3.1 改写）

v3 在此节展示了 service 私有 helper `_latest_anchor_user_instructions_hash` 的实现。v3.1 把这部分逻辑移到 `src/services/context_turn_intent.py`（见 §7bis.5）的 `resolve_turn_context_intent` 与 `_latest_anchor_hash_from_events`。

要点：

- 复用 `SessionEventsPort.load_events`（`limit=50`、`order="desc"`、`event_types=("user_turn_context", "history_checkpoint")`），不引入独立 `LatestAnchorHashPort`
- 兜底逻辑相同：扫 50 条仍未找到 anchor → 按首轮处理（返回 `ANCHOR_TURN`）
- Phase 1 不设置 runtime 分流配置；新 turn 一律写 `user_turn_context`。旧数据兼容只发生在 restore/codec 读取路径（见 `COMPAT:v0-restore` / `COMPAT:v0-checkpoint-marker`）。

### 8.4 几个场景演化

| 场景 | events 序列 | restore 后 messages |
|------|-------------|---------------------|
| Session 首轮，AGENT.md v1 | User/query, user_turn_context(anchor, hash=v1) | [Sys, anchor_v1+turn] |
| 第 2 轮，AGENT.md 未变 | + User/query, user_turn_context(continuation) | [Sys, anchor_v1+turn1, Asst1, anchor_v1 序列, turn2] |
| 第 3 轮，AGENT.md 改到 v2 | + User/query, user_turn_context(anchor, hash=v2) | [Sys, anchor_v1+turn1, Asst1, turn2, ..., anchor_v2+turn3] |
| 第 4 轮，AGENT.md 仍 v2 | + User/query, user_turn_context(continuation) | 同上 + turn4 |
| 第 5 轮，触发 preflight compaction | + User/query, history_checkpoint(...), user_turn_context(anchor, transform=preflight_compacted, hash=v2) | [Sys, base_anchor_v2+turn5] |

**关键观察**：anchor 在 messages 中**不一定是第一条**。第 3 轮之后，messages 序列里同时存在 `anchor_v1` 和 `anchor_v2`。LLM 自然理解"更靠近末尾的 instructions 是最新版"（近端 attention 偏向）。

这与 v2 "anchor 必须是 messages[0]" 的假设不同，但更简洁：不需要字符串重写、不需要 restore 状态机维护。

### 8.5 与 history_checkpoint 的交互

压缩触发时，compactor 写 `history_checkpoint`，payload 包含 `user_instructions_text` / `user_instructions_hash`（v3.1：service 层调 compactor 前已读取一次 `UserInstructions` 并原样传给 compactor，实例内 text 与 hash 一致）。compactor 内部通过 `ContextAssembler.assemble_compaction` 装配的 anchor base_messages[0] 用的就是这个 hash 对应的 AGENT.md 内容。

下一轮请求时，v3.1 的 `resolve_turn_context_intent` helper（在 `src/services/context_turn_intent.py`，参见 §7bis.5）会查询最近 events 找到 `history_checkpoint.user_instructions_hash`，并与 service 层当前读到的 AGENT.md hash 比对。如果用户在压缩后又改了 AGENT.md，下一轮会再写一条 `user_turn_context(kind=anchor, hash=新)`。

### 8.6 hash 计算细节

```python
def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
```

empty AGENT.md（不存在或全空白）也要算 hash（sha256("")），保证 None 状态可比较。

---

## 9. `compaction.py` — `ContextCompactor` 改造

### 9.1 v3.1 改造原则

- 改造前后行为等价（保留 fallback、保留 strategy 字段、保留 retained_turns 字段）
- 装配方式从手写字符串 / 手写 source 拼装 改为调用 `ContextAssembler.assemble_compaction`
- 接受 service 层注入的 `user_instructions: UserInstructions`（统一对象，禁止 text/hash 拆传）
- compactor 内部**不持有** `SessionContextBuilder`，**不直接 import** `UserInstructionsSource` / `CompactedHistorySource` / `SessionJobsSource` 等 source（硬约束 §4.2 #2 / #4）
- 写入的 `history_checkpoint` payload 含新字段

### 9.2 改造后 `apply_compaction_plan`（v3.1）

```python
async def apply_compaction_plan(
    self,
    plan: CompactionPlan,
    messages: list[Message],
    *,
    intent: ContextAssemblyIntent,           # PREFLIGHT_COMPACTION 或 RUNTIME_COMPACTION
    user_instructions: UserInstructions,
    turn_input: TurnInput | None = None,
    summary_override: str | None = None,
    session_attachments_override: SessionAttachmentsSource | None = None,
) -> CompactionResult:
    """执行压缩并替换 messages。

    preset_* 参数是 oversized input (Case 3 / Phase 4) 的预留旁路,
    本 spec 阶段不实现端到端调用,仅保留参数位避免接口变更。
    """
    if not messages:
        raise ValueError("Cannot compact an empty message list")
    if not isinstance(messages[0], SystemMessage):
        raise TypeError(f"messages[0] must be SystemMessage, got {type(messages[0])}")
    if not intent.is_compaction:
        raise ValueError(f"apply_compaction_plan requires compaction intent, got {intent!r}")

    system_msg = messages[0]

    # === Summary 阶段 (保留 fallback, 不进 assembler) ===
    if summary_override is not None:
        summary = summary_override
        strategy, durability, failure_reason = "summary", "durable", None
    else:
        summary_input = self._select_summary_input(messages, turn_input)
        if not summary_input:
            raise ValueError("Cannot compact messages without history")
        try:
            summary = await self._summarize(summary_input)
            strategy, durability, failure_reason = "summary", "durable", None
        except Exception as e:
            logger.warning("Summary failed, falling back: %s", e)
            summary, strategy, failure_reason = self._fallback(messages, e)
            durability = "ephemeral"

    # === 装配走 ContextAssembler (硬约束 §4.2 #4) ===
    # covered_until_event_id 不再由 compactor 本地派生; 派生规则集中在 assembler 内,
    # 调用方仅传 turn_input, 真实边界从 assembly.covered_until_event_id 读回
    assembly = await self._context_assembler.assemble_compaction(
        intent=intent,
        request=CompactionAssemblyRequest(
            session_id=self._session_id,
            spawn_id=self._spawn_id,
            user_instructions=user_instructions,
            compacted_history_summary=summary,    # compactor 内部局部变量 summary 透传
            turn_input=turn_input,
            session_attachments_override=session_attachments_override,
        ),
    )

    # === 双视图渲染 (assembler 不渲染, caller 选 view) ===
    runtime_message = assembly.user_turn_context.to_message(ContextView.RUNTIME)
    messages[:] = [system_msg, runtime_message]

    checkpoint_message = assembly.user_turn_context.to_message(ContextView.CHECKPOINT)
    base_messages = [checkpoint_message.model_dump(mode="json")]

    return CompactionResult(
        compaction_id=plan.compaction_id,
        compaction_count=plan.compaction_count,
        phase=plan.phase,
        strategy=strategy,
        durability=durability,
        trigger_tokens=plan.trigger_tokens,
        retained_turns=0,
        failure_reason=failure_reason,
        base_messages=base_messages,
        checkpoint_covered_until_event_id=assembly.covered_until_event_id,
        # 新字段 (会被 sink 写到 history_checkpoint payload)
        user_instructions_text=assembly.user_instructions_text,
        user_instructions_hash=assembly.user_instructions_hash,
    )


def _fallback(
    self,
    messages: list[Message],
    error: Exception,
) -> tuple[str, str, str]:
    """Summary LLM 失败时的降级。保留为 Phase 3 默认行为,不在本 spec 删除。"""
    # 实现等价于现 context_compactor.py:364-385, 本节略
    ...
```

**关键点（v3.1）**：

- compactor 入口的 `user_instructions_text` / `user_instructions_hash` 两个 str 参数**合并**为 `user_instructions: UserInstructions`，避免 text/hash 拆开传可能出现的不一致（硬约束 §4.2 #7 在 compactor 路径的延伸；text 与 hash 在 `UserInstructions` 实例内保证一致）
- compactor 新增 `intent` 必填参数。caller 必须明确传 `PREFLIGHT_COMPACTION` 或 `RUNTIME_COMPACTION`，由 caller 决定语义
- `runtime_message` 与 `checkpoint_message` 的差异完全靠 view 过滤实现：同一份 `assembly.user_turn_context` 投影两次
- compactor `__init__` 注入 `context_assembler: ContextAssembler`（取代 v3 注入 `session_builder`）
- **fallback 路径保留**。`sliding_window` / `tool_truncation` 删除决策延后到 Phase 3 完成、有埋点数据后单独评估
- summary 生成仍在 compactor 内部（涉及 LLM 调用、token 预算、fallback 策略），不进 assembler；assembler 拿到 summary 字符串后只负责 section 组装

### 9.3 类型保留 + shim 链路

`ContextCompactor` 类名保留，文件名用 `compaction.py`。命名一致性见 §13。

Phase 3 期间，旧路径 `matmaster/core/context_compactor.py` 改为薄 shim（与 `core/context_compactor.py` → `context/compaction.py` 的真实迁移**同一阶段**完成；Phase 2 不触碰 compactor 主路径）：

```python
# matmaster/core/context_compactor.py (Phase 3 shim)
from matmaster.context.compaction import (  # noqa: F401
    ContextCompactor,
    CompactionPlan,
    CompactionResult,
    estimate_tokens,
    parse_turns,
)
```

---

## 10. `core/agent.py` 改造

### 10.1 v3 关键变化

v3 把 v2 的 snapshot_sink 整套机制**从 kernel 删除**。kernel 不再感知 `user_turn_context` 事件。

- service 层在调 `kernel.run_stream` 之前已经写完 `User/query` + `user_turn_context` 两条事件
- service 层调 `ModelHistoryRestoreService.restore(...)` 拿到完整 `model_messages`（含本轮 user_turn_context 渲染后的 UserMessage 作为最后一条）
- kernel.run_stream 直接用 model_messages，不做 turn_input 装配
- kernel.run_stream 的 `task` 参数语义改变：v3 中 task 可以是空字符串（因为 user message 已经在 model_messages 末尾），或 deprecated

### 10.2 kernel 入口简化

```python
# matmaster/core/agent.py 改造后伪代码

async def _run_items(self, spec, task, model_messages, ...):
    """v3: model_messages 已经是完整的 LLM 视图,包含本轮 user message。
    kernel 不再装配 turn_input。
    """
    if not model_messages:
        raise ValueError("v3 kernel.run_stream requires non-empty model_messages")
    if not isinstance(model_messages[-1], UserMessage):
        raise ValueError("model_messages[-1] must be UserMessage in v3")

    state = _KernelState(
        messages=[
            SystemMessage(content=spec.system_prompt),
            *model_messages,
        ]
    )

    # 主 turn 循环 (agentic tool loop): v3 不再写任何 snapshot 事件
    while state.turn < spec.max_turns:
        state.turn += 1

        # ── runtime compaction (可能改写 state.messages) ──
        if spec.compactor:
            plan = await spec.compactor.plan_runtime_compaction(...)
            if plan is not None:
                async for item in self._run_compaction_plan(
                    plan, state,
                    user_instructions_text=spec.user_instructions_text,
                    user_instructions_hash=spec.user_instructions_hash,
                ):
                    yield item

        api_messages = normalize_and_validate_openai_messages(
            canonicalize_messages_for_provider(state.messages)
        )

        # ── LLM 调用 ──
        response = await self._call_llm(api_messages, ...)
        ...
```

### 10.3 `AgentRuntimeSpec` 字段变更

| 字段 | v2 | v3 | v3.1 |
|------|-----|-----|-----|
| `context_builder: ContextBuilder` | 必填 | 保持（Phase 3 才改名为 `system_prompt_builder`） | 同 v3 |
| `turn_input: TurnInput` | 新增 | **删除**（kernel 不再装配 turn_input） | 同 v3 |
| `user_instructions_text: str` | 新增 | 保留（compactor 在 runtime compaction 时需要） | **删除**，由 `user_instructions: UserInstructions` 替代（通过 compactor 入口传入） |
| `user_instructions_hash: str` | 未规定 | **新增**（compactor 写 checkpoint 时需要） | **删除**（合并到 `UserInstructions`） |
| `runtime_ports.snapshot_sink` | 新增 | **删除** | 同 v3 |
| `runtime_ports.checkpoint_sink` | 已有 | 保留，payload 扩展（见 §3.3） | 同 v3 |
| `context_assembler: ContextAssembler` | — | — | **新增** |
| `user_instructions_port: UserInstructionsPort` | — | — | **新增** |
| `session_events_port: SessionEventsPort` | — | — | **新增** |
| `session_jobs_port: SessionJobsPort \| None` | — | — | **新增**（Optional） |

废除散落字段：

- `spec.meta["current_input_context"]`
- `spec.meta["attachment_manifest"]`
- `spec.meta["current_user_images"]`
- service 层的 `_apply_user_instructions_to_initial_user_query`（不再需要，AGENT.md 通过 `UserInstructionsPort` + `ContextAssembler` 装配）
- service 层的 `_load_user_instructions` 私有 helper（v3.1：等价能力进 `AppUserInstructionsPort`，见 §7bis.4）

---

## 11. `ModelHistoryRestorer` — backend model restore（v0/v1 分流）

### 11.1 接口与算法

`matmaster/context/history_restore.py` 暴露**纯算法**，不依赖 DB。通过回调接收 events 访问能力。

```python
# matmaster/context/history_restore.py
from __future__ import annotations
from collections.abc import Callable
from typing import Any

from matmaster.types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolMessage,
    UserMessage,
)


class ModelHistoryRestorer:
    """重建 backend 视角的 LLM 真实历史。"""

    def __init__(
        self,
        *,
        get_latest_checkpoint: Callable[[str, str | None], dict[str, Any] | None],
        get_events_after: Callable[[str, int | None, str | None], list[dict[str, Any]]],
        legacy_restore: Callable[[str, str | None], list[Message]],
    ) -> None:
        """
        Args:
            get_latest_checkpoint(session_id, spawn_id) -> checkpoint dict or None
            get_events_after(session_id, after_id, spawn_id) -> list of events
            legacy_restore(session_id, spawn_id) -> messages
                v0 路径委托给 ChatHistoryConverter.events_to_dialog_messages 的包装函数
        """
        self._get_latest_checkpoint = get_latest_checkpoint
        self._get_events_after = get_events_after
        self._legacy_restore = legacy_restore

    def restore(
        self,
        session_id: str,
        *,
        spawn_id: str | None = None,
    ) -> list[Message]:
        """Schema-aware 分流。

        v1 路径: 存在 v1 history_checkpoint 时使用
        COMPAT:v0-restore: 兼容旧 session, 委托给 ChatHistoryConverter
        """
        checkpoint = self._get_latest_checkpoint(session_id, spawn_id)
        schema_v1 = (
            checkpoint is not None
            and checkpoint.get("content", {}).get("schema_version") == "history_checkpoint.v1"
        )

        # 判断是否走 v1: 有 v1 checkpoint OR (无 checkpoint 但 events 含 user_turn_context)
        if not schema_v1:
            has_user_turn_context = self._session_has_user_turn_context(session_id, spawn_id)
            if not has_user_turn_context:
                # COMPAT:v0-restore
                return self._legacy_restore(session_id, spawn_id)

        return self._restore_v1(session_id, spawn_id, checkpoint)

    def _restore_v1(
        self,
        session_id: str,
        spawn_id: str | None,
        checkpoint: dict[str, Any] | None,
    ) -> list[Message]:
        if checkpoint is not None and checkpoint.get("content", {}).get("schema_version") == "history_checkpoint.v1":
            content = checkpoint["content"]
            after = content.get("covered_until_event_id")
            if after is None:
                # v1 checkpoint 必须有确定边界 (见 §3.3); null 视为 checkpoint 损坏,
                # 回退到 legacy restore 比 silently replay 全量更安全
                # COMPAT:v0-restore
                logger.warning(
                    "history_checkpoint.v1 has null covered_until_event_id; "
                    "falling back to legacy restore (COMPAT:v0-restore)"
                )
                return self._legacy_restore(session_id, spawn_id)
            messages = self._deserialize_messages(content["base_messages"])
        else:
            messages = []
            after = None

        events = self._get_events_after(session_id, after, spawn_id)

        for event in events:
            etype = event.get("type")
            payload = event.get("content", {})

            if etype == "user_turn_context":
                msg_dict = payload.get("message", {})
                messages.append(UserMessage.model_validate(msg_dict))

            elif etype == "assistant_state":
                # tool_calls 分支的权威 (kernel agent.py:535)
                from matmaster.types.message_normalization import restore_persisted_assistant_state
                inner = payload.get("state") or payload
                try:
                    msg = restore_persisted_assistant_state(inner)
                    messages.append(msg)
                except Exception:
                    logger.warning("assistant_state restore failed, skipping")

            elif etype in ("response", "run_result", "finish"):
                # 自然结束分支 (kernel agent.py:498-513)
                # 注意: 同一 turn 不会同时有 assistant_state(tool_calls) 和 response,
                # 因为 kernel 二选一。重复出现按出现顺序 append (defensive)。
                content = payload.get("content") or payload.get("text") or ""
                reasoning = payload.get("reasoning_content")
                messages.append(AssistantMessage(
                    content=content,
                    reasoning_content=reasoning,
                ))

            elif etype == "tool_result":
                messages.append(ToolMessage(
                    content=payload.get("result", ""),
                    tool_call_id=payload["call_id"],
                    tool_name=payload.get("tool_name", ""),
                ))
            # 其他类型(skill_hit, thought, planner_reply, log_line, ...)跳过

        return messages

    def _session_has_user_turn_context(
        self,
        session_id: str,
        spawn_id: str | None,
    ) -> bool:
        """Quick scan: 检查 session 是否有任何 user_turn_context 事件。

        用于无 checkpoint 时判定走 v1 还是 v0。实现可以查 events 表 EXISTS。
        """
        events = self._get_events_after(session_id, None, spawn_id)
        return any(e.get("type") == "user_turn_context" for e in events[:200])

    @staticmethod
    def _deserialize_messages(raw: list[dict[str, Any]]) -> list[Message]:
        """checkpoint.base_messages 只含 UserMessage (与现有 codec 契约一致)。"""
        result: list[Message] = []
        for m in raw:
            role = m.get("role")
            if role == "user":
                result.append(UserMessage.model_validate(m))
            elif role == "assistant":
                result.append(AssistantMessage.model_validate(m))
            elif role == "tool":
                result.append(ToolMessage.model_validate(m))
            else:
                logger.warning(
                    "Unexpected role %r in checkpoint.base_messages; dropping", role,
                )
        return result
```

### 11.2 v1 路径 assistant 侧的保守消费

v3 的 v1 restore **同时消费** `assistant_state`（tool_calls 分支）和 `response`/`run_result`/`finish`（自然结束分支）。

**这是有意保守的设计**。Spec review 提议两种方案：
- 方案 A：保留现状 kernel 行为，restore 同时消费两类事件。复杂度在 restore 端
- 方案 B：扩展 kernel 让所有 LLM response 都写 assistant_state，restore 算法简化

v3 选 A，理由：

1. kernel 改动回归风险大，本次 spec 目标是上下文模块清理而非 kernel 持久化重构
2. 同一 turn 不会同时有 `assistant_state` 和 `response` —— kernel 二选一（[agent.py:498-513](../../matmaster/core/agent.py:498) 自然结束直接 return；[agent.py:535](../../matmaster/core/agent.py:535) tool 路径才写 assistant_state）
3. v0 路径的 `ChatHistoryConverter.events_to_dialog_messages` 已经在生产环境验证过这套消费规则

方案 B 留作未来 phase（独立 spec）。

### 11.3 `src/services/` 的 DI 实现

```python
# src/services/model_history_restore_service.py (从原 history_restore_service.py 改名)
from matmaster.context.history_restore import ModelHistoryRestorer
from src.services.chat_history import ChatHistoryConverter


def build_model_restorer(events_dao: ChatEventsTable) -> ModelHistoryRestorer:
    def get_latest_checkpoint(session_id: str, spawn_id: str | None) -> dict | None:
        row = events_dao.query_latest_by_type(
            session_id, event_type="history_checkpoint", spawn_id=spawn_id,
        )
        return {"content": row.content, "id": row.id} if row else None

    def get_events_after(
        session_id: str, after_event_id: int | None, spawn_id: str | None,
    ) -> list[dict]:
        rows = events_dao.query_after(
            session_id, after_event_id, spawn_id=spawn_id,
        )
        return [{"id": r.id, "type": r.event_type, "content": r.content} for r in rows]

    def legacy_restore(session_id: str, spawn_id: str | None) -> list[Message]:
        events = events_dao.get_session_events(session_id, include_spawn=False)
        dialog = ChatHistoryConverter.events_to_dialog_messages(events)
        return [Message.model_validate(m) for m in dialog]

    return ModelHistoryRestorer(
        get_latest_checkpoint=get_latest_checkpoint,
        get_events_after=get_events_after,
        legacy_restore=legacy_restore,
    )
```

### 11.4 多次压缩链路

最新 checkpoint 已经把更老的 compact bundle 合并到新 summary 中（现 [context_compactor.py:46](../../matmaster/core/context_compactor.py:46) 的 `SUMMARY_SYSTEM_PROMPT` 显式要求 "merge older compact bundle with later events"）。

所以 `ModelHistoryRestorer.restore` 只取最新 checkpoint 即可，不叠加。

### 11.5 `history_checkpoint_codec.py` 兼容性（`COMPAT:v0-checkpoint-marker`）

现 [src/services/history_checkpoint_codec.py:89-91](../../src/services/history_checkpoint_codec.py:89) 强制 `<previous_session_summary>` marker。v3 要把 codec 改为接受两种 marker：

```python
# v3 改造后
MARKERS_V0 = {"<previous_session_summary>"}
MARKERS_V1 = {"<compacted_history>"}

def _has_acceptable_marker(content: str) -> bool:
    return any(m in content for m in (MARKERS_V0 | MARKERS_V1))

# validate_base_messages 中
if not _has_acceptable_marker(first_content):
    raise ValueError(
        "checkpoint base_messages[0] must contain compact context bundle marker"
    )
```

base_messages 不含 SystemMessage 的约束（[codec line 86](../../src/services/history_checkpoint_codec.py:86)）保留。

`COMPAT:v0-checkpoint-marker` 的 v0 marker 退役在 Phase 4（独立 phase，30+ 天后评估）。

---

## 12. 四个 Case 的装配视图（v3.1 改为 intent / composition / port 数据流）

v3 在此用 "装配的 sources" 列表描述每个 case。v3.1 把它改为 caller / intent / composition / port 的数据流视图，更贴近实现细节。

| Case | 触发点 caller | intent | composition (assembler 内部选) | 必需 ports | user_turn_context.kind |
|------|---------------|--------|-----------------|-----------|------------------------|
| **1. 首轮无压缩** | `agent_run_service._prepare_and_dispatch` | `ANCHOR_TURN` | `ANCHOR_COMPOSITION` | UserInstructions(service 调) + SessionEvents + SessionJobs? | `anchor` |
| **1b. 普通延续轮，hash 未变** | 同上 | `CONTINUATION_TURN` | `CONTINUATION_COMPOSITION` | UserInstructions(service 调) + SessionJobs?；assembler 内部不查 events（continuation 不需要 session sections） | `continuation` |
| **1c. 延续轮，hash 变了** | 同上 | `ANCHOR_TURN` | `ANCHOR_COMPOSITION` | 同 case 1 | `anchor` |
| **2. 运行中 runtime compaction（无新输入）** | `ContextCompactor.apply_compaction_plan` | `RUNTIME_COMPACTION` | `COMPACTED_COMPOSITION` | UserInstructions(service 调，传 bundle 给 compactor) + SessionEvents + SessionJobs? | （不写 user_turn_context，只写 history_checkpoint） |
| **3. Oversized input** | **本 spec 不实现，Phase 4 独立 spec** | 预留 `transform="oversized_summary"` | 预留 | — | 预留 |
| **4. Preflight compaction（新输入 + 立即压缩）** | `ContextCompactor.apply_compaction_plan`（service 触发） | `PREFLIGHT_COMPACTION` | `COMPACTED_COMPOSITION` | 同 case 2 | （compactor 写 history_checkpoint；service 写 `user_turn_context(transform="preflight_compacted")`）|

> 注：`UserInstructions` 只由 service 通过 `UserInstructionsPort.load_user_instructions` 读取一次（硬约束 §4.2 #7），然后原样传给 assembler / compactor。`UserInstructionsPort` 不在 `ContextAssemblyPorts` 中。

最终渲染顺序：长期约束 → 历史摘要 → 可用能力 → 过去材料 → 本轮材料 → 本轮任务 → 当前 job 状态（由 `SectionOrder` enum 在 `render_sections` 内部 sort 控制，不依赖 composition step 顺序）。

---

## 13. 命名清理表

### 类型与变量

| 旧名 | 新名 | 原因 |
|------|------|------|
| `ContextVisibility` | `ContextView` | 表达"视图选择"，不是"可见性" |
| ~~`ModelVisibleUserContext`~~ ~~`UserContextSnapshot`~~ | `UserTurnContext` + `user_turn_context` event | snapshot 概念废弃 |
| `CompactionRehydrator` | `SessionContextBuilder` | 不是 hydration，是 session context collection |
| `pre_query_scope_event_id` | `pre_turn_history_event_id` | 实现细节剥离，语义直接 |
| `attachment_manifest` | `session_attachments` | 不是 manifest，是 source；时间作用域统一为 `Session-` 前缀 |
| `skill_manifest` | `session_skills` | 同上 |
| `mcp_manifest` | `session_tools` | 同上 + "tools" 是 provider-facing 语义 |
| `HistoryRestoreService` | `ModelHistoryRestoreService` | 当前名字暗示"通用历史恢复"，实际只服务 model restore 路径 |

### 文件与目录

| 旧 | 新 |
|----|-----|
| `matmaster/manifests/` | `matmaster/context/` 内部模块 |
| `matmaster/manifests/rehydrator.py` | `matmaster/context/session.py` |
| `matmaster/manifests/attachment.py` | `matmaster/context/sources/attachments.py` |
| `matmaster/manifests/skill.py` | `matmaster/context/sources/skills.py` |
| `matmaster/manifests/mcp.py` | `matmaster/context/sources/tools.py`（保留 `mcp.py` shim） |
| `matmaster/manifests/scanner.py` | `matmaster/context/scanner.py` |
| `matmaster/manifests/artifact.py` | `matmaster/context/sources/artifacts.py` |
| `matmaster/manifests/bohrium.py` | `matmaster/context/sources/session_jobs.py` |
| `matmaster/manifests/workspace.py` | `matmaster/context/sources/workspace.py` |
| `matmaster/types/context.py` | 阶段 3 删除；定义迁回**已有的** `matmaster/core/playground.py`；shim 保留至阶段 3 |
| `matmaster/types/current_input.py` | `matmaster/context/sources/turn_input.py` |
| `matmaster/core/context_builder.py` | 拆三段（见 §15） |
| `matmaster/core/context_compactor.py` | `matmaster/context/compaction.py`（shim 路径保留至阶段 3） |
| `src/services/history_restore_service.py` | `src/services/model_history_restore_service.py` |
| `tests/matmaster/manifests/` | `tests/matmaster/context/` |

### `legal_mcp_servers` 重命名

`SessionContextBuilder.__init__` 的参数 `legal_mcp_servers` 改名 `allowed_mcp_servers`。原命名暗示"法律合规"，实际是"已注册"语义。

### v3.1 新引入的类型与变量

| 名字 | 位置 | 用途 |
|------|------|------|
| `ContextAssemblyIntent` | `matmaster/context/assembly.py` | enum: ANCHOR_TURN / CONTINUATION_TURN / PREFLIGHT_COMPACTION / RUNTIME_COMPACTION |
| `TurnAssemblyRequest` | `matmaster/context/assembly.py` | 普通 turn 装配 request |
| `CompactionAssemblyRequest` | `matmaster/context/assembly.py` | 压缩装配 request |
| `AssemblyResult` | `matmaster/context/assembly.py` | 装配结果（含 user_turn_context + `UserInstructions` 透传） |
| `ContextAssembler` | `matmaster/context/assembly.py` | 装配执行器 |
| `ContextAssemblyPorts` | `matmaster/context/ports.py` | port 组合 dataclass |
| `UserInstructionsPort` / `UserInstructions` | `matmaster/context/ports.py` | AGENT.md 读取 protocol + typed 数据 (text/hash/truncated) |
| `SessionEventsPort` / `SessionEvent` / `SessionEventQuery` | `matmaster/context/ports.py` | events 读取 protocol + typed envelope |
| `SessionJobsPort` / `SessionJobs` / `SessionJobsQuery` | `matmaster/context/ports.py` | jobs 读取 protocol + typed 数据载体（Optional port） |
| `JsonScalar` / `JsonValue` / `JsonObject` | `matmaster/context/ports.py` | 受限 JSON 类型别名（硬约束 §4.2 #10） |
| `ContextComposition` / `ContextCompositionInputs` / `CompositionStep` | `matmaster/context/compositions.py` | composition 装配层 |
| `ANCHOR_COMPOSITION` / `CONTINUATION_COMPOSITION` / `COMPACTED_COMPOSITION` | `matmaster/context/compositions.py` | 三个 composition 常量 |
| `decide_turn_context_intent` | `matmaster/context/turn_intent.py` | 纯函数（hash 比对） |
| `resolve_turn_context_intent` | `src/services/context_turn_intent.py` | service helper（events 查询 + 调纯函数；无 runtime 分流） |
| `AppUserInstructionsPort` / `AppSessionEventsPort` / `AppSessionJobsPort` | `src/services/context_assembly_ports.py` | port 平台实现 |

### v3.1 新增的文件

| 路径 | 用途 |
|------|------|
| `matmaster/context/ports.py` | 核心模块对外的窄能力 Protocol + typed 数据载体 |
| `matmaster/context/compositions.py` | Composition + step 函数 + 三个 composition 常量 |
| `matmaster/context/assembly.py` | Intent / Request / Result / Assembler |
| `matmaster/context/turn_intent.py` | 纯函数 `decide_turn_context_intent` |
| `src/services/context_assembly_ports.py` | Port 协议的平台实现 |
| `src/services/context_turn_intent.py` | service-side intent resolver（events 查询 + 调纯函数；无 runtime 分流） |

---

## 14. 阶段迁移路线（v3.2：Phase 0/0.5 前置 + 4 主阶段，Phase 2 内含 2A/2B/2C 子阶段）

### Phase 0: 前置改造（独立 PR，无功能变化）

**目标**: 解除后续阶段的文件规模与职责耦合风险。这些是纯 mechanical refactor，可以**任何时候并行**于其他工作推进。v3.2 后 `user_turn_context` 不再保存 `source_query_event_id`，因此 DAO inserted id 返回链路不再是本 spec 的前置硬依赖。

**0a. 文件拆分（解除 1000 行限制风险）**:
- [matmaster/core/agent.py](../../matmaster/core/agent.py)（975 行）：抽出 snapshot/checkpoint sink wiring、preflight compaction 装配、tool 调度辅助到独立 helper 模块
- [src/services/agent_run_service.py](../../src/services/agent_run_service.py)（930 行）：抽出 instructions loading、history restore wiring、bohrium rebuild
- 目标行数 < 800 行/文件，预留 Phase 1-3 扩展空间
- [src/services/stream_service.py](../../src/services/stream_service.py)（960 行）：抽出 SSE filter 逻辑

**测试目标**: 现有测试全部通过；不引入新测试。

---

### Phase 0.5: PlaygroundContext import cycle cleanup（独立小 PR）

**目标**: 解除 [matmaster/core/playground.py:26](../../matmaster/core/playground.py:26) 与 `matmaster/types/context.py` 的反向 import 循环，为 Phase 2C 的 shim 化扫清依赖障碍。该阶段与 Phase 0 一样是纯 mechanical refactor，可以**任何时候并行**于其他工作推进。

**0.5a. 归位**:
- `PlaygroundContext` / `WorkspaceArchivalConfig` 定义归位到 `matmaster/core/playground.py`
- `matmaster/types/context.py` 改为薄 re-export shim，指向 `matmaster/core/playground.py`
- 检查所有 `from matmaster.types.context import ...` 与 `from matmaster.core.playground import ...` 的方向一致性

**0.5b. 不在本阶段做**:
- 不改 context assembly
- 不改 runtime behavior
- 不引入 v3.1 新类型

**测试目标**: 所有现有测试通过；import 顺序敏感的测试不再受 partial init 影响（必要时新增一组按不同顺序 import 的烟雾测试）。

---

### Phase 1: 事件语义阶段（核心）

**目标**: 落地两事件模型 + v0/v1 restore 分流 + SSE filter 改造 + AGENT.md hash anchor 决策。**不**改 prompt 形态，**不**做 Case 3，**不**动 ContextSection 内核（保留现 ContextBuilder 内的字符串拼接为现状渲染）。

**1a. 新事件类型注册**:
- 在 `ChatEventsTable` 接受 `event_type = "user_turn_context"` 的写入
- SSE filter [stream_service.py:_should_emit_event_to_sse](../../src/services/stream_service.py:66) 加 `user_turn_context` 到 hidden list
- live SSE handler `SSEHandler._should_skip()` 同步加

**1b. AGENT.md hash anchor 决策**:
- 实现 §8.2 的 service 层装配决策（is_anchor 判定 + 写 user_turn_context）
- 实现 `_latest_anchor_user_instructions_hash` 查询
- 实现 size cap (50KB) + hash 计算
- Phase 1 不引入 runtime 分流开关；新 turn 一律走 `user_turn_context` 写入路径
- `_apply_user_instructions_to_initial_user_query` 从 runtime 主路径移除；若因过渡需要短暂保留函数体，标记为 `COMPAT:legacy-runtime-injection-helper`，**最迟在 Phase 2C cutover 时删除**（2A / 2B 不动该函数体，2C 与 service 路径切换一起清理）

**1c. history_checkpoint payload 扩展**:
- `HistoryCheckpointService.build_checkpoint_sink` payload 加 `schema_version`、`render_version`、`user_instructions_text`、`user_instructions_hash`
- [history_checkpoint_codec.py](../../src/services/history_checkpoint_codec.py) 接受 v0/v1 双 marker（`<previous_session_summary>` / `<compacted_history>`），标记为 `COMPAT:v0-checkpoint-marker`
- 写入时仍输出 v0 marker（Phase 3 切到 v1）

**1d. ModelHistoryRestoreService 分流**:
- 改名 [history_restore_service.py](../../src/services/history_restore_service.py) → `model_history_restore_service.py`
- 内部实现 §11.1 的 schema-aware 分流
- v0 路径委托 `ChatHistoryConverter.events_to_dialog_messages`，标记为 `COMPAT:v0-restore`
- v1 路径同时消费 `user_turn_context` + `assistant_state` + `response`/`run_result` + `tool_result`

**1e. 兼容项标记（便于后续移除）**:
- `COMPAT:v0-restore`: 旧 session 没有 `user_turn_context` / v1 checkpoint 时，restore 委托 legacy `ChatHistoryConverter.events_to_dialog_messages`。后续删除条件：活跃 session 已迁移或产品确认不再恢复旧 session。
- `COMPAT:v0-checkpoint-marker`: `history_checkpoint_codec.py` 暂时接受 `<previous_session_summary>`。后续删除条件：写入切到 v1 marker 后，旧 checkpoint 观察窗口结束。
- `COMPAT:legacy-runtime-injection-helper`: 如 Phase 1 为降低 diff 暂时保留 `_apply_user_instructions_to_initial_user_query` 函数体，必须无 runtime caller，并**在 Phase 2C cutover 时删除**。

**1f. 测试目标**:
- 单元测试：`ModelHistoryRestorer._restore_v1` 各分支
- 单元测试：AGENT.md hash 决策（首轮 / hash 未变 / hash 变 / 50KB 超限 / 文件不存在）
- 集成测试：完整 session 写入 + 恢复
- 兼容测试：`COMPAT:v0-restore` 下 v0 session 的 restore 等价于现 `HistoryRestoreService`
- 兼容测试：`COMPAT:v0-checkpoint-marker` 下旧 marker checkpoint 仍能通过 codec 校验

---

### Phase 2: Context 模块阶段（v3.1 含装配三件套）

**整体目标**: 建立新 context 内核与装配三件套，并完成普通 user turn 的 runtime cutover。输出**等价于现状**。

**整体约束**:
- 不改 prompt 形态（拆分版默认关闭）
- **不**迁移 `ContextCompactor` 主路径（留给 Phase 3）
- **不**触碰 `core/context_compactor.py`（留给 Phase 3）
- **不**切 checkpoint v1 marker（留给 Phase 3）
- **不**做 prompt 形态 A/B（留给 Phase 3）

**拆分原则**: 按验证门分类，而不是按文件目录分类。三个子阶段对应三种独立的验证方式与风险类型：

| 子阶段 | 验证门 | 主导风险类型 |
|--------|--------|--------------|
| 2A | 单元测试 + mock ports | 代码结构风险 |
| 2B | events fixture golden master | 行为等价风险 |
| 2C | snapshot + 集成 + 端到端 session | 运行路径风险 |

---

#### Phase 2A: 内核 + 简单 source + 装配三件套（mock-testable）

**目标**: 建立 `matmaster/context/` 的最小可单测内核与 v3.1 装配三件套。所有新代码对运行时为 **dead code**，不进入任何业务路径。

**新增文件**（按依赖顺序）：

内核类型与渲染原语：

1. `matmaster/context/sections.py`（含 `__post_init__` 校验）
2. `matmaster/context/rendering.py`（含 tag escape）
3. `matmaster/context/turn_context.py`（含 key 唯一性校验）

简单 source（纯函数 / 直接输入，不依赖 events 重放）：

4. `matmaster/context/sources/turn_input.py`
5. `matmaster/context/sources/user_instructions.py`
6. `matmaster/context/sources/compacted_history.py`
7. `matmaster/context/sources/session_jobs.py`（占位，含 `SessionJobsSource.from_jobs`）
8. `matmaster/context/sources/workspace.py`（占位）
9. `matmaster/context/sources/artifacts.py`（占位）

v3.1 装配三件套（核心层）：

10. **`matmaster/context/ports.py`** ← v3.1 新增（`UserInstructions` / `SessionEvent` / `JsonObject` / 三个 Port Protocol / `ContextAssemblyPorts`）
11. **`matmaster/context/compositions.py`** ← v3.1 新增（`ContextCompositionInputs` / `ContextComposition` / step 函数 / 三个 composition 常量 / `_INTENT_COMPOSITION_MAP`）
12. **`matmaster/context/assembly.py`** ← v3.1 新增（`ContextAssemblyIntent` / `TurnAssemblyRequest` / `CompactionAssemblyRequest` / `AssemblyResult` / `ContextAssembler`；`assemble_compaction` 接口可同时落地，但只通过 mock ports 验证，**不**接真实 compactor）
13. **`matmaster/context/turn_intent.py`** ← v3.1 新增（纯函数 `decide_turn_context_intent`）

平台侧：

14. **`src/services/context_assembly_ports.py`** ← v3.1 新增（`AppUserInstructionsPort` / `AppSessionEventsPort` / `AppSessionJobsPort`）
15. **`src/services/context_turn_intent.py`** ← v3.1 新增（`resolve_turn_context_intent` helper，events 查询 + 调纯函数；无 runtime 分流）

**不在 2A 做**:
- `attachments.py` / `skills.py` / `tools.py` / `scanner.py` / `session.py` / `history_restore.py`（依赖 events 重放，留给 2B）
- `system_prompt.py`（与 `ContextBuilder.build_system_prompt` 行为绑定，可放 2C 或单独 PR）
- 任何 manifests / 业务路径 / runtime 切换

**测试目标**:
- Phase 0.5 + Phase 1 测试全部仍通过
- 单元测试覆盖每个简单 source 的 `to_sections`
- 单元测试覆盖 `wrap_tag` escape（含 `</tag>` 注入用例）
- 单元测试覆盖 `from_sources` 的 key 唯一性校验
- 单元测试覆盖 `__post_init__` 不变量校验
- 单元测试覆盖 `ContextComposition.apply` 三个 composition 与 `_INTENT_COMPOSITION_MAP` dispatch
- 单元测试覆盖 `ContextAssembler.assemble_turn` / `assemble_compaction`（mock ports，verify bundle 透传 / Optional port skip / 双视图）
- 单元测试覆盖 `decide_turn_context_intent` 纯函数
- 集成测试覆盖 `resolve_turn_context_intent`（mock events port；无 runtime 分流路径）
- 静态校验：grep `from matmaster.context` 在 `src/services/` 中仅出现在 `context_assembly_ports.py` 和 `context_turn_intent.py` 自身，无其他 runtime caller

---

#### Phase 2B: Session source 迁移与 manifests 等价（fixture-equivalence）

**目标**: 把从 events 重建 session context 的逻辑迁进新 source 和 `SessionContextBuilder`，与旧 manifests 行为**逐 fixture 等价**。仍**不**切运行时主路径。

**新增文件**:
- `matmaster/context/scanner.py`
- `matmaster/context/session.py`（构造参数从 `events: list[dict]` 改为 `events: tuple[SessionEvent, ...]`）
- `matmaster/context/history_restore.py`（Phase 1 接口骨架的完整实现）
- `matmaster/context/sources/attachments.py`
- `matmaster/context/sources/skills.py`
- `matmaster/context/sources/tools.py`

**shim 改造**:
- `matmaster/manifests/*` 改为薄 shim 委托新 source（**必须与新 source 同 PR**，因为 shim 正确性依赖"旧出口 == 新出口"的对照测试）

**测试目标**（重点是 golden master 等价对照，**不是**仅靠普通单测）:
- Phase 2A 测试全部仍通过
- 至少准备以下几类 events fixture，逐组对比旧 manifests 输出与新 source 输出：
  - 普通附件累积（单轮 / 多轮）
  - 多轮 skill 激活 / 变化
  - tool catalog 演化
  - 带 `until_event_id` 的边界截断
  - 带 `spawn_id` 的过滤
  - checkpoint 前后事件混合
  - hash anchor 与 checkpoint 交错
- `SessionContextBuilder(events=tuple[SessionEvent, ...])` 的 `until_event_id` 边界测试
- include / exclude attachments 测试

**验收**:
- 新 session builder 能从 typed events 生成与旧 manifests 等价的 sections
- 生产路径暂时不使用它（仍然继续走旧 manifests 链路）

---

#### Phase 2C: Runtime cutover 与 legacy helper 清理（snapshot + integration）

**目标**: 把普通 user turn 的运行路径切到新 assembler，删除 Phase 1 残留的 legacy injection helper。这是 Phase 2 唯一**会改变生产路径**的子阶段，PR diff 必须保持最小。

**业务代码切换**:
- [matmaster/core/agent.py](../../matmaster/core/agent.py) import 从 `matmaster.manifests` 切到 `matmaster.context`
- [matmaster/core/agent.py:336-347](../../matmaster/core/agent.py:336) kernel 入口改造：用 history 末尾的 UserMessage，不再装配 turn_input
- [src/services/agent_run_service.py](../../src/services/agent_run_service.py) **不再直接装配** `TurnInput` + `UserTurnContext.from_sources`，改为：
  - 装配 `TurnInput`
  - 调 `user_instructions_port.load_user_instructions` 拿 `UserInstructions`
  - 调 `resolve_turn_context_intent` 判定 intent
  - 调 `context_assembler.assemble_turn(intent, request)` 装配
  - 调 `events_service.add_history_event` 写 `user_turn_context`

**AgentRuntimeSpec 注入**（字段演化见 §15）:
- `context_assembler: ContextAssembler`
- `user_instructions_port: UserInstructionsPort`
- `session_events_port: SessionEventsPort`
- `session_jobs_port: SessionJobsPort | None`（Optional）

**Shim 改造**:
- `matmaster/types/current_input.py` re-export `TurnInput`
- 注：`matmaster/types/context.py` 的 shim 化已在 **Phase 0.5** 完成，本阶段不再重复处理 import 环

**清理**:
- 删除 `_apply_user_instructions_to_initial_user_query`
- 清理 `COMPAT:legacy-runtime-injection-helper` 标记

**不在 2C 做**（重申整体约束）:
- `core/context_compactor.py` shim → Phase 3
- 真实 `ContextCompactor` 迁移 → Phase 3
- checkpoint v1 marker 切换 → Phase 3
- prompt 形态 A/B → Phase 3

**Prompt 形态**:
- **沿用现状**: `TurnInstructionSource` 和 `TurnAttachmentsSource` 合并到一个 `<current_instruction>` block（兼容当前 `[Current attachments]` 拼接方式）
- 拆分版可由 `__init__` 参数或 feature flag 切换，但**默认关闭**

**测试目标**:
- Phase 2A + 2B 测试全部仍通过
- Snapshot test: 关键 case 的 prompt 字符串与 Phase 1 末态等价
- 完整 session 写入 + restore 集成测试
- AGENT.md 首轮 / 未变 / 改动后下一轮立即生效
- `user_turn_context` 写入失败 fail-fast
- SSE 不暴露内部事件
- bundle 防竞态：service 读完 AGENT.md 后文件变化，assembler 仍使用传入对象

---

### Phase 3: Compaction 接入 + Prompt 形态决策

**目标**: 把 preflight / runtime compaction 接入新 renderer，切到 v1 marker，做 prompt 形态 A/B。

**前置约束**: Phase 2 期间 `core/context_compactor.py` 维持真实代码不变（不做 shim 化），本阶段才把 compactor 迁移到 `context/compaction.py` 并将原文件 shim 化。Phase 2 / Phase 3 边界严格按"compaction 主路径是否触碰"切分。

**3a. compaction.py 迁移**:
- 把 `core/context_compactor.py` 内容迁到 `context/compaction.py`
- `core/context_compactor.py` 改为薄 shim
- 装配方式从手写字符串改为 `UserTurnContext` + view 投影
- **fallback 保留**（`sliding_window` / `tool_truncation`），但加埋点（命中率、成功率）

**3b. checkpoint payload 切到 v1**:
- compaction sink 写 `schema_version="history_checkpoint.v1"` + `<compacted_history>` marker
- codec 仍接受双 marker（`COMPAT:v0-checkpoint-marker`）

**3c. Prompt 形态 A/B**:
- 在 Phase 2 末或 Phase 3 起手时做 offline eval（见 §6.5 评估维度）
- 通过则启用 `<turn_attachments>` 拆分（默认）
- 不通过则保留合并形态，调整 tag 名后再 A/B

**3d. 测试目标**:
- 压缩前后 `restore_v1` 行为正确
- 多次压缩链路正确
- prompt 形态切换不破坏 tool 调用
- fallback 路径仍可触发并写 ephemeral checkpoint

---

### Phase 4: 清理 + Oversized Input（独立 spec）

**4a. 清理**:
- 删除所有 shim（`matmaster/manifests/`、`matmaster/core/context_builder.py`、`matmaster/core/context_compactor.py`、`matmaster/types/context.py`、`matmaster/types/current_input.py`）
- `AgentRuntimeSpec.context_builder: ContextBuilder` → `system_prompt_builder: SystemPromptBuilder` rename（一次性 PR）
- 测试目录从 `tests/matmaster/manifests/` 迁到 `tests/matmaster/context/`

**4b. v0 兼容性退役**:
- 移除 `COMPAT:v0-checkpoint-marker`: `history_checkpoint_codec.py` 不再接受 v0 marker
- 移除 `COMPAT:v0-restore`: 删除无 `user_turn_context` / v1 checkpoint 时委托 legacy restore 的分支
- 前提：所有线上 session 的最新 checkpoint 已超过 30 天，或产品确认不再恢复旧 session

**4c. Oversized Input 独立 spec**:
- 不在本 spec 范围
- 需要单独设计 `InputSummaryConfig`、原文写盘策略、路径安全、失败处理
- 接口预留点：`ContextCompactor.apply_compaction_plan(summary_override, session_attachments_override)` + `user_turn_context.transform="oversized_summary"` 已就位

**4d. Fallback 删除决策**:
- 基于 Phase 3 埋点的命中率与成功率数据
- 删除决策独立 PR，不在 Phase 4 主线

---

## 15. `ContextBuilder` 拆解 + `AgentRuntimeSpec` 演化

[matmaster/core/context_builder.py](../../matmaster/core/context_builder.py) 中的 `ContextBuilder` 类**废弃**，三段职责拆分：

| 旧方法 | 新位置 |
|--------|--------|
| `build_system_prompt(...)` | `matmaster/context/system_prompt.py` 中的 `SystemPromptBuilder` 类 |
| `build_user_request(...)` | **v3.1**: `matmaster/context/assembly.py` 的 `ContextAssembler.assemble_turn`；底层使用 `compositions.py` (`ANCHOR_COMPOSITION` / `CONTINUATION_COMPOSITION`) + `turn_context.py` (`UserTurnContext.from_sources`，仅 composition 内部调用) + `sources/turn_input.py` |
| `build_compact_bundle(...)` | **v3.1**: `matmaster/context/assembly.py` 的 `ContextAssembler.assemble_compaction`；底层使用 `compositions.py` (`COMPACTED_COMPOSITION`) + `sources/compacted_history.py` |
| `_tag(...)` 等 helper | `matmaster/context/rendering.py` (`wrap_tag`) |

`AgentRuntimeSpec` 字段演化（注：所有 v3.1 新增字段的**实际注入**都发生在 Phase 2C cutover，不在 2A / 2B）：

| 字段 | Phase 1 | Phase 2C (v3.1) | Phase 4 |
|------|---------|------------------|---------|
| `context_builder: ContextBuilder` | 不变 | 退化为 `build_system_prompt` 的 wrapper | 重命名为 `system_prompt_builder: SystemPromptBuilder`，删除 shim |
| `context_assembler: ContextAssembler` | — | **新增**（v3.1） | 保留 |
| `user_instructions_port: UserInstructionsPort` | — | **新增**（v3.1） | 保留 |
| `session_events_port: SessionEventsPort` | — | **新增**（v3.1） | 保留 |
| `session_jobs_port: SessionJobsPort \| None` | — | **新增**（v3.1，Optional） | 保留 |

---

## 16. 测试覆盖

```
tests/matmaster/context/
  test_sections.py
    - empty content filter
    - view filter (RUNTIME / CHECKPOINT)
    - order stable sort
    - __post_init__: invariant RUNTIME ⊇ CHECKPOINT（负向 case）
    - __post_init__: empty key / tag（负向 case）
  test_rendering.py
    - wrap_tag basic
    - wrap_tag escape: content 含 </tag> 时被替换
    - render_sections multi-section ordering
  test_turn_context.py
    - from_sources key 唯一性校验（负向 case）
    - render(view) 输出等价（双视图对比）

  # v3.1 新增
  test_ports.py
    - UserInstructions / SessionEvent / SessionEventQuery / SessionJobs 构造
    - SessionJobs.empty() 返回空 active_jobs
    - JsonObject 类型边界（typed JSON envelope）
  test_compositions.py
    - ContextCompositionInputs 默认值
    - ANCHOR_COMPOSITION.apply 等价于手写 from_sources（snapshot 测试）
    - CONTINUATION_COMPOSITION.apply 等价于手写 from_sources
    - COMPACTED_COMPOSITION.apply 等价于手写 from_sources
    - defer_turn_instruction=True → instruction order = TURN_INSTRUCTION_LAST
    - 空 session_jobs → SessionJobsSource 返回空 sections
    - session_attachments_override → 命中 _step_session_attachments_override
  test_assembly.py
    - ContextAssembler.assemble_turn(ANCHOR_TURN) 调 events port + session_builder
    - ContextAssembler.assemble_turn(CONTINUATION_TURN) 不调 events port（performance 断言）
    - ContextAssembler.assemble_compaction(RUNTIME_COMPACTION) / (PREFLIGHT_COMPACTION)
    - AssemblyResult.user_instructions_text/hash 来自 bundle 透传（mock UserInstructions）
    - 同一 UserInstructions 进入 assembler 后不被二次读取（mock port，assert call_count=0）
    - Optional SessionJobsPort = None → 空 SessionJobs, composition skip
    - intent 类型错误（如 assemble_turn 传 RUNTIME_COMPACTION）→ ValueError
    - 双视图: result.user_turn_context.to_message(RUNTIME) 含 turn_input section,
                                     to_message(CHECKPOINT) 不含
  test_turn_intent.py
    - decide_turn_context_intent: hash 相等 → CONTINUATION_TURN
    - decide_turn_context_intent: hash 不等 → ANCHOR_TURN
    - decide_turn_context_intent: latest_anchor_hash=None → ANCHOR_TURN

  test_compaction.py
    - apply_compaction_plan with turn_input / without
    - summary 成功 → durable
    - summary 失败 → ephemeral + fallback strategy
    - summary_override 旁路
    - user_instructions_text / hash 通过 bundle 传递到 CompactionResult
    - apply_compaction_plan 调 context_assembler.assemble_compaction（不直接拼 source）
  test_session.py
    - build_sections include / exclude attachments
    - until_event_id 边界
    - 构造参数为 tuple[SessionEvent, ...]，从 events: list[dict] 改造后向后兼容
  test_history_restore.py
    - 无 checkpoint + 无 user_turn_context → legacy_restore 委托（`COMPAT:v0-restore`）
    - 无 checkpoint + 有 user_turn_context → v1 路径
    - v1 checkpoint → v1 路径
    - v0 checkpoint → legacy_restore 委托（`COMPAT:v0-restore`）
    - 多 user_turn_context 顺序追加
    - response 与 assistant_state 同 turn 不冲突（不存在该情况，但 defensive）
    - tool_result restore
    - spawn_id 过滤
    - ImageContentPart 嵌套反序列化
  sources/
    test_user_instructions.py
      - empty 返回空 sections
      - hash 计算稳定（同输入同 hash）  ← 现移到 src/services/context_assembly_ports 测试
    test_turn_input.py
      - has_effective_input 边界
      - images_as_parts 转换
    test_attachments.py
      - from_events
      - with_added (Case 3 预留)
    test_skills.py / test_tools.py
      - 从 events 重建 skill 列表
      - allowed_mcp_servers 过滤
    test_session_jobs.py
      - from_jobs 空 → 空 sections
      - from_jobs 含 active_jobs → 渲染 section
    test_compacted_history.py
      - empty summary → 空 section
  integration/
    test_compaction_roundtrip.py
      - Case 1 / 1b / 1c / 2 / 4 端到端
      - 写 user_turn_context → restore_v1 等价
    test_multi_compaction.py
      - 两次压缩链路
    test_codec_v0_v1_compat.py
      - 旧 checkpoint 仍能 restore
      - 新 checkpoint v1 marker 校验通过
    test_agent_md_responsiveness.py
      - 首轮 → 改 AGENT.md → 第 2 轮立即反映
      - 改 AGENT.md → 压缩触发 → anchor 含新内容
      - **bundle 防竞态: service 读 bundle 后改 AGENT.md, assembler 仍用旧 bundle (v3.1)**
    test_sse_filter.py
      - user_turn_context 不出现在 replay
      - assistant_state 不出现在 replay
      - history_checkpoint 不出现在 replay

tests/src/services/
  test_context_assembly_ports.py    ← v3.1 新增
    - AppUserInstructionsPort: size cap truncate + warning
    - AppUserInstructionsPort: 文件不存在 → 空 bundle
    - AppUserInstructionsPort: hash 计算稳定
    - AppSessionEventsPort: 字段映射正确 (id / event_type / source / content / ...)
    - AppSessionEventsPort: limit / order / event_types 过滤
  test_context_turn_intent.py       ← v3.1 新增
    - resolve_turn_context_intent: 无 events → ANCHOR_TURN
    - resolve_turn_context_intent: 找到 anchor + hash 匹配 → CONTINUATION_TURN
    - resolve_turn_context_intent: 找到 anchor + hash 不匹配 → ANCHOR_TURN
    - resolve_turn_context_intent: 找到 history_checkpoint → 取其 hash
    - resolve_turn_context_intent: 扫 50 条仍未找 anchor → ANCHOR_TURN（防御性兜底）
```

Phase 1 内 `tests/matmaster/manifests/` 保留并继续通过（验证 shim 等价性）；Phase 4 删除并迁到 `tests/matmaster/context/`。

---

## 17. 验收要点与风险跟踪

### 已决验收要点

- **`user_turn_context` 与 `User/query` 的关联方式**: v3.2 不保存 `source_query_event_id`，也不要求 `add_history_event` 返回 inserted row id。关联通过 events 表顶层 metadata 完成：`session_id + spawn_id + invocation_id`。`invocation_id` 是一次真实用户请求的标识，足以把 raw transcript 事件与 provider-facing 事件归为同一 turn。该决策避免为了一个 restore 不消费的审计字段，在 API → Redis job → Worker 链路中额外传递 DB 自增 id。
- **`User/query` 与 `user_turn_context` 不强求同事务**：
   - **理由**：v1 restore 路径不消费 User/query（见 §11.1），孤立的 User/query 不污染 provider-facing history，最坏后果仅是前端历史多一条"裸用户消息"；v0 兼容退役（Phase 4）后该后果亦消失
   - **同事务代价过高**：DAO 接口需扩散 connection 参数或引入 `events_service.transaction()` context manager；async 下需保证同一 connection 跨 await 不被其他协程拿走；事务窗口扩大到含 AGENT.md 读 + events 查 + UserMessage 渲染，可能引入连接池占用 / 锁竞争
   - **兜底**：fail-fast + SSE 错误事件 + 后台周期按 `session_id + invocation_id` 扫 `User/query 无对应 user_turn_context` 的比率（目标 < 0.1%，与下方 user_turn_context 写入失败率监控对齐）

### 高优先级

1. **Phase 1 直接切新路径的回归风险**: Phase 1 不引入 runtime 分流开关，新 turn 一律写 `user_turn_context`。上线/联调时需要把风险收敛到测试与兼容读取路径：
   - 监控 user_turn_context 写入失败率（应 < 0.1%）
   - 针对 `COMPAT:v0-restore` 做 restore_v1 vs legacy_restore 的 messages 序列 offline diff
   - 监控 AGENT.md 修改后首轮生效率
   - 用 `rg "COMPAT:"` 跟踪仍存在的兼容逻辑，避免长期遗留

2. **多次压缩 + AGENT.md 变更的边界**: 用户在两次压缩之间多次改 AGENT.md，会生成多条 anchor user_turn_context。如果其中夹杂 continuation，messages 序列中会有多个 anchor。预期 LLM 自然理解，但需要 prompt 评估验证。Phase 1 末做一次小规模 case study。

### 中优先级

3. **`SessionJobsSource` 占位与未来接入的接口契约**: 数据接入留待 bohrium tool job table + hot cache 系统建好。接入时只需修改 `SessionJobsSource.from_jobs(...)`（`SessionJobs` 字段由 `SessionJobsPort` 定义），不动 source 接口和 order 表。

4. **fallback 命中率埋点**: Phase 3 加埋点，30 天后评估是否删 `sliding_window` / `tool_truncation`。在没有数据前**不删**。

5. **prompt cache 命中率**: AGENT.md hash 变化产生新 anchor 会让 prompt prefix 变化，cache miss。当前实现每轮都重写 first user message，cache miss 是已有现实。新设计在 cache 维度**至少不退步**。Phase 3 可加埋点对比。

6. **v3.1 events port 在 service 与 assembler 同时被调用的事务一致性** (新增): 同一 turn 内 `resolve_turn_context_intent` 调用 `SessionEventsPort.load_events`（最近 50 条倒序）后, `ContextAssembler.assemble_turn` 又会调用一次（按 `until_event_id` 升序）. 若 turn 处理时间较长, 中间可能有 background 写入新事件. 需评估:
   - 是否同事务/同 snapshot 读取（强一致 vs 性能）
   - 是否在 Phase 2 落地时把 `assemble_turn` 内的 events 查询挪到 service, 由 service 一次读取后传给 assembler（去掉一次查询）
   - Phase 2 落地时实测查询次数与延迟, 决定是否优化

### 低优先级

7. **`UserTurnContext` 与未来 sub-agent handoff 视图**: 当前 ContextView 只有 RUNTIME / CHECKPOINT。未来 sub-agent 可能需要新视图（如 SUBAGENT_HANDOFF），届时再加。本 spec 不预留。

8. **`schema_version` / `render_version` 演化矩阵**: Phase 1 后只有 v1，未引入 v2。未来 v2 升级时再单独设计 codec 分发表。

9. **`run_meta` 字段整改**: `run_meta` 字典本身仍是 god bag。后续重构应考虑把 `run_meta` 替换为 typed dataclass（如 `AgentRunMeta`），但**不在本次范围内**。

10. **v3.1 ContextAssembler 中 events 查询是否需要预读上一轮 cache** (新增): 长会话时每轮调 `assemble_turn(ANCHOR_TURN)` 都要拉全部 events 重新装配 session sections, 数据量随 turn 数线性增长. 是否在 Phase 2 后单独评估"按 turn 增量缓存 session_sections"; 不在本 spec 范围, 先按全量查实现.

---

## 18. 不在本次范围

- bohrium job table + hot cache 系统的搭建（`SessionJobsSource` 只占位）
- Oversized input offload（Case 3 / Phase 4 独立 spec）
- `run_meta` 整体 typed 化
- LLM provider 抽象层重构
- Tool calling schema 重写
- 前端 chat 历史展示组件改造（仍走现 SSE replay）
- AGENT.md `/reload-agent-md` 显式命令机制
- kernel `assistant_state` 写入条件扩展（自然结束写 assistant_state）
- Sub-agent checkpoint 语义扩展
- Fallback 路径删除（依赖 Phase 3 埋点数据）

---

## 附录 A: 名词对照

| 概念 | 描述 |
|------|------|
| Raw transcript history | 由 User/query / response / tool_result 等原始事件组成的对话流，前端回放与审计的数据源 |
| Model-visible history | 后端发给 LLM 的真实消息序列，由 system prompt + base_messages (from checkpoint) + 后续 user_turn_context / assistant_state / response / tool_result 重建 |
| `user_turn_context` | 新事件类型，每个真实用户 turn 最多一条，记录 provider-facing UserMessage 事实 |
| Anchor | 装配了完整长尾 sources（UserInstructions / SessionContext / TurnInput）的 user message。出现条件：session 首轮 OR AGENT.md hash 变化的轮 OR 压缩触发后的轮 |
| Continuation | 只装配 TurnInput 的 user message，依赖更早 anchor 提供长尾 sections |
| Source | `matmaster/context/sources/` 下的 frozen dataclass，自带 `to_sections() -> tuple[ContextSection, ...]`，互相独立不依赖 |
| Section | `ContextSection` 实例，渲染单元 |
| View | `ContextView`，渲染时的视图选择（RUNTIME / CHECKPOINT），不参与恢复。不变量 `RUNTIME ⊇ CHECKPOINT` |
| Checkpoint | `history_checkpoint` event 的 v1 payload，含 base_messages、user_instructions_text/hash、schema_version、render_version、covered_until_event_id |
| `invocation_id` | events 表顶层字段，一次真实用户请求的标识；`User/query` 与 `user_turn_context` 通过 `session_id + spawn_id + invocation_id` 关联 |
| `pre_turn_history_event_id` | 本轮 User/query 和 user_turn_context 事件写入前的最后 event id，`int` 类型；`0` 表示本轮前 session 内无任何 event（首轮）。用于 `assemble_turn` 的历史 view cutoff 与 preflight compaction 的 checkpoint 覆盖边界。**不**用 `None` 表达"无历史"（避免与 `SessionEventQuery.until_event_id=None` 的"无上界"语义混淆） |
| `user_instructions_hash` | AGENT.md 文本的 sha256，service 层用于判定是否需要新 anchor |
| `transform` | user_turn_context.payload 字段，`"raw"` / `"preflight_compacted"` / `"oversized_summary"`（最后一个 Phase 4 落地）|

### v3.1 新增名词

| 概念 | 描述 |
|------|------|
| Port | `matmaster/context/ports.py` 声明的窄能力 Protocol。返回 typed data carrier / event sequence。不返回核心装配产物 |
| typed 数据载体 | Port 返回的 frozen dataclass，承载从外部数据源读出的值（如 `UserInstructions`、`SessionJobs`、`SessionEvent`）。不使用 "Bundle" / "Snapshot" 后缀避免误导（jobs 不存在拍照行为；instructions 不是 bundle 而是数据本体） |
| `SessionEvent` | DB events 行的 typed envelope。`content: JsonObject`（受限 JSON 类型），不使用 `dict[str, Any]` |
| `JsonObject` / `JsonValue` | 受限 JSON 类型别名（不含 `Any`），用于 typed JSON envelope |
| Composition | `matmaster/context/compositions.py` 声明的 step 元组，决定在某场景下装配哪些 source |
| `ContextComposition` / `ContextCompositionInputs` | Composition 类型与输入载体；`ContextCompositionInputs` 是 composition 内部类型，不作公共 API |
| Step | Composition 内部的纯函数 `(ContextCompositionInputs) -> tuple[ContextSection, ...]` |
| `ContextAssemblyIntent` | enum: `ANCHOR_TURN` / `CONTINUATION_TURN` / `PREFLIGHT_COMPACTION` / `RUNTIME_COMPACTION` |
| `TurnAssemblyRequest` / `CompactionAssemblyRequest` | 两个 typed request 类型，分别给 `assemble_turn` / `assemble_compaction` |
| `AssemblyResult` | 装配结果，含 `user_turn_context: UserTurnContext` + `user_instructions_text/hash` 透传 |
| `ContextAssembler` | 装配执行器，持有 `ContextAssemblyPorts`。不读 AGENT.md、不判 intent、不写事件 |
| Intent resolver | `matmaster/context/turn_intent.py` 的纯函数 + `src/services/context_turn_intent.py` 的 helper（events 查询 + 调纯函数；无 runtime 分流） |

---

## 附录 B: 与现有代码的具体衔接点

本附录列出 v3 实现期间必须改动的具体文件和函数，方便 PR 切分时对照。

### Phase 0 改动

- [matmaster/core/agent.py](../../matmaster/core/agent.py) 文件拆分：compaction wiring / tool dispatch helpers
- [src/services/agent_run_service.py](../../src/services/agent_run_service.py) 文件拆分：instructions loading / history restore wiring / Bohrium rebuild
- [src/services/stream_service.py](../../src/services/stream_service.py) 文件拆分：SSE replay filter helpers

### Phase 0.5 改动

- [matmaster/core/playground.py:26](../../matmaster/core/playground.py:26) 反向 import 循环拆解
- `PlaygroundContext` / `WorkspaceArchivalConfig` 定义归位到 `core/playground.py`
- [matmaster/types/context.py](../../matmaster/types/context.py) 改为薄 re-export shim
- 不动 context assembly 与 runtime behavior

### Phase 1 改动

- [src/services/stream_service.py:66](../../src/services/stream_service.py:66) `_should_emit_event_to_sse`: 加 `user_turn_context` hidden
- `matmaster.integration.event_router.SSEHandler._should_skip()`: 同步加
- [src/services/agent_run_service.py:775-780](../../src/services/agent_run_service.py:775) `_apply_user_instructions_to_initial_user_query`: 从 runtime 主路径移除；如为降低 diff 暂留函数体，标记 `COMPAT:legacy-runtime-injection-helper`
- [src/services/agent_run_service.py:182-209](../../src/services/agent_run_service.py:182) `_apply_user_instructions_to_initial_user_query` 实现: 若暂留，最迟在 Phase 2C cutover 时删除
- [src/services/history_checkpoint_service.py:26-55](../../src/services/history_checkpoint_service.py:26) `build_checkpoint_sink`: payload 加新字段
- [src/services/history_checkpoint_codec.py:89-91](../../src/services/history_checkpoint_codec.py:89) marker 校验: 接受 v0 + v1 双 marker（`COMPAT:v0-checkpoint-marker`）
- [src/dao/chat_events_table.py:327-](../../src/dao/chat_events_table.py:327) `add_history_checkpoint`: content payload 加新字段
- [src/services/history_restore_service.py](../../src/services/history_restore_service.py) 改名 + 内部委托新模块；旧 session legacy restore 标记 `COMPAT:v0-restore`

### Phase 2A 改动

- 新增 `matmaster/context/sections.py` / `rendering.py` / `turn_context.py`
- 新增简单 source: `turn_input.py` / `user_instructions.py` / `compacted_history.py` / `session_jobs.py` / `workspace.py` / `artifacts.py`
- 新增装配三件套: `ports.py` / `compositions.py` / `assembly.py` / `turn_intent.py`
- 新增平台 ports 实现: `src/services/context_assembly_ports.py` / `src/services/context_turn_intent.py`
- 所有新代码对运行时为 dead code，不切 import，不动业务路径

### Phase 2B 改动

- 新增 `matmaster/context/scanner.py` / `session.py` / `history_restore.py`
- 新增 session source: `attachments.py` / `skills.py` / `tools.py`
- [matmaster/manifests/](../../matmaster/manifests/) 整目录改薄 shim 委托新 source（与新 source 同 PR）
- 新增 events fixture golden master 等价对照测试

### Phase 2C 改动

- [matmaster/core/agent.py:336-347](../../matmaster/core/agent.py:336) kernel 入口改造：用 history 末尾的 UserMessage，不再装配 turn_input
- [matmaster/core/agent.py](../../matmaster/core/agent.py) import 从 `matmaster.manifests` 切到 `matmaster.context`
- [src/services/agent_run_service.py](../../src/services/agent_run_service.py) 完整切到新路径
- [matmaster/types/current_input.py](../../matmaster/types/current_input.py) shim（types/context.py 的 shim 已在 Phase 0.5 完成）
- `AgentRuntimeSpec` 注入 `context_assembler` / `user_instructions_port` / `session_events_port` / `session_jobs_port | None`
- 删除 `_apply_user_instructions_to_initial_user_query`，清理 `COMPAT:legacy-runtime-injection-helper`
- **不**触碰 `core/context_compactor.py`（留给 Phase 3）

### Phase 3 改动

- [matmaster/core/context_compactor.py](../../matmaster/core/context_compactor.py) → shim（与真实迁移到 `context/compaction.py` 同阶段完成）
- [matmaster/core/context_builder.py](../../matmaster/core/context_builder.py) → shim
- Checkpoint 写入切到 v1 marker
- prompt 形态 A/B 评估 + 切换

### Phase 4 改动

- 删除所有 shim
- 字段 rename
- `COMPAT:v0-checkpoint-marker` 退役
- `COMPAT:v0-restore` 退役
