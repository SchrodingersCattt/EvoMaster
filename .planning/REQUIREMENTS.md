# Requirements: MatMaster v3.0 Context System Unification

**Defined:** 2026-05-14
**Core Value:** 让"模型可见 user context"的装配只发生在 `matmaster/context/` 内核模块；前端回放、后端续跑、压缩恢复、prompt cache 四个用例从同一份事件流可恢复；AGENT.md 改动下一轮立即生效。

需求依据：`.planning/context-refactor/DESIGN.md` (v3.1，§14 阶段迁移路线 + 附录 B 衔接点)。

## v1 Requirements

### Events DAO（前置改造）

- [ ] **DAO-01**: `src/dao/chat_events_table.py:add_event` 改为 `INSERT ... RETURNING id`，签名返回 `int | None`
- [ ] **DAO-02**: `src/services/events_service.py:add_history_event` 透传 inserted event id 给所有 caller
- [ ] **DAO-03**: 所有 `add_event(` / `add_history_event(` 调用点（`prepare_send_message`、worker entry 等）改为消费返回的 event id，不重新查询数据库

### File Split（前置改造）

- [ ] **SPLIT-01**: `matmaster/core/agent.py`（当前 975 行）拆到 < 800 行/文件：snapshot/checkpoint sink wiring、preflight compaction 装配、tool 调度辅助抽出独立 helper
- [ ] **SPLIT-02**: `src/services/agent_run_service.py`（当前 930 行）拆到 < 800 行/文件：instructions loading、history restore wiring、user-input event 写入、bohrium rebuild 抽出独立 helper
- [ ] **SPLIT-03**: `src/services/stream_service.py`（当前 960 行）拆到 < 800 行/文件：SSE filter 逻辑抽出

### Event Schema（事件语义）

- [ ] **EVT-01**: `ChatEventsTable` 接受 `event_type = "user_turn_context"` 的写入，并保证每个 `source_query_event_id` 至多一条该事件（DESIGN §4.1 #1）
- [ ] **EVT-02**: `stream_service._should_emit_event_to_sse`（src/services/stream_service.py:66）+ `matmaster.integration.event_router.SSEHandler._should_skip()` 同步把 `user_turn_context` / `assistant_state` / `history_checkpoint` 加入 hidden list（DESIGN §4.1 #4）
- [ ] **EVT-03**: `user_turn_context.v1` payload schema 落地（`schema_version` / `kind` ∈ {anchor, continuation} / `transform` ∈ {raw, preflight_compacted, oversized_summary} / `source_query_event_id` 必填 / `user_instructions_hash` / `render_version`），含写入时序约束（5 种场景：普通延续 / hash 变化 / session 首轮 / preflight compaction / runtime compaction，DESIGN §3.4）
- [ ] **EVT-04**: `HistoryCheckpointService.build_checkpoint_sink` payload 扩展为 v1：加 `schema_version` / `render_version` / `user_instructions_text` / `user_instructions_hash`；`chat_events_table.add_history_checkpoint` 接受新字段
- [ ] **EVT-05**: `history_checkpoint_codec.py:89-91` 接受 v0/v1 双 marker（`<previous_session_summary>` + `<compacted_history>`），写入侧本阶段仍输出 v0 marker；标记 `COMPAT:v0-checkpoint-marker`

### AGENT.md Hash Anchor

- [ ] **HASH-01**: service 层 anchor / continuation 判定（首轮 anchor / hash 不变 continuation / hash 变化 anchor / AGENT.md 文件不存在 anchor 但 hash 为空）+ 写入对应 `user_turn_context`，含 fail-fast 错误处理（写入失败本轮终止）；tool 循环内不再写 `user_turn_context`（kernel 不感知该事件）
- [ ] **HASH-02**: AGENT.md 50KB size cap（超限走 truncate + warning，不 fail-fast）+ sha256 hash 计算细节 + `_latest_anchor_user_instructions_hash` 查询（最近 anchor 的 hash）
- [ ] **HASH-03**: `src/services/agent_run_service.py:182-209` 的 `_apply_user_instructions_to_initial_user_query` 实现从 runtime 主路径移除；如本阶段为降低 diff 暂留函数体，标记 `COMPAT:legacy-runtime-injection-helper` 并确保无 runtime caller，Phase 3 前删除

### Model History Restore

- [ ] **RESTORE-01**: `src/services/history_restore_service.py` 改名为 `model_history_restore_service.py`，内部委托新 `matmaster/context/history_restore.py:ModelHistoryRestorer` 实现的 schema-aware 分流
- [ ] **RESTORE-02**: v0 路径委托 `ChatHistoryConverter.events_to_dialog_messages`，标记 `COMPAT:v0-restore`；v0 checkpoint 仍能通过 codec 校验
- [ ] **RESTORE-03**: v1 路径正确消费 `user_turn_context` + `assistant_state` + `response`/`run_result` + `tool_result`，含 ImageContentPart 嵌套反序列化、`spawn_id` 过滤、多次压缩链路（checkpoint `covered_until_event_id` 边界正确）

### Context 内核（matmaster/context/）

- [ ] **CTX-01**: 新建 `matmaster/context/sections.py`：`ContextSection` / `ContextView` / `SectionOrder`，含 `__post_init__` 校验（RUNTIME ⊇ CHECKPOINT 不变量、empty key/tag 负向）
- [ ] **CTX-02**: 新建 `matmaster/context/rendering.py`：`wrap_tag`（对用户可控内容做最小 escape，防止 `</tag>` 注入破坏 section 边界）+ `render_sections`
- [ ] **CTX-03**: 新建 `matmaster/context/turn_context.py`：`UserTurnContext` 聚合根 + `from_sources` 校验 section `key` 唯一性，冲突时 raise；`from_sources` 仅 `ContextRecipe.apply()` 调用
- [ ] **CTX-04**: 新建 `matmaster/context/session.py`：`SessionContextBuilder` 从 `SessionEvent` 序列装配 session-level sections；构造参数从 `events: list[dict]` 改为 `events: tuple[SessionEvent, ...]`
- [ ] **CTX-05**: 新建 `matmaster/context/system_prompt.py`：`SystemPromptBuilder`（从旧 `ContextBuilder.build_system_prompt` 抽出）
- [ ] **CTX-06**: 新建 `matmaster/context/history_restore.py`：`ModelHistoryRestorer` 完整实现（DI 注入 events 访问，Phase 2 接口骨架的完整实现）
- [ ] **CTX-07**: 新建 `matmaster/context/scanner.py`：从 `matmaster/manifests/scanner.py` 迁移的底层 events 扫描工具

### Source 模块（matmaster/context/sources/）

- [ ] **SRC-01**: `sources/user_instructions.py`：`UserInstructionsSource` + `to_sections`
- [ ] **SRC-02**: `sources/turn_input.py`：`TurnInstructionSource` / `TurnAttachmentsSource` / `TurnInput`（含 `has_effective_input` 边界、`images_as_parts` 转换、`source_query_event_id` 必填）
- [ ] **SRC-03**: `sources/attachments.py`：`from_events` + `with_added`（Case 3 预留）
- [ ] **SRC-04**: `sources/skills.py` + `sources/tools.py` — active tools/skills（`tools.py` 替代 `mcp.py`，第一阶段保留 `mcp.py` shim）
- [ ] **SRC-05**: `sources/compacted_history.py`：`CompactedHistorySource` + `to_sections`
- [ ] **SRC-06**: 三个占位 source — `sources/session_jobs.py`（含 `SessionJobsSource.from_jobs`）+ `sources/workspace.py` + `sources/artifacts.py`；空数据应正确返回空 sections

### Assembly 三件套（matmaster/context/ + src/services/ Port 实现）

- [ ] **ASM-01**: 新建 `matmaster/context/ports.py`：typed 数据载体（`UserInstructions` / `SessionEvent` / `SessionEventQuery` / `SessionJobs`）+ 受限 JSON 类型 `JsonObject` / `JsonValue`（不含 `Any`）+ 三个 Port Protocol（`UserInstructionsPort` / `SessionEventsPort` / `SessionJobsPort | None`）+ `ContextAssemblyPorts` 组合
- [ ] **ASM-02**: 新建 `matmaster/context/recipes.py`：`ContextRecipeInputs`（recipe 内部输入类型，不作公共 API）+ `ContextRecipe` + step 函数 + `ANCHOR_RECIPE` / `CONTINUATION_RECIPE` / `COMPACTED_RECIPE` 三个常量 + `_INTENT_RECIPE_MAP` dispatch
- [ ] **ASM-03**: 新建 `matmaster/context/assembly.py`：`ContextAssemblyIntent` enum（`ANCHOR_TURN` / `CONTINUATION_TURN` / `PREFLIGHT_COMPACTION` / `RUNTIME_COMPACTION`）+ `TurnAssemblyRequest` / `CompactionAssemblyRequest` / `AssemblyResult` + `ContextAssembler.assemble_turn` / `assemble_compaction`（不判 intent、不读 hash、不处理迁移兼容、不写事件）
- [ ] **ASM-04**: 新建 `matmaster/context/turn_intent.py`：纯函数 `decide_turn_context_intent(latest_anchor_hash, current_hash)`（不读 events、不知迁移兼容策略）
- [ ] **ASM-05**: 新建 `src/services/context_assembly_ports.py`：`AppUserInstructionsPort` / `AppSessionEventsPort` / `AppSessionJobsPort` 实现 `matmaster/context/ports.py` 的 Protocol；AGENT.md 路径约定、size cap、hash 计算迁入 port 实现；Port 不返回 service 对象、不返回 `ContextSection`/`UserMessage`/`UserTurnContext`、不返回已装配 sections
- [ ] **ASM-06**: 新建 `src/services/context_turn_intent.py`：`resolve_turn_context_intent(events_port, ...)` helper（events 查询 + 调 `decide_turn_context_intent` 纯函数；无 runtime 分流）
- [ ] **ASM-07**: `AgentRuntimeSpec` 注入新字段：`context_assembler: ContextAssembler` + `user_instructions_port: UserInstructionsPort` + `session_events_port: SessionEventsPort` + `session_jobs_port: SessionJobsPort | None`

### Service / Core 路径切换 + Shim 化

- [ ] **SHIM-01**: `matmaster/manifests/*` 整目录改为薄 shim 委托新 source
- [ ] **SHIM-02**: `matmaster/types/current_input.py` 改为 shim re-export `TurnInput`
- [ ] **SHIM-03**: `matmaster/types/context.py` 改为 shim re-export `PlaygroundContext` / `WorkspaceArchivalConfig`；拆解 `matmaster/core/playground.py:26` 与 `types/context.py` 间的反向 import 循环
- [ ] **SHIM-04**: `matmaster/core/agent.py` import 从 `matmaster.manifests` 切到 `matmaster.context`；kernel 入口（agent.py:336-347）改造为使用 history 末尾的 UserMessage，不再装配 turn_input；`core/context_builder.py` 退化为 `SystemPromptBuilder` 的 wrapper
- [ ] **SHIM-05**: `src/services/agent_run_service.py` 完整切到新路径：装配 TurnInput → `user_instructions_port.load_user_instructions` → `resolve_turn_context_intent` → `context_assembler.assemble_turn(intent, request)` → `events_service.add_history_event` 写 `user_turn_context`；不再直接调 `UserTurnContext.from_sources` 或拼装 `Source(...)`
- [ ] **SHIM-06**: 删除 `_apply_user_instructions_to_initial_user_query`（如 HASH-03 暂留 `COMPAT:legacy-runtime-injection-helper`，此处必须删除）

### Compaction 接入

- [ ] **CMP-01**: `matmaster/core/context_compactor.py` 内容迁到 `matmaster/context/compaction.py`，原文件保留为薄 shim
- [ ] **CMP-02**: `ContextCompactor.apply_compaction_plan` 装配方式改为 `context_assembler.assemble_compaction`（COMPACTED_RECIPE）；不直接拼 source；ContextCompactor 不知 compacted context 包含哪些 source（DESIGN §4.2 #4）
- [ ] **CMP-03**: compaction sink 写入切到 `schema_version="history_checkpoint.v1"` + `<compacted_history>` marker；codec 仍接受双 marker（`COMPAT:v0-checkpoint-marker`）
- [ ] **CMP-04**: fallback (`sliding_window` / `tool_truncation`) 保留，加埋点（命中率、成功率），写 ephemeral checkpoint 时记 `failure_reason`；本 milestone 不删 fallback

### Prompt 形态 A/B

- [ ] **PROMPT-01**: `TurnInstructionSource` + `TurnAttachmentsSource` 拆分功能 flag 实现（`__init__` 参数或 feature flag），默认关闭（沿用现状 `<current_instruction>` 合并形态）
- [ ] **PROMPT-02**: prompt 形态 offline eval（按 DESIGN §6.5 评估维度），在 Phase 3 末或 Phase 4 起手时执行
- [ ] **PROMPT-03**: A/B 决策记录与切换：通过则启用 `<turn_attachments>` 拆分（默认开）；不通过保留合并形态，调整 tag 名后再 A/B

### 清理 + 退役

- [ ] **CLEAN-01**: 删除 `matmaster/manifests/` / `matmaster/core/context_builder.py` / `matmaster/core/context_compactor.py` / `matmaster/types/context.py` / `matmaster/types/current_input.py` 全部 shim
- [ ] **CLEAN-02**: `AgentRuntimeSpec.context_builder: ContextBuilder` → `system_prompt_builder: SystemPromptBuilder` rename（一次性 PR）
- [ ] **CLEAN-03**: 测试目录迁移 `tests/matmaster/manifests/` → `tests/matmaster/context/`
- [ ] **CLEAN-04**: `PlaygroundContext` / `WorkspaceArchivalConfig` 从 shim 迁回 `matmaster/core/playground.py`（注意：是迁回已有文件，不是新建；处理反向 import 循环）
- [ ] **CLEAN-05**: 移除 `COMPAT:v0-checkpoint-marker`（`history_checkpoint_codec.py` 不再接受 v0 marker）+ `COMPAT:v0-restore`（删除无 `user_turn_context` / v1 checkpoint 时委托 legacy restore 的分支）；前提：活跃 session 已迁移或产品确认不再恢复旧 session（>30 天观察窗口）

## v2 Requirements

Deferred to future milestones. Tracked but not in v3.0 roadmap.

### Oversized Input（Case 3，独立 spec）

- **OVRS-01**: `InputSummaryConfig` 设计与落地
- **OVRS-02**: 原文写盘策略（路径安全、容量限制、清理策略）
- **OVRS-03**: 失败处理与回滚
- **OVRS-04**: `user_turn_context.transform="oversized_summary"` 真实启用（v3.0 仅预留字段）

### Bohrium Job Table + Hot Cache

- **JOBS-01**: bohrium tool job table schema 设计
- **JOBS-02**: Hot cache 系统接入 `SessionJobsPort`
- **JOBS-03**: `SessionJobsSource.from_jobs(...)` 数据实接入（v3.0 仅占位）

### Fallback 删除

- **FBDR-01**: 基于 Phase 4 埋点的命中率/成功率评估
- **FBDR-02**: 删除 `sliding_window` / `tool_truncation` fallback（独立 PR）

### 其他延后项

- **FUT-01**: `run_meta` typed dataclass 整改（替换 god bag dict）
- **FUT-02**: LLM provider 抽象层重构
- **FUT-03**: Tool calling schema 重写
- **FUT-04**: AGENT.md `/reload-agent-md` 显式命令机制
- **FUT-05**: Kernel `assistant_state` 写入条件扩展
- **FUT-06**: Sub-agent checkpoint 语义扩展（`spawn_id` child checkpoint）
- **FUT-07**: 长会话 session_sections 按 turn 增量缓存（DESIGN §17 风险 #11）

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| 前端 chat 历史展示组件改造 | 仍走现 SSE replay + `ChatHistoryConverter`，前端无感知 |
| Sub-agent handoff 新 ContextView | 当前 ContextView 只有 RUNTIME / CHECKPOINT；未来 sub-agent 需要 SUBAGENT_HANDOFF 时再加，本 spec 不预留（DESIGN §17 风险 #8） |
| `schema_version` / `render_version` v2 codec 分发表 | Phase 2 后只有 v1，未引入 v2；未来 v2 升级时再单独设计（DESIGN §17 风险 #9） |
| 长会话 events 查询缓存优化 | Phase 3 落地时实测查询次数与延迟，决定是否优化；不在本 spec 范围（DESIGN §17 风险 #11） |
| v2.x milestone 历史回填到顶层 `MILESTONES.md` | 旧 milestone 已审计归档在 `.planning/milestones/v2.x-MILESTONE-AUDIT.md`，回填工作收益低；GSD 框架以 `--reset` 启动 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DAO-01 | TBD | Pending |
| DAO-02 | TBD | Pending |
| DAO-03 | TBD | Pending |
| SPLIT-01 | TBD | Pending |
| SPLIT-02 | TBD | Pending |
| SPLIT-03 | TBD | Pending |
| EVT-01 | TBD | Pending |
| EVT-02 | TBD | Pending |
| EVT-03 | TBD | Pending |
| EVT-04 | TBD | Pending |
| EVT-05 | TBD | Pending |
| HASH-01 | TBD | Pending |
| HASH-02 | TBD | Pending |
| HASH-03 | TBD | Pending |
| RESTORE-01 | TBD | Pending |
| RESTORE-02 | TBD | Pending |
| RESTORE-03 | TBD | Pending |
| CTX-01 | TBD | Pending |
| CTX-02 | TBD | Pending |
| CTX-03 | TBD | Pending |
| CTX-04 | TBD | Pending |
| CTX-05 | TBD | Pending |
| CTX-06 | TBD | Pending |
| CTX-07 | TBD | Pending |
| SRC-01 | TBD | Pending |
| SRC-02 | TBD | Pending |
| SRC-03 | TBD | Pending |
| SRC-04 | TBD | Pending |
| SRC-05 | TBD | Pending |
| SRC-06 | TBD | Pending |
| ASM-01 | TBD | Pending |
| ASM-02 | TBD | Pending |
| ASM-03 | TBD | Pending |
| ASM-04 | TBD | Pending |
| ASM-05 | TBD | Pending |
| ASM-06 | TBD | Pending |
| ASM-07 | TBD | Pending |
| SHIM-01 | TBD | Pending |
| SHIM-02 | TBD | Pending |
| SHIM-03 | TBD | Pending |
| SHIM-04 | TBD | Pending |
| SHIM-05 | TBD | Pending |
| SHIM-06 | TBD | Pending |
| CMP-01 | TBD | Pending |
| CMP-02 | TBD | Pending |
| CMP-03 | TBD | Pending |
| CMP-04 | TBD | Pending |
| PROMPT-01 | TBD | Pending |
| PROMPT-02 | TBD | Pending |
| PROMPT-03 | TBD | Pending |
| CLEAN-01 | TBD | Pending |
| CLEAN-02 | TBD | Pending |
| CLEAN-03 | TBD | Pending |
| CLEAN-04 | TBD | Pending |
| CLEAN-05 | TBD | Pending |

**Coverage:**
- v1 requirements: 55 total
- Mapped to phases: 0（pending roadmap）
- Unmapped: 55 ⚠️

---
*Requirements defined: 2026-05-14*
*Last updated: 2026-05-14 after initial v3.0 definition*
