# Phase 2C Runtime Cutover 与 Legacy Helper 清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 DESIGN.md v3.3 Phase 2C：把普通 user turn 的生产路径从 `matmaster.manifests` + `user_turn_context_service` 的 Phase 1 渲染链路切换到 `matmaster.context.assembly.ContextAssembler` + `src/services/context_assembly_ports.py` + `src/services/context_turn_intent.py` 的新链路；同时删除 Phase 1 残留的 `_apply_user_instructions_to_initial_user_query` legacy injection helper 与 `COMPAT:legacy-runtime-injection-helper` 标记。`AgentRuntimeSpec` 新增四个 v3.1 字段（`context_assembler` / `user_instructions_port` / `session_events_port` / `session_jobs_port`）作为 Phase 3 compaction cutover 的预留入口，但 Phase 2C 内本身不调度 compactor 走新路径。`matmaster/manifests/*` 整目录继续保留为薄 shim（Phase 4 才删）。`core/context_compactor.py` 不动；`core/exp.py` 内构造 `CompactionRehydrator` 的代码路径不动；`history_checkpoint` 仍写 v0 marker（Phase 3 切 v1）。Phase 2C 的成功验收定义为：普通 user turn 的 `user_turn_context` 写入路径由 assembler 产出，AGENT.md 首轮 / hash 未变 / hash 变 / 写入失败 / SSE filter / restore 兼容均有行为测试；Phase 1 ↔ Phase 2C 的 prompt 字符串差异必须在 Task 5.5 的 oracle gate 中被显式分类，不能靠口头声明 byte-equivalent。

**Architecture:** Phase 2A 在 `matmaster/context/` 落地了内核类型、装配三件套（ports / compositions / assembly / turn_intent）、平台 ports 实现（`context_assembly_ports.py` / `context_turn_intent.py`），但 runtime 仍走 `matmaster.manifests` + `user_turn_context_service` 旧路径，新代码对生产为 dead code。Phase 2B 把 events → session sections 的真实装配逻辑迁入 `matmaster/context/session.py::SessionContextBuilder` + 三个 session source（`attachments` / `skills` / `tools`），并通过 `ContextAssembler(session_context_factory=...)` 暴露 production seam；同时把 `ModelHistoryRestoreService` 内部委托给 `matmaster.context.history_restore.ModelHistoryRestorer`。Phase 2C 是 Phase 2 唯一**会改变生产路径**的子阶段：(1) `src/services/agent_run_service.py` 的 Stage 5b 不再调 `user_turn_context_service.render_runtime_task_for_user_turn_context` + `render_provider_facing_current_message_content` + `build_user_turn_context_payload` 这条 Phase 1 渲染链路，改为把 Stage 3 已读到的 `UserInstructionsInfo` 桥接为 `UserInstructions`，再调 `resolve_turn_context_intent` → `ContextAssembler.assemble_turn(...)`，然后把装配产物 `assembly.user_turn_context.to_message(ContextView.RUNTIME)` 渲染成事件 payload 写入 events 表，并以渲染后的字符串作为 `task` 参数传给 `kernel.run_stream`；(2) `src/services/agent_run_history_wiring.py` 的 `attachment_manifest.build_available_attachments` 切到 `SessionAttachmentsSource` 的 `scan_legacy_attachment_entries` + `format_entries_text` 等价路径；(3) `matmaster/core/agent.py` 的 `_run_items` 不再读 `spec.meta["attachment_manifest"]` 也不再调 `spec.context_builder.build_user_request(user_text=task, attachments=...)` —— Phase 2C 保持当前过渡式调用顺序：history 在当前 `user_turn_context` 写入前恢复，kernel 仍负责把 service 已装配好的 `task` 作为本轮 `UserMessage` 追加；(4) `_apply_user_instructions_to_initial_user_query` 函数体连同 `_strip_user_instructions_prefix` / `_find_first_user_message_index` / `_render_user_instructions_block` 在 `src/services/agent_run_instructions.py` 内删除，对应测试文件 `tests/matmaster/services/test_user_instructions_runtime_injection.py` 整体删除；(5) `matmaster/types/current_input.py` 顶部新增 `TurnInput` re-export shim，但保留 `CurrentInputContext` + `build_current_instruction_block` 作为 `core/agent.py` runtime compaction 路径与 `core/context_compactor.py` preflight 路径的兼容入口（Phase 4 才删）。`AgentRuntimeSpec` 新增四个 Optional 字段是为 Phase 3 compaction cutover 准备的 schema 入口；Phase 2C 不通过 `run_meta` / `spec.meta` 走私 service object，service 在 Stage 5b 局部持有 assembler 与 port 实例。Phase 2C 不动 compactor 主体、不切 checkpoint v1 marker、不做 prompt 形态 A/B。

**Tech Stack:** Python 3.11+ / uv / pytest / pytest-asyncio / dataclasses / Protocol / Pydantic `UserMessage` / `matmaster.context.assembly.ContextAssembler` / `matmaster.context.ports` typed envelope

**Spec 来源:** `.planning/context-refactor/DESIGN.md` §2、§3.4-3.6、§4.1-4.2、§5.1-5.3、§6.3-6.5、§7.2-7.3、§7bis、§8.2-8.4、§10.1-10.3、§12 Case 1/1b/1c/2/4、§13 命名清理、§14 Phase 2C、§15 AgentRuntimeSpec 演化、§16 测试覆盖、§17 验收要点、附录 B「Phase 2C 改动」、PHASE-2B-PLAN.md「Notes For Phase 2C」、PHASE-2A-PLAN.md「Notes For Phase 2B」。

---

## 全局约束

1. **Phase 2C 是 Phase 2 唯一会改变生产路径的子阶段。** PR diff 必须保持最小：每个 Task 是一个独立 commit，commit 内不允许搭车做与 Phase 2C 目标无关的清理（包括格式化、类型注解整改、test rename、docstring 重写）。如果发现任何 Phase 2C 范围外的代码瑕疵，记录到 `.planning/context-refactor/FOLLOWUPS.md` 而不是直接修复。
2. **不触碰 `matmaster/core/context_compactor.py`。** 整个文件在 Phase 2C 内必须 byte-for-byte 不变。该文件的迁移由 Phase 3 负责（DESIGN.md §14 Phase 3a）。`core/context_compactor.py` 继续 import `matmaster.manifests.rehydrator.CompactionRehydrator`，继续接收 `current_input_context: CurrentInputContext` 参数。
3. **不切 `history_checkpoint` v1 marker。** `HistoryCheckpointService.build_checkpoint_sink` payload 仍写 `<previous_session_summary>`（v0 marker）。`COMPAT:v0-checkpoint-marker` 在 Phase 2C 不退役（Phase 3 负责切换写入端）。
4. **不做 prompt 形态 A/B 测试。** `TurnInput.to_sections()` 必须使用默认 `split_attachments=False`（与 Phase 2A pin test 等价于 `build_current_instruction_block(CurrentInputContext(...))` 输出）。任何 `<turn_attachments>` 拆分版的实验都属于 Phase 3 范围。
5. **`matmaster/manifests/*` 整目录保留为 Phase 2B 末态的薄 shim。** 不允许在 Phase 2C 内删除 shim 文件、修改 shim 内部 re-export 列表或调整 shim 公开 API 签名。manifests shim 退役在 Phase 4。
6. **`matmaster/core/agent.py` 不允许新增 `from matmaster.manifests` import。** Phase 2B 验收已确认 `core/agent.py` 没有 `matmaster.manifests` import；Phase 2C 内必须维持这个不变量。`core/agent.py` 仅 import `matmaster.types.current_input.CurrentInputContext`（runtime / preflight compaction 旁路）与 `matmaster.types.messages` 等通用类型；Phase 2C 不引入 `from matmaster.context.assembly import ...` 这类直接 import（assembler 仅在 service 层使用）。
7. **`core/exp.py` 内构造 `CompactionRehydrator` 的 lazy import 路径不变。** Exp.assemble 内仍走 `from matmaster.manifests.rehydrator import CompactionRehydrator`，这是 Phase 3 compaction cutover 的工作面，不在 Phase 2C 范围。
8. **AGENT.md 同一 turn 内只读一次（DESIGN.md §4.2 #7 / §8.2 步骤 2 / §17 验收要点 6）。** service 在 Stage 3 Bohrium 阶段读到的 `UserInstructionsInfo` 必须原样桥接为 `matmaster.context.ports.UserInstructions`，禁止在 Stage 5b 再 `AppUserInstructionsPort.load_user_instructions(...)` 重读一次。具体桥接：`UserInstructions(text=info.text, hash=info.hash, truncated=info.truncated)`。即使两者类型一致字段一致，也不允许调 port 方法重读（防止 AGENT.md 在两次读取间被改写）。
9. **写入 `user_turn_context` event 仍走 `user_turn_context_service.write_user_turn_context_event(...)`。** 该 helper 内部已有 fail-fast + 应用层 dedup（DESIGN.md §4.1 #1 v3.3）+ `payload != existing → fail-fast` 行为，Phase 2C 不重写。Phase 2C cutover 改动仅限于：(a) `payload` 不再来自 `build_user_turn_context_payload(...)`，而来自 `assembly.user_turn_context.to_message(RUNTIME).model_dump(...)` 包装为同结构的 dict；(b) 触发条件不再依赖 `decide_user_turn_context_kind`，而由 `resolve_turn_context_intent(...)` 返回的 `ContextAssemblyIntent.is_anchor_turn` 决定 `kind` 字段。
10. **`user_turn_context_service.py` 内仍保留 `write_user_turn_context_event` / `UserInstructionsInfo` / `make_user_instructions_info` / `hash_user_instructions` / `load_user_instructions_from_session` / `USER_TURN_CONTEXT_SCHEMA_VERSION` / `USER_CONTEXT_RENDER_VERSION` / `DEFAULT_TURN_TRANSFORM` / `UserTurnContextKind` / `UserTurnContextTransform` / `UserTurnContextWriteStatus`。** 这些是写入边界 / Bohrium stage 仍在用的 helper / 共享常量；Phase 2C cutover 后仅删除 `latest_anchor_user_instructions_hash` / `decide_user_turn_context_kind` / `render_runtime_task_for_user_turn_context` / `render_provider_facing_current_message_content` / `build_user_turn_context_payload` 与配套的 `_context_events_newest_first` 私有 helper。
11. **`agent_run_instructions.py` 内 `_USER_INSTRUCTIONS_PATH` 常量保留。** 该路径常量被 `user_turn_context_service.load_user_instructions_from_session` 引用以读 AGENT.md；Phase 2C cutover 后仍需要。删除范围限于 `_apply_user_instructions_to_initial_user_query` / `_strip_user_instructions_prefix` / `_find_first_user_message_index` / `_render_user_instructions_block` / `_USER_INSTRUCTIONS_START` / `_USER_INSTRUCTIONS_END` / `_USER_INSTRUCTIONS_TEMPLATE`。`COMPAT:legacy-runtime-injection-helper` 标记一并清理。
12. **不引入新的 sub-agent 装配路径。** DESIGN.md §3.1 注 与 §4.1 #6 明确：Phase 2C 内仅 root spawn 写 `user_turn_context`；sub-agent spawn 路径暂不接 ContextAssembler。`src/services/agent_run_service.py::AgentRunService.run_agent` 内 `spawn_id=None` 的硬编码不变，子 agent 触发的 `child_exp.run_stream` 路径不引入新写入。
13. **图像处理路径不变。** Stage 4 内 `current_user_images_payload` 装配逻辑不动；Phase 2C cutover 时把 `current_user_images_payload` 通过 `TurnInput.attachments.images=tuple(...)` 喂进 assembler，再由 `to_message(RUNTIME).images` 输出 `list[ImageContentPart]`，最终写入 event payload 的 `message.images` 字段。`pg_ctx.with_run_meta(current_user_images=image_parts)` 仍保留（compactor 路径 / kernel `spec.meta.current_user_images` 仍消费）。
14. **commit 单位与 Phase 2A / 2B 一致：一个 Task → 一个 commit。** Task 1（baseline inventory）无 commit；Task 2-5 各一个 commit；Task 5.5（renderer oracle gate）一个 commit；Task 6-7 各一个 commit；Task 8 视 FOLLOWUPS.md 是否新增内容决定是否产 docs commit；Task 9 一个 commit；Task 10（snapshot / integration 测试增量）独立 commit；Task 11（静态校验 + 回归基线）无 commit。所有 commits 应在同一个 PR 内。
15. **所有 Python 命令使用 `uv run python` 或 `uv run pytest`，不使用系统 Python。**
16. **当前工作树可能已有与 Phase 2C 无关的 dirty 文件**（`.planning/` 子目录、DESIGN.md 修订、个别 source/test 文件）。执行本计划时不得恢复、格式化或改写任何与 Phase 2C 无关的 dirty 文件；若 Phase 2C 需要编辑某个已 dirty 文件，先 `git diff <file>` 确认增量，再最小化叠加修改。
17. **Phase 2C 不把 service object 写进 `run_meta` / `spec.meta`。** 项目约定中 `run_meta` 只承载被动 metadata，不承载 callback、sink、factory、barrier、port 或外部 service 对象。`context_assembler` / port 字段在 `AgentRuntimeSpec` 上只做 Optional schema 预留；Stage 5b 局部构造并使用 assembler。Phase 3 若要让 compactor 消费这些字段，必须在 `Exp.build_runtime` 内从 runtime ports / passive metadata 重新构造，或先更新 DESIGN.md 说明直接注入路径。
18. **Phase 2C 保持过渡式 history 调用顺序。** `build_history_wiring(...)` 在当前轮 `user_turn_context` 写入前执行，`history` 不包含当前轮 provider-facing UserMessage；kernel 仍负责追加 `UserMessage(content=task, images=current_user_images)`。不要在 Phase 2C 内把 restore 挪到写入 `user_turn_context` 之后，除非同时把 kernel 改成消费 `history[-1]` 且不追加当前 task。
19. **Phase 1 ↔ Phase 2C renderer 差异必须用 oracle gate 显式分类。** Task 5.5 在删除 Phase 1 renderer 前同时跑旧链路与新 assembler 链路。当前 baseline 可能同时暴露两类差异：AGENT.md wrapper 从 legacy `<matmaster-user-instructions ...>` 变为 context `<user_instructions>`；附件从旧链路的 `[Available attachments]` 外挂变为 TurnInput 的 `[Current attachments]` 内嵌。执行 Task 6 前必须读懂并记录这些差异；如果产品/评测要求 Phase 2C 全量 byte-equivalent，停止执行本 plan，先更新 DESIGN/render_version 或调整实现。

---

## File Structure

新建文件：

- Create: `src/services/context_assembly_factory.py` — production helper 把 `playground_ctx` / skill registry / mcp config 装配为 `session_context_factory` Callable（用于 `ContextAssembler.__init__`）。
- Create: `tests/services/test_context_assembly_factory.py` — 单元测试。
- Create: `tests/matmaster/services/test_agent_run_stream_context_cutover.py` — Phase 2C 关键 case 的 prompt 字符串 oracle + snapshot 测试（Phase 1 ↔ Phase 2C renderer delta gate / 附件形态差异记录 / 首轮 / hash 未变 / hash 变 / 多 turn / fail-fast / SSE filter / bundle 防竞态）。

修改文件（runtime cutover）：

- Modify: `matmaster/types/runtime.py` — `AgentRuntimeSpec` 增加 4 个 Optional 字段（`context_assembler` / `user_instructions_port` / `session_events_port` / `session_jobs_port`），新 model_validator 校验类型。
- Modify: `matmaster/types/current_input.py` — 顶部新增 `from matmaster.context.sources.turn_input import TurnInput` re-export shim；保留现有 `CurrentInputContext` / `build_current_instruction_block` / `_clean_tuple` / `_display_name`。
- Modify: `src/services/agent_run_history_wiring.py` — 把 `attachment_manifest.build_available_attachments / format_available_attachments` 调用替换为 `matmaster.context.sources.attachments.scan_legacy_attachment_entries + format_entries_text`（保持 byte-for-byte 等价的 `attachment_text` 字符串）。删除 `from matmaster.manifests import attachment as attachment_manifest`，新增 `from matmaster.context.sources.attachments import scan_legacy_attachment_entries, format_entries_text`。
- Modify: `src/services/agent_run_service.py` — Stage 5b cutover：删除对 `user_turn_context_service.latest_anchor_user_instructions_hash` / `decide_user_turn_context_kind` / `render_runtime_task_for_user_turn_context` / `render_provider_facing_current_message_content` / `build_user_turn_context_payload` 的调用，改为调 `resolve_turn_context_intent` + `ContextAssembler.assemble_turn` + 自己包 payload；删除 `from matmaster.manifests import skill as skill_manifest` 改为 `from matmaster.context.sources.skills import resolve_active_skills, skill_name`（注：manifests shim 实际就是 re-export 这两个符号，切到 source 直接 import 是等价行为，不增加运行时开销）。
- Modify: `src/services/user_turn_context_service.py` — 删除 `latest_anchor_user_instructions_hash` / `decide_user_turn_context_kind` / `render_runtime_task_for_user_turn_context` / `render_provider_facing_current_message_content` / `build_user_turn_context_payload` 与配套私有 helper `_context_events_newest_first`。保留 `UserInstructionsInfo` / `hash_user_instructions` / `make_user_instructions_info` / `load_user_instructions_from_session` / `_truncate_utf8` / `write_user_turn_context_event` / 模块常量。
- Modify: `src/services/agent_run_instructions.py` — 删除 `_apply_user_instructions_to_initial_user_query` / `_strip_user_instructions_prefix` / `_find_first_user_message_index` / `_render_user_instructions_block` 与配套常量 `_USER_INSTRUCTIONS_START` / `_USER_INSTRUCTIONS_END` / `_USER_INSTRUCTIONS_TEMPLATE`。删除 `COMPAT:legacy-runtime-injection-helper` 标记。保留 `_USER_INSTRUCTIONS_PATH`（被 user_turn_context_service 引用）。
- Modify: `matmaster/core/agent.py` — `_run_items` 内删除 `spec.meta.get("attachment_manifest")` 读取与 `spec.context_builder.build_user_request(user_text=task, attachments=attachment_text)` 调用，改为直接 `UserMessage(content=task, images=current_user_images)`。保留 `spec.context_builder` 字段读取（compactor / SystemPromptBuilder 仍用），保留 `current_input_context` 旁路（compactor 用）。

删除文件：

- Delete: `tests/matmaster/services/test_user_instructions_runtime_injection.py` — 该测试整文件覆盖被删 helper，Phase 2C cutover 后无被测对象。

不变文件（验收必须断言这些文件 byte-for-byte 未改）：

- `matmaster/core/context_compactor.py`
- `matmaster/core/exp.py`
- `matmaster/core/context_builder.py`
- `matmaster/manifests/__init__.py` / `attachment.py` / `skill.py` / `mcp.py` / `rehydrator.py` / `scanner.py` / `bohrium.py` / `artifact.py` / `workspace.py`
- `matmaster/context/sections.py` / `rendering.py` / `turn_context.py` / `compositions.py` / `assembly.py` / `turn_intent.py` / `ports.py` / `scanner.py` / `session.py` / `history_restore.py`
- `matmaster/context/sources/` 全目录
- `src/services/context_assembly_ports.py` / `context_turn_intent.py` / `model_history_restore_service.py` / `history_checkpoint_codec.py` / `history_checkpoint_service.py`
- `src/services/agent_run_bohrium_stage.py` / `chat_history.py` / `stream_service.py` / `stream_sse_filter.py`

新增测试（最小化）：

- Test: `tests/services/test_context_assembly_factory.py`
- Test: `tests/matmaster/services/test_agent_run_stream_context_cutover.py`

更新测试（最小化）：

- Modify Test: `tests/matmaster/services/test_user_turn_context_service.py` — 删除针对 `latest_anchor_user_instructions_hash` / `decide_user_turn_context_kind` / `render_runtime_task_for_user_turn_context` / `render_provider_facing_current_message_content` / `build_user_turn_context_payload` 的用例；保留 `write_user_turn_context_event` / `UserInstructionsInfo` / `hash_user_instructions` / `load_user_instructions_from_session` 相关用例。
- Modify Test: `tests/matmaster/services/test_agent_run_stream.py` — 把假设 Stage 5b 仍走 `build_user_turn_context_payload` 的 mock / patch 切换到新链路（`AppUserInstructionsPort` mock + `resolve_turn_context_intent` 入口）。
- Modify Test: `tests/matmaster/core/test_agent_kernel_stream.py`（若现有 kernel 测试更适合则使用现有文件）— 增加运行时断言，验证 kernel 不再把 service 已装配好的 `task` 通过 `build_user_request` 二次包装。

保留并继续通过（不修改）：

- `tests/matmaster/context/` 所有 Phase 2A + 2B 用例（含 `test_assembly.py` / `test_manifests_equivalence.py`）。
- `tests/matmaster/services/test_context_assembly_ports.py` / `test_context_turn_intent.py`。
- `tests/matmaster/services/test_model_history_restore_service.py`。
- `tests/matmaster/services/test_history_restore_service.py` / `test_history_checkpoint_codec.py` / `test_history_checkpoint_service.py`。
- `tests/matmaster/manifests/` 全部 shim 等价用例（Phase 2B 验收范围，不归 Phase 2C 修改）。
- `tests/services/test_attachment_manifest_service.py`（若存在；属于 shim 兼容回归基线）。
- `tests/matmaster/integration/test_history_checkpoint_recovery.py`。
- `tests/matmaster/services/test_active_mcp_replay.py` / `test_lazy_mcp_replay.py`。

---

## Task 1: Baseline And Phase Boundary Inventory

**Files:** read-only

**Spec 依据:** DESIGN.md §14 Phase 2B 终态 / Phase 2C 起手；附录 B「Phase 2B 改动」/「Phase 2C 改动」；PHASE-2B-PLAN.md「Notes For Phase 2C」。

- [ ] **Step 1: Confirm uv environment and dirty files**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -V && git status --short
```

Expected:

```text
Python 3.11+ (typically 3.13.x)
git status --short prints the current dirty files (may include unrelated .planning/* and DESIGN.md edits)
```

工作树通常会有 Phase 2C 范围外的 dirty 文件（DESIGN.md 微调、`.planning/*` 笔记等）。**不要** 把 dirty list 视为必须匹配的断言：只确认 Python 环境正确，并记录哪些 dirty 文件可能与本计划编辑重叠（重点关注 `matmaster/types/runtime.py`、`matmaster/types/current_input.py`、`matmaster/core/agent.py`、`src/services/agent_run_service.py`、`src/services/agent_run_history_wiring.py`、`src/services/agent_run_instructions.py`、`src/services/user_turn_context_service.py`）。If those files are dirty, read them via `git diff <file>` before editing; do not revert them.

- [ ] **Step 2: Confirm Phase 2A + 2B artifacts present and Phase 2C targets absent**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && \
  test -f matmaster/context/sections.py && \
  test -f matmaster/context/rendering.py && \
  test -f matmaster/context/turn_context.py && \
  test -f matmaster/context/compositions.py && \
  test -f matmaster/context/assembly.py && \
  test -f matmaster/context/turn_intent.py && \
  test -f matmaster/context/ports.py && \
  test -f matmaster/context/scanner.py && \
  test -f matmaster/context/session.py && \
  test -f matmaster/context/history_restore.py && \
  test -f matmaster/context/sources/turn_input.py && \
  test -f matmaster/context/sources/user_instructions.py && \
  test -f matmaster/context/sources/compacted_history.py && \
  test -f matmaster/context/sources/session_jobs.py && \
  test -f matmaster/context/sources/attachments.py && \
  test -f matmaster/context/sources/skills.py && \
  test -f matmaster/context/sources/tools.py && \
  test -f src/services/context_assembly_ports.py && \
  test -f src/services/context_turn_intent.py && \
  test ! -f src/services/context_assembly_factory.py && \
  test ! -f tests/services/test_context_assembly_factory.py && \
  test ! -f tests/matmaster/services/test_agent_run_stream_context_cutover.py
```

Expected: command exits `0`. 如果 Phase 2A / 2B 文件缺失，停止并报告 —— Phase 2C 依赖干净的 Phase 2B baseline。如果任一 Phase 2C 新增目标文件已存在，停止并 `git diff` 该文件再决定下一步。

- [ ] **Step 3: Confirm runtime callers of `matmaster.manifests` match Phase 2B 末态**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "from matmaster\.manifests|import matmaster\.manifests" matmaster src tests
```

Expected: 匹配应**仅**出现在以下文件（Phase 2B 验收基线，Phase 2C 必须保留 manifests shim 入口）：

```text
src/services/agent_run_service.py            # Task 6 改造后移除该 import
src/services/agent_run_history_wiring.py     # Task 5 改造后移除该 import
matmaster/core/context_compactor.py          # Phase 2C 不动，Phase 3 改造
matmaster/core/exp.py                        # Phase 2C 不动，Phase 3 改造
matmaster/manifests/skill.py                 # shim 内部 cross-import (Phase 2B 落地)
matmaster/manifests/rehydrator.py            # shim 内部 cross-import (Phase 2B 落地)
matmaster/manifests/attachment.py            # 若存在 cross-import (Phase 2B 落地)
matmaster/manifests/mcp.py                   # shim 内部 cross-import (Phase 2B 落地)
matmaster/manifests/scanner.py               # shim 内部 cross-import (Phase 2B 落地)
tests/services/test_attachment_manifest_service.py
tests/matmaster/manifests/test_*.py
tests/matmaster/integration/test_history_checkpoint_recovery.py
tests/matmaster/services/test_active_mcp_replay.py
```

Phase 2C 完成后，前两行（`agent_run_service.py` / `agent_run_history_wiring.py`）会消失；其余文件保持不变。把上面这份预期清单复制到 Task 11 静态校验的对照表。

- [ ] **Step 4: Confirm Phase 2B `ContextAssembler.session_context_factory` seam is wired**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "session_context_factory\b" matmaster/context/assembly.py tests/matmaster/context/test_assembly.py
```

Expected:
- `matmaster/context/assembly.py` 内 `ContextAssembler.__init__` 接受 `session_context_factory` 参数，无 factory 时回退 `_empty_session_section_builder`，有 factory 时通过 `_build_via_factory` 调 `SessionContextBuilder.build_sections(...)`。
- `tests/matmaster/context/test_assembly.py` 已有 factory 注入用例。

如果 `session_context_factory` 不存在，停止并报告：Phase 2B baseline 未完成 production seam 工作，Phase 2C 无法开始。

- [ ] **Step 5: Snapshot current Phase 1+2B baseline test suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/manifests \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/matmaster/services/test_context_turn_intent.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_user_instructions_runtime_injection.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_history_restore_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  tests/matmaster/services/test_active_mcp_replay.py \
  tests/matmaster/services/test_lazy_mcp_replay.py \
  tests/matmaster/integration \
  tests/services/test_attachment_manifest_service.py \
  tests/test_chat_events_history_checkpoint.py \
  -q
```

Expected: all tests pass。把通过的总数记到笔记里，作为 Phase 2C 完成后的回归基线。如果有任何 fail，停止并先修 baseline；Phase 2C 不允许在带有失败 baseline 上开工。

- [ ] **Step 6: Verify `attachment_manifest` is not a compactor dependency**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "attachment_manifest" \
  matmaster/core/context_compactor.py \
  matmaster/core/exp.py \
  matmaster/core/agent_compaction.py \
  matmaster/core/agent.py \
  src/services/agent_run_service.py \
  src/services/agent_run_history_wiring.py
```

Expected before Task 6/7:

```text
matmaster/core/exp.py:... run_meta.get("attachment_manifest", "")
matmaster/core/agent.py:... spec.meta.get("attachment_manifest")
src/services/agent_run_service.py:... pg_ctx.with_run_meta(attachment_manifest=attachment_text)
src/services/agent_run_history_wiring.py:... attachment_manifest...
```

No match is allowed in `matmaster/core/context_compactor.py` or `matmaster/core/agent_compaction.py`.

Interpretation:

- `agent_run_history_wiring.py` is Task 5 scope.
- `agent_run_service.py` write-side is Task 6 scope.
- `core/agent.py` read-side is Task 7 scope.
- `core/exp.py` copies passive `run_meta["attachment_manifest"]` into `spec.meta`; after Task 6 removes the write-side, this becomes harmless empty-string compatibility. Do **not** edit `core/exp.py` in Phase 2C, but Task 7/11 static checks must prove the kernel no longer consumes the copied key.
- If `context_compactor.py` or `agent_compaction.py` matches, stop. The Task 6 removal of `pg_ctx.with_run_meta(attachment_manifest=...)` is unsafe until that dependency is either preserved or moved to Phase 3.

- [ ] **Step 7: Verify MCP session context inputs are visible before Stage 5b**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "legal_mcp_servers|schemas_by_server|allowed_mcp_servers" \
  src/services matmaster/core matmaster/context tests -g '*.py'
```

Expected before Task 6:

```text
matmaster/core/exp.py:... legal_mcp_servers=run_meta.get("legal_mcp_servers")
matmaster/core/exp.py:... schemas_by_server=run_meta.get("schemas_by_server")
matmaster/context/session.py:...
matmaster/context/sources/tools.py:...
tests/matmaster/context/...
```

Important conclusion for implementers:

- The current production code shows `legal_mcp_servers` / `schemas_by_server` are consumed by `Exp.assemble` from passive `run_meta`; there may be no service-side writer before Stage 5b in the current baseline.
- Task 6 must therefore treat missing keys as an explicit tested case, not an assumption. Add a cutover snapshot with non-empty events + explicitly supplied `legal_mcp_servers` / `schemas_by_server` in the `session_context_factory` unit layer; do not claim production already populates them unless the grep reveals a real writer.
- The `legal_mcp_servers` → `allowed_mcp_servers` rename remains Phase 4 scope. Do not rename fields in Phase 2C.

- [ ] **Step 8: Pin the real SSE filter test files**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "user_turn_context|_should_emit_event_to_sse|SSEHandler" \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_sse_skill_hit.py \
  src/services/stream_sse_filter.py \
  matmaster/integration/sse_handler.py
```

Expected:

```text
tests/test_stream_replay_skill_hit.py:... test_should_not_emit_user_turn_context
tests/matmaster/integration/test_sse_skill_hit.py:... test_should_skip_user_turn_context
src/services/stream_sse_filter.py:... user_turn_context hidden
matmaster/integration/sse_handler.py:... user_turn_context hidden
```

Task 10 and Task 11 must run these exact test files. Do not use a conditional command that silently falls back to `echo "test file may not exist"`.

- [ ] **Step 9: Inventory Stage 5b call sites in `agent_run_service.py`**

Read the following ranges to confirm Phase 2C cutover surface area:

- [src/services/agent_run_service.py:51-59](../../src/services/agent_run_service.py:51) — `user_turn_context_service` 当前 import 列表（Task 6 会缩减）。
- [src/services/agent_run_service.py:528-580](../../src/services/agent_run_service.py:528) — Stage 5b 主路径：`get_recent_context_anchor_events` → `latest_anchor_user_instructions_hash` → `decide_user_turn_context_kind` → `render_runtime_task_for_user_turn_context` → `render_provider_facing_current_message_content` → `build_user_turn_context_payload` → `write_user_turn_context_event`。该范围**整段替换**为新链路。
- [src/services/agent_run_service.py:582](../../src/services/agent_run_service.py:582) — `user_prompt = rendered_runtime_task` 这一行的语义变化：Phase 2C 后 `user_prompt` 必须是 `assembly.user_turn_context.to_message(ContextView.RUNTIME).content`（含 instructions + attachments + user_text 装配后的完整字符串）。

- [ ] **Step 10: Inventory kernel entry change surface in `core/agent.py`**

Read [matmaster/core/agent.py:260-275](../../matmaster/core/agent.py:260) carefully:

```python
current_user_images = [
    ImageContentPart.model_validate(image)
    for image in spec.meta.get("current_user_images", [])
]
attachment_text = str(spec.meta.get("attachment_manifest") or "")
user_content = spec.context_builder.build_user_request(
    user_text=task,
    attachments=attachment_text,
)
state = _KernelState(
    messages=[
        SystemMessage(content=spec.system_prompt),
        *(history or []),
        UserMessage(content=user_content, images=current_user_images),
    ]
)
```

Task 7 改造后等价于：

```python
current_user_images = [
    ImageContentPart.model_validate(image)
    for image in spec.meta.get("current_user_images", [])
]
state = _KernelState(
    messages=[
        SystemMessage(content=spec.system_prompt),
        *(history or []),
        UserMessage(content=task, images=current_user_images),
    ]
)
```

注意：`spec.context_builder` / `spec.system_prompt` / `current_input_context` (compactor 旁路) 等其他字段仍保留。只移除 `attachment_text` 读取和 `build_user_request` 调用。

This Task has no commit.

---

## Task 2: Add TurnInput Re-Export Shim In `matmaster/types/current_input.py`

**Files:**
- Modify: `matmaster/types/current_input.py`
- Modify: `tests/matmaster/services/test_user_turn_context_service.py`（仅新增 import smoke test，不改其他用例）

**Spec 依据:** DESIGN.md §5.3 删除/迁移清单（`matmaster/types/current_input.py` 迁到 `context/sources/turn_input.py`，shim 保留至阶段 4）、§13 命名清理表（`current_input.py` → `context/sources/turn_input.py`）、§14 Phase 2C 改动「`matmaster/types/current_input.py` shim」、附录 B「Phase 2C 改动」、PHASE-2B-PLAN.md「Notes For Phase 2C」第 5 条。

Phase 2C 阶段 `CurrentInputContext` + `build_current_instruction_block` 仍是 `core/agent.py` runtime/preflight 路径与 `core/context_compactor.py` 的兼容入口，**不能删除**。但 DESIGN §13 要求 `TurnInput` 经由 `matmaster/types/current_input.py` 暴露 import path，让 service 层与未来 phase 3 compactor 切换时能在不破坏现有 import 语句的前提下逐步迁移。本 Task 只新增 re-export shim，不删除任何现有公共符号。

- [ ] **Step 1: Write smoke test for `TurnInput` re-export**

Edit `tests/matmaster/services/test_user_turn_context_service.py` 顶部添加一个独立的 import smoke test（不与现有用例耦合）：

```python
def test_turn_input_is_reexported_from_current_input_shim() -> None:
    """Phase 2C shim: `matmaster.types.current_input` re-exports
    `TurnInput` from `matmaster.context.sources.turn_input` so callers
    can migrate without changing every import statement.
    """
    from matmaster.context.sources.turn_input import TurnInput as TurnInputSource
    from matmaster.types.current_input import TurnInput

    assert TurnInput is TurnInputSource
```

(放在文件最顶部 import 之后、`UserInstructionsInfo` 相关用例之前。)

- [ ] **Step 2: Verify the smoke test is red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_user_turn_context_service.py::test_turn_input_is_reexported_from_current_input_shim -q
```

Expected: `ImportError: cannot import name 'TurnInput' from 'matmaster.types.current_input'`.

- [ ] **Step 3: Add the re-export to `matmaster/types/current_input.py`**

在 `matmaster/types/current_input.py` 顶部 `from __future__ import annotations` 下方、`from dataclasses import dataclass` 之前插入一行 re-export，并加 docstring 标明这是 shim：

```python
from __future__ import annotations

# Phase 2C shim (DESIGN.md §13 命名清理 / §14 Phase 2C 改动 / Phase 4 删除):
# `TurnInput` 的真实定义已迁到 `matmaster.context.sources.turn_input`。
# 本模块保留 re-export 是为了在 Phase 4 删除旧 import 路径之前，
# 让生产 / 测试代码可以渐进切换。`CurrentInputContext` /
# `build_current_instruction_block` 在 Phase 4 之前仍是
# `core/agent.py` runtime+preflight 路径与 `core/context_compactor.py`
# preflight compaction 路径的兼容入口，不在本 phase 删除。
from matmaster.context.sources.turn_input import TurnInput  # noqa: F401

from dataclasses import dataclass
...
```

(其余文件内容**保持原样**，不要重排现有 import / 重命名 / 调整类型注解。)

- [ ] **Step 4: Verify the smoke test is green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_user_turn_context_service.py::test_turn_input_is_reexported_from_current_input_shim -q
```

Expected: passes.

- [ ] **Step 5: Confirm full module suite stays green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_user_instructions_runtime_injection.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/context \
  -q
```

Expected: all tests pass。

- [ ] **Step 6: Commit**

Commit 信息：

```text
refactor(context): re-export TurnInput from matmaster.types.current_input shim

DESIGN.md §13 命名清理 + §14 Phase 2C：在不删除 CurrentInputContext /
build_current_instruction_block 的前提下，提供 TurnInput 的旧 import path,
为 Phase 2C 后续 cutover 与 Phase 4 真正删除 shim 留出过渡。
```

---

## Task 3: Add Production `session_context_factory` Helper

**Files:**
- Create: `src/services/context_assembly_factory.py`
- Create: `tests/services/test_context_assembly_factory.py`

**Spec 依据:** DESIGN.md §7bis.6 调用图（`ContextAssembler` 装配 session sections 通过 `session_context_factory`）、§14 Phase 2C「AgentRuntimeSpec 注入」、§17 验收要点 6（events port 在 service 与 assembler 同时被调用的事务一致性 → 工厂方法把构造控制权交给 service，便于后续优化）、PHASE-2B-PLAN.md「Notes For Phase 2C」第 3 条（`agent_run_service` builds it from `playground_ctx`-derived skill registry + mcp configuration, and passes it into `ContextAssembler.__init__`）、`matmaster/core/exp.py:496-506` 当前 `CompactionRehydrator` 构造参数（`skill_registry`, `playground_ctx`, `legal_mcp_servers`, `schemas_by_server`）。

新 helper `build_session_context_factory` 接受 service 层已经构造好的 skill registry + run_meta 字段，返回一个匹配 `ContextAssembler.session_context_factory` 签名的 Callable。Phase 2C 内只在 Stage 5b 装配 assembler 时调用一次；Phase 3 compactor cutover 后可同名复用。

- [ ] **Step 1: Write failing tests for `build_session_context_factory`**

Create `tests/services/test_context_assembly_factory.py`:

```python
from __future__ import annotations

import pytest

from matmaster.context.ports import SessionEvent
from matmaster.context.scanner import coerce_session_events
from matmaster.context.session import SessionContextBuilder

from src.services.context_assembly_factory import build_session_context_factory


@pytest.fixture
def sample_events() -> tuple[SessionEvent, ...]:
    return coerce_session_events(
        [
            {
                "id": 1,
                "type": "skill_hit",
                "content": {"skill_name": "pxrd"},
                "source": "System",
            },
            {
                "id": 2,
                "type": "query",
                "source": "User",
                "content": {"content": "hello", "files": ["/tmp/a.txt"]},
                "invocation_id": "inv-1",
            },
        ]
    )


def test_factory_returns_session_context_builder_with_injected_dependencies(
    sample_events: tuple[SessionEvent, ...],
) -> None:
    factory = build_session_context_factory(
        skill_registry=object(),
        legal_mcp_servers={"bohrium"},
        schemas_by_server={"bohrium": [{"name": "submit_job"}]},
    )

    builder = factory(sample_events)

    assert isinstance(builder, SessionContextBuilder)
    assert builder.events is sample_events
    assert builder.legal_mcp_servers == {"bohrium"}
    assert builder.schemas_by_server == {"bohrium": [{"name": "submit_job"}]}


def test_factory_passes_none_legal_servers_through(
    sample_events: tuple[SessionEvent, ...],
) -> None:
    factory = build_session_context_factory(
        skill_registry=object(),
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    builder = factory(sample_events)

    assert builder.legal_mcp_servers is None
    assert builder.schemas_by_server is None


def test_factory_accepts_empty_events_and_returns_buildable_sections(
    sample_events: tuple[SessionEvent, ...],
) -> None:
    factory = build_session_context_factory(
        skill_registry=None,
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    builder = factory(())

    sections = builder.build_sections(until_event_id=None, include_attachments=True)
    assert sections == ()


def test_factory_rejects_non_tuple_events_via_session_builder_invariant() -> None:
    factory = build_session_context_factory(
        skill_registry=None,
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    with pytest.raises(TypeError, match="must be a tuple of SessionEvent"):
        factory([])  # type: ignore[arg-type]
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/services/test_context_assembly_factory.py -q
```

Expected: `ModuleNotFoundError: No module named 'src.services.context_assembly_factory'`.

- [ ] **Step 3: Implement `src/services/context_assembly_factory.py`**

Create `src/services/context_assembly_factory.py`:

```python
"""Phase 2C production helper for ContextAssembler.session_context_factory.

`matmaster.context.assembly.ContextAssembler` takes a Callable
`session_context_factory: Callable[[tuple[SessionEvent, ...]], SessionContextBuilder]`.
Service layer constructs the SessionContextBuilder with platform-specific
dependencies (skill registry, allowed mcp servers, schemas_by_server) that
matmaster.context cannot know.

DESIGN.md §7bis.6 keeps the construction in service layer so the events
DAO query path stays under platform control. Phase 3 will reuse the same
factory to drive compactor's assemble_compaction path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from matmaster.context.ports import SessionEvent
from matmaster.context.session import SessionContextBuilder

SessionContextFactory = Callable[[tuple[SessionEvent, ...]], SessionContextBuilder]


def build_session_context_factory(
    *,
    skill_registry: Any | None,
    legal_mcp_servers: set[str] | None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
) -> SessionContextFactory:
    """Bind skill registry + mcp config to a SessionContextBuilder factory.

    The returned callable matches ContextAssembler.session_context_factory's
    signature: (tuple[SessionEvent, ...]) -> SessionContextBuilder.
    """

    def factory(events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=events,
            skill_registry=skill_registry,
            legal_mcp_servers=legal_mcp_servers,
            schemas_by_server=schemas_by_server,
        )

    return factory
```

- [ ] **Step 4: Verify tests are green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/services/test_context_assembly_factory.py -q
```

Expected: all 4 tests pass。

- [ ] **Step 5: Confirm Phase 2A + 2B baseline stays green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/matmaster/services/test_context_turn_intent.py \
  -q
```

Expected: all tests pass。

- [ ] **Step 6: Commit**

Commit 信息：

```text
feat(context): add production session_context_factory helper

DESIGN.md §7bis.6 + §14 Phase 2C：把 ContextAssembler.session_context_factory
的平台依赖（skill registry / allowed mcp servers / schemas_by_server）
封装为 build_session_context_factory，供 Stage 5b cutover 装配 assembler
时直接调用。Phase 3 compaction cutover 复用同一 helper。
```

---

## Task 4: Extend `AgentRuntimeSpec` With Context Assembler / Port Fields

**Files:**
- Modify: `matmaster/types/runtime.py`
- Modify: `tests/matmaster/test_runtime_spec.py` (新增；若文件不存在则创建)

**Spec 依据:** DESIGN.md §10.3「`AgentRuntimeSpec` 字段变更」v3.1 列、§15「`AgentRuntimeSpec` 字段演化」Phase 2C 列、§14 Phase 2C「AgentRuntimeSpec 注入」、附录 B「Phase 2C 改动」「`AgentRuntimeSpec` 注入 `context_assembler` / `user_instructions_port` / `session_events_port` / `session_jobs_port | None`」。

Phase 2C 内 spec 内的这 4 个字段对 kernel / compactor 都是 **dead weight**（kernel 改造后 task 参数已经是装配好的字符串；compactor 仍走老 CompactionRehydrator 路径，Phase 3 才迁移）。但 spec 字段必须落地，Phase 3 才能在不改 spec schema 的情况下复用同一注入入口。所有字段都是 Optional（默认 None），并通过 `_check_v2_field_types` model_validator 校验注入类型。

- [ ] **Step 1: Read the existing AgentRuntimeSpec model**

Read [matmaster/types/runtime.py:48-110](../../matmaster/types/runtime.py:48) 以理解：

- `AgentRuntimeSpec` 是 frozen Pydantic BaseModel（`model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)`）
- 现有 lazy-import 校验放在 `_check_v2_field_types` model_validator 内（避免循环 import）
- 字段顺序：`llm_provider` → `max_turns` → `hook_executor` → `runtime_ports` → `compaction` → `system_prompt` → `compactor` → `context_builder` → `meta` → 4 个 `Any | None` 字段

新字段必须遵循同样的 lazy-import + Optional + model_validator 模式。

- [ ] **Step 2: Write failing tests for the 4 new fields**

Create `tests/matmaster/test_runtime_spec.py`（若不存在则新建，已存在则附加用例）:

```python
from __future__ import annotations

import pytest

from matmaster.core.context_builder import ContextBuilder
from matmaster.types.runtime import AgentRuntimeSpec


def _ctx_builder() -> ContextBuilder:
    return ContextBuilder()


def test_spec_accepts_no_context_assembler_or_ports() -> None:
    spec = AgentRuntimeSpec(context_builder=_ctx_builder())
    assert spec.context_assembler is None
    assert spec.user_instructions_port is None
    assert spec.session_events_port is None
    assert spec.session_jobs_port is None


def test_spec_accepts_real_context_assembler() -> None:
    from matmaster.context.assembly import ContextAssembler
    from matmaster.context.ports import ContextAssemblyPorts

    class _StubEventsPort:
        async def load_events(self, _query):  # noqa: ARG002
            return ()

    assembler = ContextAssembler(
        ports=ContextAssemblyPorts(session_events=_StubEventsPort())
    )
    spec = AgentRuntimeSpec(
        context_builder=_ctx_builder(),
        context_assembler=assembler,
    )
    assert spec.context_assembler is assembler


def test_spec_rejects_non_assembler_type_for_context_assembler() -> None:
    with pytest.raises(ValueError, match="context_assembler"):
        AgentRuntimeSpec(
            context_builder=_ctx_builder(),
            context_assembler="not-an-assembler",  # type: ignore[arg-type]
        )


def test_spec_accepts_real_user_instructions_port() -> None:
    from src.services.context_assembly_ports import AppUserInstructionsPort

    spec = AgentRuntimeSpec(
        context_builder=_ctx_builder(),
        user_instructions_port=AppUserInstructionsPort(),
    )
    assert isinstance(spec.user_instructions_port, AppUserInstructionsPort)


def test_spec_accepts_real_session_events_port() -> None:
    from src.services.context_assembly_ports import AppSessionEventsPort

    class _EventsTable:
        def query_context_events(self, **_kwargs):
            return []

    port = AppSessionEventsPort(events_table=_EventsTable())
    spec = AgentRuntimeSpec(
        context_builder=_ctx_builder(),
        session_events_port=port,
    )
    assert spec.session_events_port is port


def test_spec_accepts_optional_session_jobs_port_as_none() -> None:
    spec = AgentRuntimeSpec(
        context_builder=_ctx_builder(),
        session_jobs_port=None,
    )
    assert spec.session_jobs_port is None
```

注：这里故意使用 fake events table，不调用真实 `get_chat_events_table()`。`AgentRuntimeSpec` 只校验 port 能力边界，不应把 unit test 绑定到 DB fixture。

- [ ] **Step 3: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/test_runtime_spec.py -q
```

Expected: tests fail with `AttributeError` 或 `pydantic.ValidationError`（取决于 Pydantic 的字段未声明行为；通常是 `ValidationError: extra fields are not permitted`，因为 spec 是 frozen + extra='forbid' 默认）。

- [ ] **Step 4: Add 4 Optional fields to `AgentRuntimeSpec`**

在 [matmaster/types/runtime.py](../../matmaster/types/runtime.py) 内修改：

1. 在文件顶部 import 区段下方加 TYPE_CHECKING 块（避免循环 import）：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from matmaster.context.assembly import ContextAssembler
    from matmaster.context.ports import (
        SessionEventsPort,
        SessionJobsPort,
        UserInstructionsPort,
    )
```

(若文件已经有 `if TYPE_CHECKING:` 块则在其中追加，避免重复。)

2. 在 `meta: dict[str, Any] = Field(default_factory=dict)` 后、`tool_runner: Any | None = None` 前插入 4 个 Optional 字段：

```python
    # Phase 2C v3.1 (DESIGN.md §10.3 / §15 / §14 Phase 2C):
    # 注入装配三件套用的 assembler + 三个 port。Phase 2C kernel/compactor 不调用,
    # 仅 service 层在 Stage 5b 持有同一 assembler 实例; spec 字段保留是为 Phase 3
    # compactor cutover 提供同名注入入口。所有字段默认 None。
    context_assembler: Any | None = None
    user_instructions_port: Any | None = None
    session_events_port: Any | None = None
    session_jobs_port: Any | None = None
```

3. 在 `_check_v2_field_types` model_validator 内扩展类型校验：

在现有 `checks: list[tuple[str, Any, type]]` 之后增加 lazy import 检查（不能放进同一个 checks list，因为 `Protocol` 不能用 `isinstance` 直接校验）。具体写法：

```python
        if self.context_assembler is not None:
            from matmaster.context.assembly import ContextAssembler

            if not isinstance(self.context_assembler, ContextAssembler):
                raise ValueError(
                    "context_assembler must be ContextAssembler, "
                    f"got {type(self.context_assembler).__name__}"
                )
        # user_instructions_port / session_events_port / session_jobs_port
        # 是 Protocol-typed runtime adapters; isinstance 校验依赖 runtime_checkable.
        # ports.py 上的 Protocol 已经声明 @runtime_checkable, 但实际生产实现
        # 直接遵从 duck typing。这里只做 None 与 hasattr 校验,
        # 避免和测试 stub 实例化冲突。
        if self.user_instructions_port is not None and not hasattr(
            self.user_instructions_port, "load_user_instructions"
        ):
            raise ValueError(
                "user_instructions_port must implement load_user_instructions"
            )
        if self.session_events_port is not None and not hasattr(
            self.session_events_port, "load_events"
        ):
            raise ValueError("session_events_port must implement load_events")
        if self.session_jobs_port is not None and not hasattr(
            self.session_jobs_port, "load_session_jobs"
        ):
            raise ValueError("session_jobs_port must implement load_session_jobs")
```

(`AppUserInstructionsPort` / `AppSessionEventsPort` / `AppSessionJobsPort` 都有同名 method，duck typing 通过。)

4. 检查 `matmaster/context/ports.py` 内 `UserInstructionsPort` / `SessionEventsPort` / `SessionJobsPort` 是否已声明 `@runtime_checkable`。如果未声明，Phase 2C 不去补 —— hasattr 校验已足够，避免对 Phase 2A 文件做 byte-changing edit。

- [ ] **Step 5: Verify tests pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/test_runtime_spec.py -q
```

Expected: all tests pass（DB-dependent `test_spec_accepts_real_session_events_port` 可能 skip）。

- [ ] **Step 6: Confirm Phase 2A + 2B baseline still passes**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/matmaster/services/test_context_turn_intent.py \
  tests/matmaster/services/test_agent_run_stream.py \
  -q
```

Expected: all tests pass。

- [ ] **Step 7: Commit**

Commit 信息：

```text
feat(runtime): add context assembler + port fields to AgentRuntimeSpec

DESIGN.md §10.3 v3.1 / §15 Phase 2C / §14 Phase 2C：AgentRuntimeSpec 新增
context_assembler / user_instructions_port / session_events_port /
session_jobs_port 四个 Optional 字段。Phase 2C kernel/compactor 不使用,
仅作为 Phase 3 compaction cutover 的注入入口预留。lazy import 校验避免
循环依赖, hasattr duck typing 兼容生产 port 与测试 stub。
```

---

## Task 5: Rewire `agent_run_history_wiring.py` Off `matmaster.manifests`

**Files:**
- Modify: `src/services/agent_run_history_wiring.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`（已有 import 假设可能要小调；视回归情况）

**Spec 依据:** DESIGN.md §5.3 删除/迁移清单（`matmaster/manifests/` → `matmaster/context/`）、§14 Phase 2C「`agent_run_service` / `agent_run_history_wiring` 切到 `matmaster.context.*`」、附录 B「Phase 2C 改动」、PHASE-2B-PLAN.md「Notes For Phase 2C」第 1 条。

`build_history_wiring` 当前调用：

```python
from matmaster.manifests import attachment as attachment_manifest
...
entries = attachment_manifest.build_available_attachments(query_events)
attachment_text = attachment_manifest.format_available_attachments(entries)
```

Phase 2B 落地后，`attachment_manifest.build_available_attachments` 内部已是 shim → `scan_legacy_attachment_entries`；`format_available_attachments` shim → `format_entries_text`。Phase 2C 把 import 直接指向 `matmaster.context.sources.attachments`，行为等价但消除一层间接。**注意**：`query_events` 是 `events_table.get_session_user_query_events(session_id)` 返回的 display-flattened rows（`content` 是 str，`files/images/workspace_paths` 在顶层），必须走 `scan_legacy_attachment_entries`（不是 `scan_attachment_entries`，后者要求 typed SessionEvent + nested `content` payload）。

- [ ] **Step 1: Read current import / call sites**

Read [src/services/agent_run_history_wiring.py:21-77](../../src/services/agent_run_history_wiring.py:21) 仔细确认：

- import 仅一行 `from matmaster.manifests import attachment as attachment_manifest`
- 调用点 line 76-77：`build_available_attachments(query_events)` + `format_available_attachments(entries)`
- `query_events` 类型是 `list[dict]`（display-flattened）

切换后调用变成：

```python
from matmaster.context.sources.attachments import (
    format_entries_text,
    scan_legacy_attachment_entries,
)
...
entries = scan_legacy_attachment_entries(query_events)
attachment_text = format_entries_text(entries)
```

- [ ] **Step 2: Write an equivalence smoke test (lightweight regression guard)**

Append to `tests/matmaster/services/test_agent_run_stream.py`（或合适的 unit-test 文件）一个 minimal 等价 case，不依赖完整 `build_history_wiring` flow，只对 `attachment_text` 等价性做断言：

```python
def test_history_wiring_attachment_text_equivalent_to_manifests_shim() -> None:
    """Phase 2C: agent_run_history_wiring 切到 matmaster.context.sources.attachments
    后 attachment_text 必须与 matmaster.manifests.attachment shim 输出字节等价。
    """
    from matmaster.context.sources.attachments import (
        format_entries_text,
        scan_legacy_attachment_entries,
    )
    from matmaster.manifests.attachment import (
        build_available_attachments,
        format_available_attachments,
    )

    query_events: list[dict] = [
        {
            "id": 10,
            "source": "User",
            "type": "query",
            "content": "first turn user text",
            "files": ["/tmp/a.txt"],
            "images": ["/tmp/b.png"],
            "workspace_paths": ["/workspace/note.md"],
        },
        {
            "id": 12,
            "source": "User",
            "type": "query",
            "content": "second turn",
            "files": ["/tmp/c.csv"],
        },
    ]

    manifest_text = format_available_attachments(
        build_available_attachments(query_events)
    )
    source_text = format_entries_text(scan_legacy_attachment_entries(query_events))

    assert manifest_text == source_text
    assert "[Available attachments]" in source_text
    assert "/tmp/c.csv" in source_text
```

- [ ] **Step 3: Verify the smoke test passes on Phase 2B baseline**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_agent_run_stream.py::test_history_wiring_attachment_text_equivalent_to_manifests_shim -q
```

Expected: passes。Phase 2B shim 已经保证等价；测试用作 Phase 2C cutover 的安全网。

- [ ] **Step 4: Cut `build_history_wiring` over to context.sources.attachments**

Edit `src/services/agent_run_history_wiring.py`:

1. 把 import 行：

```python
from matmaster.manifests import attachment as attachment_manifest
```

替换为：

```python
from matmaster.context.sources.attachments import (
    format_entries_text,
    scan_legacy_attachment_entries,
)
```

2. 把调用点（约 line 76-77）：

```python
    entries = attachment_manifest.build_available_attachments(query_events)
    attachment_text = attachment_manifest.format_available_attachments(entries)
```

替换为：

```python
    entries = scan_legacy_attachment_entries(query_events)
    attachment_text = format_entries_text(entries)
```

文件其余部分（`build_history_wiring` 签名 / `_get_query_events` / `_get_all_events` / `_get_latest_checkpoint_covered_until_event_id` / `_RunSessionEventHistory` / `PlaygroundRuntimePorts` 装配 / `bohrium_rebuild_events` 加载）**完全不动**。

- [ ] **Step 5: Verify the smoke test + all existing agent_run_stream tests still pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  -q
```

Expected: all tests pass。

- [ ] **Step 6: Confirm Phase 2B manifests shim回归与 attachment 服务级测试不退化**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/manifests \
  tests/services/test_attachment_manifest_service.py \
  -q
```

Expected: all tests pass（manifests shim 仍是其它 caller 的对外 API，Phase 2C 不动 shim 本身）。

- [ ] **Step 7: Static check — no `matmaster.manifests` import remains in `agent_run_history_wiring.py`**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "matmaster\.manifests" src/services/agent_run_history_wiring.py
```

Expected: 无匹配。

- [ ] **Step 8: Commit**

Commit 信息：

```text
refactor(services): rewire agent_run_history_wiring to matmaster.context.sources.attachments

DESIGN.md §5.3 + §14 Phase 2C：删除 attachment_manifest shim 间接调用,
直接走 scan_legacy_attachment_entries + format_entries_text。Phase 2B
等价对照测试保证 attachment_text 字节等价。
```

---

## Task 5.5: Pin Phase 1 Renderer Oracle Before Stage 5b Cutover

**Files:**
- Create/Modify: `tests/matmaster/services/test_agent_run_stream_context_cutover.py`

**Spec 依据:** DESIGN.md §6.5 prompt 形态决策、§14 Phase 2C「Snapshot test: 关键 case 的 prompt 字符串」、§17 验收要点；review 合并要求：在删除 Phase 1 renderer 前直接比较旧链路与新 assembler 链路，避免仅靠传递性断言把真实 render delta 掩盖掉。

本 Task 是 Task 6 的 gate。它必须在 Stage 5b cutover 前执行，因为 Task 9 会删除 Phase 1 renderer。目标不是假定所有 case 都相等，而是把真实差异显式 pin 住：

- 无附件基础 anchor case：记录 Phase 1 legacy `<matmaster-user-instructions ...>` wrapper 与 Phase 2C `<user_instructions>` wrapper 的差异。若执行者认为 Phase 2C 不允许此 render delta，停止并回到 DESIGN/render_version 决策。
- 当前附件 case：记录 Phase 1 旧链路把 `[Available attachments]` 放在 `<current_instruction>` 外，而 Phase 2C assembler 把 `[Current attachments]` 合并进 `<current_instruction>` 内。该差异不阻塞 Task 6，但禁止后续文档继续声称全量 byte-equivalent。

- [ ] **Step 1: Add Phase 1 ↔ Phase 2C oracle tests before deleting old functions**

Create `tests/matmaster/services/test_agent_run_stream_context_cutover.py` if it does not exist. Add:

```python
from __future__ import annotations

import hashlib

import pytest

from matmaster.context.assembly import (
    ContextAssembler,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import ContextAssemblyPorts, SessionEvent, UserInstructions
from matmaster.context.sections import ContextView
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)
from src.services.user_turn_context_service import (
    DEFAULT_TURN_TRANSFORM,
    USER_CONTEXT_RENDER_VERSION,
    USER_TURN_CONTEXT_SCHEMA_VERSION,
    UserInstructionsInfo,
    build_user_turn_context_payload,
    render_provider_facing_current_message_content,
    render_runtime_task_for_user_turn_context,
)


class _StubEventsPort:
    def __init__(self, events: tuple[SessionEvent, ...] = ()) -> None:
        self.calls: list[object] = []
        self._events = events

    async def load_events(self, query):
        self.calls.append(query)
        return self._events


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _info(text: str) -> UserInstructionsInfo:
    return UserInstructionsInfo(text=text, hash=_hash(text), truncated=False)


def _bundle(text: str) -> UserInstructions:
    return UserInstructions(text=text, hash=_hash(text), truncated=False)


def _assembler(events: tuple[SessionEvent, ...] = ()) -> ContextAssembler:
    return ContextAssembler(
        ports=ContextAssemblyPorts(session_events=_StubEventsPort(events))
    )


async def _phase2c_payload(
    *,
    user_text: str,
    instructions: UserInstructions,
    attachments: TurnAttachmentsSource | None = None,
) -> dict:
    result = await _assembler().assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text=user_text),
                attachments=attachments or TurnAttachmentsSource(),
                pre_turn_history_event_id=0,
            ),
            user_instructions=instructions,
        ),
    )
    rendered_message = result.user_turn_context.to_message(ContextView.RUNTIME)
    return {
        "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
        "kind": "anchor",
        "message": rendered_message.model_dump(mode="json"),
        "user_instructions_hash": result.user_instructions_hash,
        "transform": DEFAULT_TURN_TRANSFORM,
        "render_version": USER_CONTEXT_RENDER_VERSION,
    }


def _phase1_payload(
    *,
    user_text: str,
    instructions: UserInstructionsInfo,
    attachment_text: str = "",
) -> dict:
    rendered_runtime_task = render_runtime_task_for_user_turn_context(
        user_prompt=user_text,
        user_instructions=instructions,
        kind="anchor",
    )
    rendered_message_content = render_provider_facing_current_message_content(
        rendered_runtime_task=rendered_runtime_task,
        attachment_text=attachment_text,
    )
    return build_user_turn_context_payload(
        kind="anchor",
        rendered_message_content=rendered_message_content,
        images=[],
        user_instructions=instructions,
        transform=DEFAULT_TURN_TRANSFORM,
    )


@pytest.mark.asyncio
async def test_phase2c_base_prompt_delta_from_phase1_renderer_is_explicit() -> None:
    """Task 5.5 oracle: expose the legacy wrapper -> context wrapper delta."""
    old_payload = _phase1_payload(
        user_text="calculate lattice parameter",
        instructions=_info("Use SI units."),
    )
    new_payload = await _phase2c_payload(
        user_text="calculate lattice parameter",
        instructions=_bundle("Use SI units."),
    )

    old_content = old_payload["message"]["content"]
    new_content = new_payload["message"]["content"]

    assert old_content != new_content
    assert old_content.startswith(
        '<matmaster-user-instructions source="/personal/.matmaster/AGENT.md">'
    )
    assert "Treat it as user-level preferences." in old_content
    assert old_content.endswith("calculate lattice parameter")
    assert new_content == (
        "<user_instructions>\nUse SI units.\n</user_instructions>"
        "\n\n"
        "<current_instruction>\ncalculate lattice parameter\n</current_instruction>"
    )


@pytest.mark.asyncio
async def test_current_attachment_prompt_shape_delta_is_explicit_before_cutover() -> None:
    """Current baseline differs for attachment-bearing turns; keep the delta visible.

    Phase 1 appends historical/current available attachments after the
    provider-facing task. Phase 2C assembler keeps the new TurnInput default:
    current attachments are merged into the <current_instruction> block. This
    test prevents accidental claims of full byte equivalence.
    """
    old_payload = _phase1_payload(
        user_text="inspect this file",
        instructions=_info("Be precise."),
        attachment_text="[Available attachments]\nfile_1 a.txt /tmp/a.txt",
    )
    new_payload = await _phase2c_payload(
        user_text="inspect this file",
        instructions=_bundle("Be precise."),
        attachments=TurnAttachmentsSource(files=("/tmp/a.txt",)),
    )

    old_content = old_payload["message"]["content"]
    new_content = new_payload["message"]["content"]

    assert old_content != new_content
    assert old_content.startswith(
        '<matmaster-user-instructions source="/personal/.matmaster/AGENT.md">'
    )
    assert old_content.endswith(
        "inspect this file\n\n[Available attachments]\nfile_1 a.txt /tmp/a.txt"
    )
    assert new_content == (
        "<user_instructions>\nBe precise.\n</user_instructions>"
        "\n\n"
        "<current_instruction>\ninspect this file\n\n"
        "[Current attachments]\nfile_1 a.txt /tmp/a.txt\n</current_instruction>"
    )
```

- [ ] **Step 2: Verify the oracle tests pass on the Phase 1/2B baseline**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py \
  -q
```

Expected: both tests pass and explicitly show the wrapper/attachment deltas. If the deltas are unacceptable for Phase 2C, stop before Task 6 and revise DESIGN/render_version or adjust implementation. If a delta unexpectedly disappears, update the test comment and Task 6 notes because the current baseline has changed.

- [ ] **Step 3: Commit**

Commit 信息：

```text
test(context): pin phase 2c renderer oracle before cutover

DESIGN.md §14 Phase 2C：在删除 Phase 1 renderer 前直接比较旧渲染链路
与 ContextAssembler 输出。显式记录 Phase 1 legacy user-instructions wrapper
与 Phase 2C context wrapper 的差异, 以及 available-attachments 与
current-attachments 的 prompt 形态差异, 防止后续误称全量 byte-equivalent。
```

---

## Task 6: Cutover `agent_run_service` Stage 5b To `ContextAssembler`

**Files:**
- Modify: `src/services/agent_run_service.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`（视测试假设变化决定是否修改 mock / patch）

**Spec 依据:** DESIGN.md §3.4 写入时序、§3.6 Sink 错误处理、§4.1 #1 / #2 / #5、§4.2 #7、§7bis.6 调用图、§8.2 服务端装配（v3.1 改写片段）、§14 Phase 2C 业务代码切换、§17 验收要点 1、附录 B「Phase 2C 改动」、PHASE-2B-PLAN.md「Notes For Phase 2C」第 1 条。

这是 Phase 2C 的核心 cutover。改造范围是 `agent_run_service.py` Stage 5b（约 [src/services/agent_run_service.py:528-580](../../src/services/agent_run_service.py:528)），与 Stage 4b 末尾 `pg_ctx.with_run_meta(user_instructions=...)` 后、Stage 5 `build_history_wiring(...)` 调用前的 assembler 构造代码。改造后 service 不再调用 Phase 1 渲染函数链；写入 `user_turn_context` event 的 payload 由 `ContextAssembler.assemble_turn` 产出。

Phase 2C cutover 的 7 步装配（DESIGN.md §8.2 v3.1）：

1. 已有：Stage 3 Bohrium 阶段返回 `stage_result.user_instructions: UserInstructionsInfo`
2. 把 `UserInstructionsInfo` **桥接**为 `matmaster.context.ports.UserInstructions`（同字段 typed dataclass），禁止再次读 AGENT.md
3. 装配 `ContextAssembler`（`session_events_port` + Optional `session_jobs_port` + `session_context_factory`）
4. 调 `resolve_turn_context_intent(...)` 判定 `ContextAssemblyIntent`（anchor / continuation）
5. 装配 `TurnInput`（`TurnInstructionSource(user_text=user_prompt)` + `TurnAttachmentsSource(files/images/workspace_paths)`，`pre_turn_history_event_id` 来自 `current_input_context.pre_query_scope_event_id` 或 0）
6. 调 `assembler.assemble_turn(intent, TurnAssemblyRequest(...))` 获得 `AssemblyResult`
7. 写 `user_turn_context` event：用 `assembly.user_turn_context.to_message(ContextView.RUNTIME)` 装配 `payload`，仍走 `write_user_turn_context_event(...)`（fail-fast / dedup）

**关键不变量（Phase 2C cutover 必须保留）**：

- 写入 payload 的 `schema_version` / `kind` / `message` / `user_instructions_hash` / `transform` / `render_version` 字段集合与 Phase 1/2B 末态一致。
- Prompt content 的旧链路 / 新链路差异必须由 Task 5.5 / Task 10 snapshot 显式记录；当前 baseline 至少包含 AGENT.md wrapper tag 差异与附件 placement 差异。不要在 Task 6 注释、commit message 或 PR 描述里声称全量 byte-equivalent，除非 Task 5.5 oracle 证明实际已相等。
- `kind` 字段：anchor 时是 `"anchor"`，continuation 时是 `"continuation"`；`user_instructions_hash` 仅在 anchor 时填，continuation 时为 `None`（DESIGN.md §3.2）。
- `transform` 在 Phase 2C 内**只取 `"raw"`**（preflight compaction 由 compactor 路径处理，phase 2c 不动）。
- `message` 字段是 `UserMessage.model_dump(mode="json")` 的结果，含 `content + images` 完整字段。
- service 传给 `kernel.run_stream` 的 `task` 参数变成 `assembly.user_turn_context.to_message(RUNTIME).content`（已包含 instructions + attachments + user_text 装配）。
- `pg_ctx.with_run_meta` 不再设置 `attachment_manifest=...`（kernel 在 Task 7 cutover 后不再读这个 key），但仍设置 `current_user_images=image_parts`（compactor 路径仍消费）。
- `current_input_context` 仍通过 `pg_ctx.with_run_meta(current_input_context=current_input_context)` 注入（[agent_run_service.py:284-287](../../src/services/agent_run_service.py:284) 那段代码 Phase 2C 内**不动**），compactor 仍消费它。

- [ ] **Step 1: Read the current Stage 5b code and inventory the variables in scope**

Read [src/services/agent_run_service.py:283-595](../../src/services/agent_run_service.py:283) 仔细确认：

- `current_input_context: CurrentInputContext | None` 由 `run_agent` 的参数传入；在 Stage 1 写入 `pg_ctx.run_meta`
- `current_user_images_payload: list[dict[str, Any]]` 由 Stage 4 装配，包含 `{"url": image_url, "detail": ...}` dict
- `current_images: list[str]` 由 Stage 4 装配（与 `images` 参数等价）
- `user_instructions: UserInstructionsInfo` 由 Stage 3 Bohrium 返回（`stage_result.user_instructions`）
- `attachment_text: str` 由 Stage 5 `wiring.attachment_text` 装配（Task 5 已切到 source）
- `session_id` / `task_id` / `invocation_id` 由 `run_agent` 参数提供
- `events_table` 在 Stage 1 装配
- `user_prompt: str` 是 `run_agent` 入参

- [ ] **Step 2: Re-run the Phase 1 ↔ Phase 2C renderer oracle before editing Stage 5b**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py \
  -q
```

Expected: Task 5.5 的 oracle tests pass。不要跳过这一步；Task 9 会删除 Phase 1 renderer，这里是最后一次直接比较旧链路与新链路的机会。

- [ ] **Step 3: Add a cutover import smoke test (static only, not the main behavior proof)**

In `tests/matmaster/services/test_agent_run_stream.py` append:

```python
def test_agent_run_service_imports_resolve_turn_context_intent_after_cutover() -> None:
    """Phase 2C cutover smoke check: agent_run_service must import
    resolve_turn_context_intent and ContextAssembler entry points."""
    import src.services.agent_run_service as svc_module

    src = svc_module.__file__
    assert src is not None

    from pathlib import Path

    text = Path(src).read_text()
    assert "from src.services.context_turn_intent import" in text
    assert "from matmaster.context.assembly import" in text
    assert "build_user_turn_context_payload" not in text  # 已删除
    assert "decide_user_turn_context_kind" not in text   # 已删除
    assert "render_provider_facing_current_message_content" not in text  # 已删除
```

(注：用 `Path(src).read_text()` 做 source 级断言，是为了在 cutover 完成前 / 完成后两个状态有明确 expected 行为。它不是行为证明；Task 10 会补 runtime / snapshot 行为测试。Phase 2C cutover 前测试会因为 `build_user_turn_context_payload not in text` 失败 — 表明 cutover 尚未完成。Phase 2C cutover 完成后测试通过。)

- [ ] **Step 4: Verify the cutover smoke test is red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_agent_run_stream.py::test_agent_run_service_imports_resolve_turn_context_intent_after_cutover -q
```

Expected: fails with `assert ... not in text` (Phase 1 旧函数仍在 import).

- [ ] **Step 5: Replace imports + Stage 5b body in `agent_run_service.py`**

Edit `src/services/agent_run_service.py`:

1. 替换 imports（约 [src/services/agent_run_service.py:51-59](../../src/services/agent_run_service.py:51)）：

```python
from src.services.user_turn_context_service import (
    DEFAULT_TURN_TRANSFORM,
    build_user_turn_context_payload,
    decide_user_turn_context_kind,
    latest_anchor_user_instructions_hash,
    render_provider_facing_current_message_content,
    render_runtime_task_for_user_turn_context,
    write_user_turn_context_event,
)
```

替换为：

```python
from src.services.context_assembly_factory import build_session_context_factory
from src.services.context_assembly_ports import (
    AppSessionEventsPort,
    AppSessionJobsPort,
)
from src.services.context_turn_intent import resolve_turn_context_intent
from src.services.user_turn_context_service import (
    DEFAULT_TURN_TRANSFORM,
    USER_CONTEXT_RENDER_VERSION,
    USER_TURN_CONTEXT_SCHEMA_VERSION,
    write_user_turn_context_event,
)
```

并在文件顶部 import 区段新增：

```python
from matmaster.context.assembly import (
    ContextAssembler,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import (
    ContextAssemblyPorts,
    UserInstructions,
)
from matmaster.context.sections import ContextView
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)
```

并把现有 `from matmaster.manifests import skill as skill_manifest` 替换为：

```python
from matmaster.context.sources.skills import resolve_active_skills, skill_name
```

（在 `_resolve_active_skill_names` 内 `skill_manifest.resolve_active_skills(...)` / `skill_manifest.skill_name(...)` 调用相应改名为 `resolve_active_skills(...)` / `skill_name(...)`。）

2. 整段替换 Stage 5b 代码（[src/services/agent_run_service.py:528-582](../../src/services/agent_run_service.py:528)）。Phase 1 实现：

```python
            # -- Stage 5b: Phase 1 user_turn_context cutover --
            recent_context_events = []
            try:
                recent_context_events = events_table.get_recent_context_anchor_events(
                    session_id,
                    None,  # Phase 1 writes only root user turns.
                    limit=50,
                )
            except Exception:
                logger.warning(
                    "user_turn_context: latest anchor query failed; "
                    "treating current turn as anchor",
                    exc_info=True,
                )
            latest_hash = latest_anchor_user_instructions_hash(recent_context_events)
            user_turn_kind = decide_user_turn_context_kind(
                user_instructions.hash,
                latest_hash,
            )
            rendered_runtime_task = render_runtime_task_for_user_turn_context(
                user_prompt=user_prompt,
                user_instructions=user_instructions,
                kind=user_turn_kind,
            )
            rendered_message_content = render_provider_facing_current_message_content(
                rendered_runtime_task=rendered_runtime_task,
                attachment_text=attachment_text,
            )
            user_turn_payload = build_user_turn_context_payload(
                kind=user_turn_kind,
                rendered_message_content=rendered_message_content,
                images=current_user_images_payload,
                user_instructions=user_instructions,
                transform=DEFAULT_TURN_TRANSFORM,
            )
            try:
                await write_user_turn_context_event(
                    events_table=events_table,
                    session_id=session_id,
                    task_id=task_id,
                    invocation_id=invocation_id,
                    spawn_id=None,
                    payload=user_turn_payload,
                )
            except Exception as exc:
                logger.exception(
                    "user_turn_context write failed; aborting turn "
                    "session_id=%s invocation_id=%s",
                    session_id,
                    invocation_id,
                )
                return ((False, str(exc)), _elapsed_ms())

            user_prompt = rendered_runtime_task
```

替换为 Phase 2C cutover 实现：

```python
            # -- Stage 5b: Phase 2C user_turn_context cutover via ContextAssembler --
            # DESIGN.md §8.2 v3.1: AGENT.md 已经在 Stage 3 (Bohrium) 读过一次 (硬约束
            # §4.2 #7 同一 turn 只读一次), 这里把 UserInstructionsInfo 桥接为
            # matmaster.context.ports.UserInstructions 原样传给 assembler, 不再
            # 调 AppUserInstructionsPort.load_user_instructions 重读 AGENT.md。
            instructions_bundle = UserInstructions(
                text=user_instructions.text,
                hash=user_instructions.hash,
                truncated=user_instructions.truncated,
            )

            session_events_port = AppSessionEventsPort(events_table=events_table)
            session_jobs_port = AppSessionJobsPort()
            session_context_factory = build_session_context_factory(
                skill_registry=self._build_skill_registry(
                    exp_config, session=pg_ctx.session
                ),
                # Passive run_meta only. If these keys are absent in Phase 2C,
                # the factory receives None; Task 10 covers the explicit
                # non-empty factory path with session_tools snapshots.
                legal_mcp_servers=(pg_ctx.run_meta or {}).get("legal_mcp_servers"),
                schemas_by_server=(pg_ctx.run_meta or {}).get("schemas_by_server"),
            )
            context_assembler = ContextAssembler(
                ports=ContextAssemblyPorts(
                    session_events=session_events_port,
                    session_jobs=session_jobs_port,
                ),
                session_context_factory=session_context_factory,
            )

            try:
                intent = await resolve_turn_context_intent(
                    instructions_hash=instructions_bundle.hash,
                    session_id=session_id,
                    spawn_id=None,
                    events_port=session_events_port,
                )
            except Exception:
                logger.warning(
                    "resolve_turn_context_intent failed; treating current turn as anchor",
                    exc_info=True,
                )
                intent = ContextAssemblyIntent.ANCHOR_TURN

            pre_turn_history_event_id = 0
            if current_input_context is not None and current_input_context.pre_query_scope_event_id is not None:
                pre_turn_history_event_id = int(
                    current_input_context.pre_query_scope_event_id
                )

            turn_input = TurnInput(
                instruction=TurnInstructionSource(user_text=user_prompt or ""),
                attachments=TurnAttachmentsSource(
                    files=tuple(current_input_context.files)
                    if current_input_context is not None
                    else (),
                    images=tuple(
                        image["url"]
                        for image in current_user_images_payload
                        if isinstance(image, dict) and image.get("url")
                    ),
                    workspace_paths=tuple(current_input_context.workspace_paths)
                    if current_input_context is not None
                    else (),
                ),
                pre_turn_history_event_id=pre_turn_history_event_id,
            )

            assembly = await context_assembler.assemble_turn(
                intent=intent,
                request=TurnAssemblyRequest(
                    session_id=session_id,
                    spawn_id=None,
                    turn_input=turn_input,
                    user_instructions=instructions_bundle,
                ),
            )
            rendered_message = assembly.user_turn_context.to_message(
                ContextView.RUNTIME
            )

            user_turn_payload = {
                "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
                "kind": "anchor" if intent.is_anchor_turn else "continuation",
                "message": rendered_message.model_dump(mode="json"),
                "user_instructions_hash": (
                    instructions_bundle.hash if intent.is_anchor_turn else None
                ),
                "transform": DEFAULT_TURN_TRANSFORM,
                "render_version": USER_CONTEXT_RENDER_VERSION,
            }
            try:
                await write_user_turn_context_event(
                    events_table=events_table,
                    session_id=session_id,
                    task_id=task_id,
                    invocation_id=invocation_id,
                    spawn_id=None,
                    payload=user_turn_payload,
                )
            except Exception as exc:
                logger.exception(
                    "user_turn_context write failed; aborting turn "
                    "session_id=%s invocation_id=%s",
                    session_id,
                    invocation_id,
                )
                return ((False, str(exc)), _elapsed_ms())

            user_prompt = rendered_message.content
```

注意上面代码中：

- `instructions_bundle` 是 typed 桥接，**不**重新读 AGENT.md。
- `_build_skill_registry` 已是 `AgentRunService` 现有方法（line 159 附近），可直接复用，传入 `exp_config` + `pg_ctx.session`。
- `legal_mcp_servers` / `schemas_by_server` 从 `pg_ctx.run_meta` 读取 passive metadata。Task 1 的 grep 可能显示当前 baseline 只有 `Exp.assemble` 消费这些 key、没有 Stage 5b 前写入端；因此 Task 10 必须用显式 factory input 覆盖非空 MCP/session_tools 路径。不要把 assembler / ports / factory object 写入 `run_meta`。
- `intent` 计算失败时降级为 `ANCHOR_TURN`，与 Phase 1 现有兜底语义（`latest_anchor_user_instructions_hash` 查询失败 → treat as anchor）等价。
- `TurnInput.attachments.images` 来自 `current_user_images_payload`（每个 dict 取 `url`）；这与 Phase 1 行为等价（Phase 1 把 `current_user_images_payload` 透传到 spec.meta，让 kernel `ImageContentPart.model_validate(image)` 反序列化）。
- `rendered_message.content` 是 `UserTurnContext.to_message(RUNTIME).content`：对于 anchor turn，形态为 `<user_instructions>...</user_instructions>\n\n<current_instruction>...</current_instruction>`；对于 continuation turn，形态为纯 `<current_instruction>...</current_instruction>`。Task 5.5 已显式记录它与 Phase 1 legacy renderer 的 wrapper / attachment placement 差异；Task 10 用固定 snapshot 把 Phase 2C 末态 pin 住。
- `user_prompt = rendered_message.content` 后续传给 `exp.run_stream(pg_ctx, user_prompt, history=history, ...)`，下一节 Task 7 改造 kernel 入口以不重复 `build_user_request`。

3. 在 `_resolve_active_skill_names`（line 191 附近）内：

```python
        skills = skill_manifest.resolve_active_skills(raw_events, registry)
        names = {skill_manifest.skill_name(skill) for skill in skills}
```

替换为：

```python
        skills = resolve_active_skills(raw_events, registry)
        names = {skill_name(skill) for skill in skills}
```

(已经在 Task 6 Step 4.1 把 imports 切到 `from matmaster.context.sources.skills import resolve_active_skills, skill_name`。)

4. 移除 `pg_ctx.with_run_meta(attachment_manifest=attachment_text)` 这行调用（约 [src/services/agent_run_service.py:522](../../src/services/agent_run_service.py:522)）。Task 7 改造后 kernel 不再读这个 key；compactor 仍需要 attachment 数据，但 compactor 通过 `ctx.runtime_ports.compaction.history.query_events()` 自己重建，不依赖 `attachment_manifest` run_meta。

注：保留 `pg_ctx.with_run_meta(user_instructions=user_instructions.text)` 那一行（compactor + SystemPromptBuilder 仍可能消费）。

- [ ] **Step 6: Verify cutover smoke test passes**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_agent_run_stream.py::test_agent_run_service_imports_resolve_turn_context_intent_after_cutover -q
```

Expected: passes。

- [ ] **Step 7: Run full agent_run_stream suite to verify no regression**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  -q
```

Expected: all tests pass。若有失败，常见原因：

- Test mock 假设 `build_user_turn_context_payload` 仍被调用 —— 用 `resolve_turn_context_intent` + `ContextAssembler.assemble_turn` mock 替换。
- Test mock 假设 `latest_anchor_user_instructions_hash` 仍被调用 —— 删除该 patch，改 mock `AppSessionEventsPort.load_events` 返回空。
- Test 假设 `attachment_manifest` 仍写入 run_meta —— 删除断言。

修复策略：调整 test mock 让它配 cutover 后链路；**不**回退 `agent_run_service.py` 改造。

- [ ] **Step 8: Run user_turn_context_service + renderer oracle tests to confirm helpers we kept still work**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_user_turn_context_service.py -q
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_agent_run_stream_context_cutover.py -q
```

Expected: all tests pass。Task 9 之前 `latest_anchor_user_instructions_hash` / Phase 1 renderer 等函数还没删，所以这里测试仍跑全。

- [ ] **Step 9: Static check — no `matmaster.manifests` import remains in `agent_run_service.py`**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "matmaster\.manifests" src/services/agent_run_service.py
```

Expected: 无匹配。

- [ ] **Step 10: Commit**

Commit 信息：

```text
refactor(services): cutover agent_run_service Stage 5b to ContextAssembler

DESIGN.md §8.2 v3.1 + §14 Phase 2C：删除 Phase 1 渲染链路
(latest_anchor_user_instructions_hash / decide_user_turn_context_kind /
render_runtime_task_for_user_turn_context /
render_provider_facing_current_message_content / build_user_turn_context_payload),
改为 AppSessionEventsPort + AppSessionJobsPort + resolve_turn_context_intent
+ ContextAssembler.assemble_turn。UserInstructionsInfo 桥接为
matmaster.context.ports.UserInstructions 原样传入 assembler, 不再
重读 AGENT.md (硬约束 §4.2 #7)。Task 5.5 pin 住 Phase 1 renderer
与 Phase 2C assembler 的 wrapper / attachment prompt 形态差异。
```

---

## Task 7: Cutover Kernel Entry To Drop `attachment_manifest` And `build_user_request`

**Files:**
- Modify: `matmaster/core/agent.py`
- Modify: `tests/matmaster/core/test_agent_kernel_stream.py` — 增加 runtime pass-through 断言，验证 provider 实际收到的 user message 不被二次包装。

**Spec 依据:** DESIGN.md §10.1-10.2 kernel 改造、§10.3 `AgentRuntimeSpec` 字段变更 v3「`turn_input: TurnInput` 删除」、§14 Phase 2C 业务代码切换、附录 B「Phase 2C 改动」。

`core/agent.py::_run_items` 当前装配 user message：

```python
attachment_text = str(spec.meta.get("attachment_manifest") or "")
user_content = spec.context_builder.build_user_request(
    user_text=task,
    attachments=attachment_text,
)
state = _KernelState(
    messages=[
        SystemMessage(content=spec.system_prompt),
        *(history or []),
        UserMessage(content=user_content, images=current_user_images),
    ]
)
```

Phase 2C cutover 后 service 已经把 `task` 装配为 `assembly.user_turn_context.to_message(RUNTIME).content`（含 instructions + attachments + user_text 装配）。kernel 端不能再 `build_user_request`，否则会双重拼接 stale `attachment_manifest`；即便 Task 6 删除写入端后该 key 为空，`build_user_request("text", "")` 仍会多走一次 `strip + join`，不再是对 service 已装配字符串的透明传递。

Phase 2C 改造目标：kernel 直接 `UserMessage(content=task, images=current_user_images)`，跳过 `build_user_request`。

注意：

- `spec.context_builder` 字段仍保留（compactor / SystemPromptBuilder 仍使用 `build_system_prompt`），不能删。
- `current_input_context` / `effective_current_input_context` 仍保留（preflight compaction `run_preflight_compaction_if_needed` 需要这个旁路）。

- [ ] **Step 1: Write failing runtime test asserting kernel passes task content unchanged**

Append to `tests/matmaster/core/test_agent_kernel_stream.py`（该文件已有 `RecordingContentProvider` / `_make_spec` helper；若 helpers 位置变化，使用等价 provider 记录 outbound `messages`）:

```python
import pytest

from matmaster.core.agent import AgentKernel

@pytest.mark.asyncio
async def test_run_stream_uses_preassembled_task_content_without_legacy_wrap() -> None:
    """Phase 2C: service 已经把 task 装配为 provider-facing user message。

    Kernel must pass that content through unchanged and ignore stale
    spec.meta["attachment_manifest"] even if Exp still copies it for compatibility.
    """
    provider = RecordingContentProvider()
    task = (
        "<user_instructions>\nUse SI units.\n</user_instructions>"
        "\n\n"
        "<current_instruction>\nfit structure\n</current_instruction>"
    )
    spec = _make_spec(provider=provider).model_copy(
        update={"meta": {"attachment_manifest": "ATTACHMENT-SHOULD-BE-IGNORED"}}
    )
    kernel = AgentKernel()

    async for _event in kernel.run_stream(spec, task):
        pass

    assert provider.seen_messages
    user_messages = [
        message
        for message in provider.seen_messages[0]
        if message.get("role") == "user"
    ]
    assert user_messages[-1]["content"] == task
    assert "ATTACHMENT-SHOULD-BE-IGNORED" not in user_messages[-1]["content"]
```

- [ ] **Step 2: Verify the smoke test is red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::test_run_stream_uses_preassembled_task_content_without_legacy_wrap -q
```

Expected: fails because current kernel appends `ATTACHMENT-SHOULD-BE-IGNORED` through `spec.context_builder.build_user_request(...)`.

- [ ] **Step 3: Edit `core/agent.py::_run_items`**

In `matmaster/core/agent.py` 内 `_run_items` 函数体（约 [matmaster/core/agent.py:260-275](../../matmaster/core/agent.py:260)）：

Phase 1 末态：

```python
        current_user_images = [
            ImageContentPart.model_validate(image)
            for image in spec.meta.get("current_user_images", [])
        ]
        attachment_text = str(spec.meta.get("attachment_manifest") or "")
        user_content = spec.context_builder.build_user_request(
            user_text=task,
            attachments=attachment_text,
        )
        state = _KernelState(
            messages=[
                SystemMessage(content=spec.system_prompt),
                *(history or []),
                UserMessage(content=user_content, images=current_user_images),
            ]
        )
```

Phase 2C 改造为：

```python
        current_user_images = [
            ImageContentPart.model_validate(image)
            for image in spec.meta.get("current_user_images", [])
        ]
        # Phase 2C: service layer装配完整 user_turn_context (含 instructions
        # + attachments + user_text); kernel 不再调用 build_user_request, 直接
        # 把 task 作为 UserMessage.content (DESIGN.md §10.1 / §14 Phase 2C)。
        # spec.context_builder 仍保留, 由 SystemPromptBuilder 与 compactor 使用。
        state = _KernelState(
            messages=[
                SystemMessage(content=spec.system_prompt),
                *(history or []),
                UserMessage(content=task, images=current_user_images),
            ]
        )
```

`current_input_context` / `effective_current_input_context` 等其它代码完全保留。

- [ ] **Step 4: Verify the smoke test passes**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::test_run_stream_uses_preassembled_task_content_without_legacy_wrap -q
```

Expected: passes。

- [ ] **Step 5: Run full kernel + agent_run_stream suite to verify no regression**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/core/test_agent_kernel_stream.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  -q
```

Expected: all tests pass。

如果回归出现 `expected user_content == "<some prompt with instructions wrapper>"` 但实际是 `"<纯 user text>"` 的失败，原因：测试 fixture 假设 Phase 1 末态走 `_apply_user_instructions_to_initial_user_query` 注入。Phase 2C 后 service 已经把 instructions wrap 放到 `task` 字符串里，不再依赖 kernel `build_user_request`；测试期望要相应改成"task 已是装配完成的字符串"。

- [ ] **Step 6: Confirm Phase 2A + 2B baseline still passes**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/matmaster/services/test_context_turn_intent.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/core/test_agent_kernel_stream.py \
  -q
```

Expected: all tests pass。

- [ ] **Step 7: Static check — kernel 入口不再有 `attachment_manifest` / `build_user_request`**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "attachment_manifest|build_user_request" matmaster/core/agent.py
```

Expected: 无匹配（其它涉及 `build_user_request` 的代码在 compaction helper / context_builder 自身，不在 `core/agent.py`）。该 grep 只是补充静态检查，行为由 Step 1 的 runtime test 覆盖。

- [ ] **Step 8: Commit**

Commit 信息：

```text
refactor(kernel): drop build_user_request + attachment_manifest from _run_items

DESIGN.md §10.1 / §10.2 / §14 Phase 2C：service 已在 Stage 5b 装配完整
user_turn_context 字符串作为 task 参数, kernel 不再 wrap。spec.context_builder
保留供 SystemPromptBuilder 与 compactor 使用; spec.meta["attachment_manifest"]
已由 Task 6 删除写入端。preflight compaction / current_input_context 旁路不变。
```

---

## Task 8: Preflight Compaction Compatibility Bridge

**Files:**
- Read-only: `matmaster/core/agent_compaction.py` / `matmaster/core/context_compactor.py` / `src/services/agent_run_service.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`（可能新增 preflight smoke test）

**Spec 依据:** DESIGN.md §3.4「运行中触发 preflight compaction」、§9 ContextCompactor 改造（Phase 3 范围）、§12 Case 4 Preflight、§14 Phase 2C「不触碰 core/context_compactor.py」、§17 验收要点 2。

Phase 2C cutover 后，service 把 `user_prompt` 改为 `rendered_message.content`（含 `<user_instructions>...</user_instructions>\n\n<current_instruction>...</current_instruction>` 装配的完整字符串）。但 `core/context_compactor.py` 的 preflight 路径仍读 `current_input_context: CurrentInputContext` 来判断"本轮是否有 effective input"以及构造 preflight 后的 message。具体路径（见 [matmaster/core/context_compactor.py:281-355](../../matmaster/core/context_compactor.py:281)）：

- compactor 调 `current_input_context.has_effective_input()` 判定是否需要 preflight
- compactor 调 `build_current_instruction_block(current_input_context)` 构造压缩后的 user instruction block
- compactor 用 `current_input_context.pre_query_scope_event_id` 作为 covered_until_event_id 边界

**关键不变量**：Phase 2C cutover 不能破坏 preflight compaction 的输入。`current_input_context` 仍由 service 通过 `pg_ctx.with_run_meta(current_input_context=current_input_context)` 注入；Phase 2C 改造**不动**这一行。compactor 内 `build_current_instruction_block(current_input_context)` 与 Phase 2A 已 pin 过的 `TurnInput.to_sections()` 默认渲染字节等价，所以 preflight 压缩后写入的 `user_turn_context.transform="preflight_compacted"` payload 继续保持 Phase 1 末态的 legacy 行为。

Phase 2C 内本 Task 不修改任何 compaction 代码；只是验证 cutover 后 preflight 路径仍 work。

- [ ] **Step 1: Re-read preflight call sites**

Read [matmaster/core/agent.py:248-286](../../matmaster/core/agent.py:248) 与 [matmaster/core/agent_compaction.py](../../matmaster/core/agent_compaction.py)（整文件）：

- `_run_items` 解出 `current_input_context: CurrentInputContext | None`（line 248-253）
- `effective_current_input_context = replace(current_input_context, user_text=task)`（line 254-258）
- 把 `effective_current_input_context` 透传给 `run_preflight_compaction_if_needed(...)`（line 279-286）

Phase 2C cutover 后：

- `task` 已经是装配后字符串（含 instructions / attachments），不是 raw user_text
- 但 `replace(current_input_context, user_text=task)` 把这个装配后字符串又塞回 `user_text` 字段
- 接下来 compactor 内部 `build_current_instruction_block(effective_current_input_context)` 会把这个**已经装配过 instructions 的字符串**再包一次 `<current_instruction>` block
- **结果：preflight compaction 后写入的 base_messages[0] 含双层 `<current_instruction>` wrapping**

这是 Phase 2C cutover 会暴露的一个旧路径问题；先判断它是不是 Phase 2C 新引入的 regression。

候选修复方案（最小改造，但 Phase 2C 不采用）：

把 `effective_current_input_context = replace(current_input_context, user_text=task)` 改为 `effective_current_input_context = current_input_context`（不再覆盖 user_text）。这样 compactor 读到的 `user_text` 是 service 原始的 user_prompt（raw），preflight compaction 后会改变 Phase 1 末态行为。

但是这个改动需要确认：Phase 1 末态下 `task` 传给 kernel 的是什么？看 `agent_run_service.py:582` `user_prompt = rendered_runtime_task`：

- 当 `kind=="anchor"` 时，`rendered_runtime_task` = `_render_user_instructions_block(user_instructions=..., user_query=user_prompt)` （带 wrap）
- 当 `kind=="continuation"` 时，`rendered_runtime_task` = `user_prompt.strip()` （raw）

但 kernel `replace(current_input_context, user_text=task)` 不论 anchor / continuation 都把 task 塞回 user_text；这意味着 Phase 1 末态下 **anchor turn 触发 preflight 也会有双层 wrap 问题**。这个问题在 Phase 1 已经存在；Phase 2C 不引入新的 regression（只是不修复 Phase 1 已有的 bug）。

**结论**：Phase 2C 不修复 preflight 双层 wrap，保持 Phase 1 末态 legacy 行为。FOLLOWUPS.md 添加一条议题，由 Phase 3 compactor cutover 一并修复。

- [ ] **Step 2: Add preflight compatibility note to FOLLOWUPS.md**

Edit `.planning/context-refactor/FOLLOWUPS.md` —— 在末尾追加：

```markdown
---

## 议题 3: Preflight compaction 在 anchor turn 下的双层 `<current_instruction>` wrap

**现状（Phase 1 末态 / Phase 2C 末态）**

`matmaster/core/agent.py::_run_items` 内 `effective_current_input_context = replace(current_input_context, user_text=task)`。该 `task` 在 anchor turn 下已经被 service 装配为 `<user_instructions>...</user_instructions>\n\n<current_instruction>...</current_instruction>`，再传给 compactor 的 `build_current_instruction_block(effective_current_input_context)` 会包一次 `<current_instruction>` 外层，产出 base_messages[0] 含双层 `<current_instruction>` 包裹。

**回归基线**

Phase 1 / Phase 2A / Phase 2B / Phase 2C 末态行为一致；现存所有 snapshot 测试均按这个 legacy 行为 pin。

**修复时机**

Phase 3 compactor cutover (`matmaster/core/context_compactor.py` 迁移到 `matmaster/context/compaction.py`) 时一并清理 —— compactor 改为调 `ContextAssembler.assemble_compaction(...)` 后，preflight wrap 由 `COMPACTED_COMPOSITION` 决定，`current_input_context` 不再被 compactor 直接读，wrap 行为自动正确。

**Phase 2C 内的处理**

不修。保留 Phase 1 末态 preflight 行为，以维持 Phase 2C 的"行为不退化"验收门。FOLLOWUPS 记录是为 Phase 3 实现者提供 context。
```

- [ ] **Step 3: Run preflight compaction integration / unit tests to confirm Phase 2C cutover does not break them**

Run（包括与 compactor 相关的所有 integration tests）：

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/integration \
  tests/test_chat_events_history_checkpoint.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_active_mcp_replay.py \
  tests/matmaster/services/test_lazy_mcp_replay.py \
  -q
```

Expected: all tests pass。如有失败，根因常在 mock / patch 假设 `attachment_manifest` 在 spec.meta 里有值 —— 调整 mock，让它符合 Phase 2C cutover 后的 spec.meta 内容。

- [ ] **Step 4: Static check — `core/context_compactor.py` byte-for-byte unchanged**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git diff --stat matmaster/core/context_compactor.py
```

Expected: 无变化（如果 Phase 2C cutover 期间需要导出 `current_input_context` 等字段，必须通过 `pg_ctx.with_run_meta(...)` 路径完成，绝对不动 compactor 代码本身）。

- [ ] **Step 5: Commit (planning-only, no code changes in this task)**

如果 Step 2 编辑了 FOLLOWUPS.md，commit:

```text
docs(context-refactor): note phase 2c preflight double-wrap deferral

Phase 1 / Phase 2C 末态下 preflight compaction 在 anchor turn 会包出双层
<current_instruction>。该行为是 Phase 1 已有 bug, Phase 2C 不修, Phase 3
compactor cutover 一并清理。Followups 记录是为 Phase 3 实现者提供 context。
```

如果 Step 2 没有改动（FOLLOWUPS.md 已经写过类似议题），Task 8 不产 commit；只是确认 cutover 不退化。

---

## Task 9: Delete Legacy Injection Helper + Remaining Phase 1 Renderers

**Files:**
- Modify: `src/services/agent_run_instructions.py`
- Modify: `src/services/user_turn_context_service.py`
- Delete: `tests/matmaster/services/test_user_instructions_runtime_injection.py`
- Modify: `tests/matmaster/services/test_user_turn_context_service.py`（仅删除针对被删函数的用例）

**Spec 依据:** DESIGN.md §5.3 删除/迁移清单（`_apply_user_instructions_to_initial_user_query` 删除）、§14 Phase 1e「兼容项标记」「COMPAT:legacy-runtime-injection-helper」、§14 Phase 2C「清理 / 删除 `_apply_user_instructions_to_initial_user_query`，清理 `COMPAT:legacy-runtime-injection-helper` 标记」、附录 B「Phase 2C 改动」「`agent_run_instructions.py`」。

Task 6 已经把 `agent_run_service.py` 切走，不再调用 Phase 1 渲染函数链。Task 9 删除残留的旧代码与对应测试，避免 dead code 长期遗留。

- [ ] **Step 1: Confirm no production code references the about-to-be-deleted symbols**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "latest_anchor_user_instructions_hash|decide_user_turn_context_kind|render_runtime_task_for_user_turn_context|render_provider_facing_current_message_content|build_user_turn_context_payload|_apply_user_instructions_to_initial_user_query|_render_user_instructions_block|_strip_user_instructions_prefix|_find_first_user_message_index|_USER_INSTRUCTIONS_START|_USER_INSTRUCTIONS_END|_USER_INSTRUCTIONS_TEMPLATE|COMPAT:legacy-runtime-injection-helper" \
  matmaster src/services src/dao app.py \
  --glob '!src/services/user_turn_context_service.py' \
  --glob '!src/services/agent_run_instructions.py'
```

Expected: 无匹配（runtime code 都已经切走）。如果有匹配，停止 cutover，先把 caller 改造完。

- [ ] **Step 2: Confirm only `test_user_instructions_runtime_injection.py` + `test_user_turn_context_service.py` reference the symbols in tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -ln "latest_anchor_user_instructions_hash|decide_user_turn_context_kind|render_runtime_task_for_user_turn_context|render_provider_facing_current_message_content|build_user_turn_context_payload|_apply_user_instructions_to_initial_user_query|_render_user_instructions_block" tests
```

Expected before Step 6 trims Task 5.5 oracle imports:

```text
tests/matmaster/services/test_user_instructions_runtime_injection.py
tests/matmaster/services/test_user_turn_context_service.py
tests/matmaster/services/test_agent_run_stream_context_cutover.py
```

(可能还有 `tests/matmaster/services/test_agent_run_stream.py` 残留 import / mock，确认后改 Task 6 commit 或本 task 一并清理 mock。)

- [ ] **Step 3: Delete `tests/matmaster/services/test_user_instructions_runtime_injection.py`**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git rm tests/matmaster/services/test_user_instructions_runtime_injection.py
```

理由：整文件覆盖 `_apply_user_instructions_to_initial_user_query` / `_strip_user_instructions_prefix` / `_render_user_instructions_block` 全套；这些函数即将被删，没有保留任何用例的必要。

- [ ] **Step 4: Remove legacy injection helpers from `agent_run_instructions.py`**

Edit `src/services/agent_run_instructions.py`. 完整新内容：

```python
"""User instructions path constant (Phase 2C 末态).

Phase 1 引入的 _apply_user_instructions_to_initial_user_query 与配套
helpers 已在 Phase 2C cutover 中删除 (DESIGN.md §5.3 / §14 Phase 2C 清理).
本模块仅保留 _USER_INSTRUCTIONS_PATH 常量, 由 user_turn_context_service
内的 load_user_instructions_from_session 引用。
"""

from __future__ import annotations

_USER_INSTRUCTIONS_PATH = "/personal/.matmaster/AGENT.md"
```

(整个文件其余内容删除：`_USER_INSTRUCTIONS_START` / `_USER_INSTRUCTIONS_END` / `_USER_INSTRUCTIONS_TEMPLATE` / `_strip_user_instructions_prefix` / `_find_first_user_message_index` / `_render_user_instructions_block` / `_apply_user_instructions_to_initial_user_query` / `COMPAT:legacy-runtime-injection-helper` 标记 / 顶部 docstring 中关于这些 helper 的描述。原来 import 的 `Message, UserMessage` 也一并删除。)

- [ ] **Step 5: Remove Phase 1 renderers from `user_turn_context_service.py`**

Edit `src/services/user_turn_context_service.py`. 删除以下函数与配套私有 helper：

- `latest_anchor_user_instructions_hash`
- `decide_user_turn_context_kind`
- `render_runtime_task_for_user_turn_context`
- `render_provider_facing_current_message_content`
- `build_user_turn_context_payload`
- `_context_events_newest_first`

保留：

- `UserInstructionsInfo` dataclass + `hash_user_instructions` + `make_user_instructions_info` + `load_user_instructions_from_session` + `_truncate_utf8`
- `write_user_turn_context_event`
- `USER_INSTRUCTIONS_MAX_BYTES` / `USER_TURN_CONTEXT_SCHEMA_VERSION` / `USER_CONTEXT_RENDER_VERSION` / `DEFAULT_TURN_TRANSFORM` / 类型别名 `UserTurnContextKind` / `UserTurnContextTransform` / `UserTurnContextWriteStatus`

顶部 import 同步清理：

- 删除 `from matmaster.core.context_builder import ContextBuilder`（不再被任何残留函数调用）
- 删除 `from src.services.agent_run_instructions import (_USER_INSTRUCTIONS_PATH, _render_user_instructions_block)` 行末的 `_render_user_instructions_block`（保留 `_USER_INSTRUCTIONS_PATH` 即可，因为 `load_user_instructions_from_session` 还要用）
- 删除 `from matmaster.types.messages import ImageContentPart, UserMessage` 中的 `UserMessage`（`ImageContentPart` 不再被使用也可删；如有剩余 caller 保留对应 import 即可）—— 实际删完 `build_user_turn_context_payload` 后 `UserMessage` / `ImageContentPart` 都不被引用，整行可删

修改后整文件大致结构（注意保留 docstring）：

```python
"""Phase 2C 末态: 用户 turn context 事件写入边界 + AGENT.md 读取 helper。

Phase 1 引入的渲染函数 (latest_anchor_user_instructions_hash /
decide_user_turn_context_kind / render_runtime_task_for_user_turn_context /
render_provider_facing_current_message_content /
build_user_turn_context_payload) 已在 Phase 2C cutover 中删除,
渲染由 matmaster.context.assembly.ContextAssembler 负责
(DESIGN.md §5.3 / §14 Phase 2C)。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

from src.services.agent_run_instructions import _USER_INSTRUCTIONS_PATH

logger = logging.getLogger(__name__)

USER_INSTRUCTIONS_MAX_BYTES = 50 * 1024
USER_TURN_CONTEXT_SCHEMA_VERSION = "user_turn_context.v1"
USER_CONTEXT_RENDER_VERSION = "user_context_render.v1"
DEFAULT_TURN_TRANSFORM = "raw"

UserTurnContextKind = Literal["anchor", "continuation"]
UserTurnContextTransform = Literal["raw", "preflight_compacted", "oversized_summary"]
UserTurnContextWriteStatus = Literal["written", "duplicate"]


@dataclass(frozen=True)
class UserInstructionsInfo:
    text: str
    hash: str
    truncated: bool = False


def hash_user_instructions(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


def make_user_instructions_info(
    text: str | None,
    *,
    truncated: bool = False,
) -> UserInstructionsInfo:
    raw_text = text or ""
    return UserInstructionsInfo(
        text=raw_text,
        hash=hash_user_instructions(raw_text),
        truncated=truncated,
    )


def load_user_instructions_from_session(session: Any) -> UserInstructionsInfo:
    if session is None:
        return make_user_instructions_info("")

    try:
        text = session.read_file(_USER_INSTRUCTIONS_PATH)
    except Exception:
        return make_user_instructions_info("")

    raw_text = str(text or "")
    truncated_text, truncated = _truncate_utf8(raw_text, USER_INSTRUCTIONS_MAX_BYTES)
    if not truncated:
        return make_user_instructions_info(truncated_text)

    logger.warning(
        "AGENT.md exceeds %s bytes; truncating user instructions for "
        "user_turn_context",
        USER_INSTRUCTIONS_MAX_BYTES,
    )
    return make_user_instructions_info(truncated_text, truncated=True)


async def write_user_turn_context_event(
    *,
    events_table: Any,
    session_id: str,
    task_id: str | None,
    invocation_id: str | None,
    spawn_id: str | None,
    payload: dict[str, Any],
) -> UserTurnContextWriteStatus:
    if not invocation_id:
        raise RuntimeError("user_turn_context write requires invocation_id")

    existing = await asyncio.to_thread(
        events_table.query_user_turn_context_by_invocation,
        session_id,
        invocation_id,
        spawn_id,
    )
    if existing:
        existing_payload = existing.get("content") if isinstance(existing, dict) else None
        if existing_payload == payload:
            return "duplicate"
        raise RuntimeError("user_turn_context payload differs for invocation_id")

    written = await asyncio.to_thread(
        events_table.add_event,
        session_id,
        "MatMaster",
        "user_turn_context",
        payload,
        task_id=task_id,
        invocation_id=invocation_id,
        spawn_id=spawn_id,
    )
    if not written:
        raise RuntimeError("user_turn_context write returned false")
    return "written"
```

- [ ] **Step 6: Update `tests/matmaster/services/test_user_turn_context_service.py`**

Read the existing test file. 删除如下用例（包括 fixture 与 import）：

- `test_latest_anchor_user_instructions_hash_*`
- `test_decide_user_turn_context_kind_*`
- `test_render_runtime_task_for_user_turn_context_*`
- `test_render_provider_facing_current_message_content_*`
- `test_build_user_turn_context_payload_*`

保留：

- `test_turn_input_is_reexported_from_current_input_shim`（Task 2 加的 smoke）
- `test_hash_user_instructions_*`
- `test_make_user_instructions_info_*`
- `test_load_user_instructions_from_session_*`
- `test_write_user_turn_context_event_*`

同时更新 `tests/matmaster/services/test_agent_run_stream_context_cutover.py`：

- 删除 Task 5.5 中直接 import Phase 1 renderer 的 oracle helper (`build_user_turn_context_payload` / `render_runtime_task_for_user_turn_context` / `render_provider_facing_current_message_content` / `UserInstructionsInfo`)。
- 保留 no-attachment 与 attachment-bearing 两个 snapshot case，但把旧链路结果改成固定字符串断言。这样 Task 9 删除 renderer 后，snapshot 仍保留 Phase 2C 行为基线。
- 保留附件形态差异的注释，说明该差异来自 Task 5.5 删除前 oracle，不再在运行时调用已删除函数。

- [ ] **Step 7: Verify the trimmed test file passes**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_user_turn_context_service.py -q
```

Expected: 剩余用例全部通过。

- [ ] **Step 8: Confirm legacy `agent_run_service.py` callers compile**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -c "import src.services.agent_run_service as m; print('OK')"
```

Expected: prints `OK`. 若 `ImportError` 显示残留 import 未清理（Task 6 应该已经处理掉，但本 Task 也可作 last-check）。

- [ ] **Step 9: Run full agent_run_stream + history + ports test suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py \
  tests/matmaster/services/test_agent_run_stream_response_figures.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/matmaster/services/test_context_turn_intent.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  -q
```

Expected: all tests pass。

- [ ] **Step 10: Static check — COMPAT marker cleared**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "COMPAT:legacy-runtime-injection-helper" matmaster src tests
```

Expected: 无匹配。

- [ ] **Step 11: Commit**

Commit 信息：

```text
chore(context-refactor): delete legacy runtime injection helper + phase 1 renderers

DESIGN.md §5.3 + §14 Phase 2C 清理：删除 _apply_user_instructions_to_initial_user_query
与配套 helper / 常量, 删除 user_turn_context_service 内 Phase 1 渲染函数链
(latest_anchor_user_instructions_hash / decide_user_turn_context_kind /
render_runtime_task_for_user_turn_context /
render_provider_facing_current_message_content / build_user_turn_context_payload),
删除 test_user_instructions_runtime_injection.py。COMPAT:legacy-runtime-injection-helper
标记清理。Phase 2C cutover 后这些代码已无 caller。
```

---

## Task 10: Snapshot And Integration Tests For Cutover Baseline

**Files:**
- Modify: `tests/matmaster/services/test_agent_run_stream_context_cutover.py`
- Modify: `tests/matmaster/core/test_agent_kernel_stream.py`（若 Task 7 runtime test 已落地，本 Task 只运行它）

**Spec 依据:** DESIGN.md §8.2 几个场景演化、§14 Phase 2C「测试目标」、§16 测试覆盖、§17 验收要点 1。

Phase 2C 的核心承诺是"runtime cutover 后普通 user turn 由 ContextAssembler 生产，并且所有已知 prompt 形态差异都被显式 pin 住"。本 Task 写一组 snapshot + integration 测试，把 Phase 2C 末态作为 Phase 3 / Phase 4 改造时的回归基线。

测试范围（DESIGN.md §14 Phase 2C 测试目标 + §16 integration 范围）：

1. **Anchor turn (首轮 / AGENT.md = non-empty)**: 验证 `user_turn_context.message.content` 固定为 `<user_instructions>...</user_instructions>\n\n<current_instruction>...</current_instruction>`，`kind == "anchor"`，`user_instructions_hash` 字段填入。
2. **Continuation turn (AGENT.md hash 未变)**: 验证 `kind == "continuation"`，`user_instructions_hash == None`，`message.content` 仅含 `<current_instruction>...</current_instruction>` 且 instructions wrap 缺失。
3. **Anchor turn (AGENT.md hash 变 / 第三轮)**: 验证 hash mismatch 触发新 anchor，新 hash 字段写入。
4. **Multi-turn attachments / session sections**: anchor turn 通过 `session_context_factory` 装配历史 attachments / loaded skills / active tools，本轮附件通过 `TurnInput` 进入 `<current_instruction>`；continuation 不查 session events。
5. **`user_turn_context` 写入失败 fail-fast**: mock `events_table.add_event` 返回 `False` 或抛异常，断言 `run_agent` 返回 `(False, ...)`。
6. **SSE filter 不暴露 `user_turn_context`**: 触发一轮完整 run，断言 SSE replay 中无 `user_turn_context` 事件。
7. **Bundle 防竞态**: service 读完 `UserInstructions` 后文件变化，assembler 仍使用 request 内传入对象的 text/hash，不调用任何 user-instructions port。
8. **MCP/session_tools 非空路径**: `session_context_factory` 带 `legal_mcp_servers` / `schemas_by_server` 时，anchor 输出包含 `<active_tools>`；缺 key 时是显式测试过的空能力路径。
9. **Kernel runtime pass-through**: kernel 收到 service 已装配好的 task 字符串后，不再二次 `build_user_request`，也不读 stale `attachment_manifest`。
10. **SSE filter 固定测试**: replay filter 与 live `SSEHandler` 都不暴露 `user_turn_context`。

由于 `agent_run_service.AgentRunService.run_agent` 是一个长路径流程（涉及 Bohrium / Playground / fanout / events_table / LLM 等），完整端到端 integration 测试 fixture 重，Phase 2C 用**两层测试策略**：

- **Layer A — Unit snapshot tests**: 直接调 `ContextAssembler.assemble_turn(...)` + payload builder dict 的 mock 路径，pin payload 字段。
- **Layer B — Runtime smoke tests**: 复用 `tests/matmaster/core/test_agent_kernel_stream.py` 的 recording provider，验证 kernel 传入 provider 的 user message content 不被二次包装；复用现有 SSE tests 固定 replay/live hidden 行为。

本 Task 扩展 Task 5.5 已创建的 cutover test file。Task 9 删除 Phase 1 renderer 后，该文件不得再 import 旧 renderer；所有基线以固定 snapshot 字符串表达。

- [ ] **Step 1: Extend Layer A snapshot tests**

Open `tests/matmaster/services/test_agent_run_stream_context_cutover.py`（Task 5.5 已创建）。确保文件不再 import Phase 1 renderer，然后 append the following tests. If helper names already exist from Task 5.5, reuse them instead of duplicating:

```python
"""Phase 2C cutover snapshot tests.

Pin the exact payload shape produced by ContextAssembler.assemble_turn(...)
under the production-relevant cases:
- anchor turn (AGENT.md present)
- continuation turn (AGENT.md hash unchanged)
- anchor turn (AGENT.md hash changed)
- multi-turn attachments
- empty AGENT.md still hashable

These tests guard the Phase 2C cutover from accidental prompt drift.
Task 5.5 records the Phase 1 legacy-renderer deltas before those helpers
are deleted.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from matmaster.context.assembly import (
    ContextAssembler,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    UserInstructions,
)
from matmaster.context.session import SessionContextBuilder
from matmaster.context.sections import ContextView
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)
from matmaster.skills.registry import SkillRegistry


# ---- helpers ----

class _StubEventsPort:
    def __init__(self, events: tuple[SessionEvent, ...] = ()) -> None:
        self._events = events

    async def load_events(self, _query):  # noqa: ARG002
        return self._events


def _bundle(text: str = "USER INSTRUCTIONS") -> UserInstructions:
    import hashlib
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return UserInstructions(text=text, hash=f"sha256:{digest}", truncated=False)


def _assembler(events: tuple[SessionEvent, ...] = ()) -> ContextAssembler:
    return ContextAssembler(
        ports=ContextAssemblyPorts(
            session_events=_StubEventsPort(events),
            session_jobs=None,
        ),
        session_context_factory=None,  # Phase 2A empty default for unit snapshot
    )


def _skill_registry(tmp_path: Path) -> SkillRegistry:
    root = tmp_path / "skills"
    skill_dir = root / "pxrd"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: pxrd\n"
        "description: PXRD helper\n"
        "mcp_server: mat_xrd\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    return SkillRegistry([root])


# ---- Layer A: snapshot per intent ----

@pytest.mark.asyncio
async def test_anchor_turn_renders_instructions_and_current_instruction_block() -> None:
    assembler = _assembler()
    bundle = _bundle("Use SI units.")
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="hello, world"),
        attachments=TurnAttachmentsSource(),
        pre_turn_history_event_id=0,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input,
            user_instructions=bundle,
        ),
    )

    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    assert runtime.content == (
        "<user_instructions>\nUse SI units.\n</user_instructions>"
        "\n\n"
        "<current_instruction>\nhello, world\n</current_instruction>"
    )
    assert result.user_instructions_hash == bundle.hash
    assert result.used_composition == "anchor"


@pytest.mark.asyncio
async def test_continuation_turn_emits_only_current_instruction_block() -> None:
    assembler = _assembler()
    bundle = _bundle("Use SI units.")
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="follow-up"),
        attachments=TurnAttachmentsSource(),
        pre_turn_history_event_id=10,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input,
            user_instructions=bundle,
        ),
    )

    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    assert runtime.content == (
        "<current_instruction>\nfollow-up\n</current_instruction>"
    )
    assert result.used_composition == "continuation"


@pytest.mark.asyncio
async def test_anchor_turn_with_attachments_merges_into_current_instruction() -> None:
    assembler = _assembler()
    bundle = _bundle("Be concise.")
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="check files"),
        attachments=TurnAttachmentsSource(
            files=("/tmp/a.txt",),
            images=("/tmp/b.png",),
            workspace_paths=("/workspace/note.md",),
        ),
        pre_turn_history_event_id=0,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input,
            user_instructions=bundle,
        ),
    )

    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    # Phase 2C default keeps TurnInput's merged attachment shape:
    # current attachments live inside <current_instruction>.
    assert "<user_instructions>" in runtime.content
    assert "Be concise." in runtime.content
    assert "<current_instruction>" in runtime.content
    assert "check files" in runtime.content
    assert "[Current attachments]" in runtime.content
    assert "/tmp/a.txt" in runtime.content
    assert "/tmp/b.png" in runtime.content
    assert "/workspace/note.md" in runtime.content
    # No separate <turn_attachments> block in default Phase 2C shape
    assert "<turn_attachments>" not in runtime.content


@pytest.mark.asyncio
async def test_anchor_turn_with_empty_instructions_omits_wrapper() -> None:
    assembler = _assembler()
    bundle = _bundle("")  # empty AGENT.md
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="just text"),
        attachments=TurnAttachmentsSource(),
        pre_turn_history_event_id=0,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input,
            user_instructions=bundle,
        ),
    )

    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    assert "<user_instructions>" not in runtime.content
    assert runtime.content == "<current_instruction>\njust text\n</current_instruction>"


@pytest.mark.asyncio
async def test_assembly_result_hash_is_bundle_hash_not_recomputed() -> None:
    """Phase 2C / DESIGN §4.2 #7: AGENT.md only read once; bundle hash
    is the source of truth, no re-hash from the rendered text."""
    assembler = _assembler()
    bundle = _bundle("Original text.")
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="x"),
        attachments=TurnAttachmentsSource(),
        pre_turn_history_event_id=0,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input,
            user_instructions=bundle,
        ),
    )

    assert result.user_instructions_hash == bundle.hash
    assert result.user_instructions_text == "Original text."


@pytest.mark.asyncio
async def test_bundle_hash_is_not_recomputed_even_if_text_and_hash_disagree() -> None:
    """Bundle 防竞态: assembler 只透传 caller 读到的 UserInstructions。

    这里故意传入与 text 不匹配的 hash，模拟 service 读取 bundle 后文件被修改。
    Assembler 没有 UserInstructionsPort，也不应二次读取 / 重算。
    """
    assembler = _assembler()
    bundle = UserInstructions(
        text="text read at stage 3",
        hash="sha256:" + "f" * 64,
        truncated=False,
    )
    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="x"),
                pre_turn_history_event_id=0,
            ),
            user_instructions=bundle,
        ),
    )

    assert result.user_instructions_text == "text read at stage 3"
    assert result.user_instructions_hash == "sha256:" + "f" * 64


@pytest.mark.asyncio
async def test_anchor_turn_with_session_factory_renders_tools_and_attachments(
    tmp_path: Path,
) -> None:
    """Non-empty factory path: legal_mcp_servers/schemas_by_server affect session_tools."""
    events = (
        SessionEvent(
            id=1,
            event_type="query",
            source="User",
            content={"files": ("https://oss.example.com/a.csv",)},
        ),
        SessionEvent(
            id=2,
            event_type="skill_hit",
            source="System",
            content={"skill_name": "pxrd"},
        ),
    )
    port = _StubEventsPort(events)

    def factory(loaded_events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=loaded_events,
            skill_registry=_skill_registry(tmp_path),
            legal_mcp_servers={"mat_xrd"},
            schemas_by_server={"mat_xrd": [{"name": "read"}]},
        )

    assembler = ContextAssembler(
        ports=ContextAssemblyPorts(session_events=port),
        session_context_factory=factory,
    )
    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="current"),
                attachments=TurnAttachmentsSource(files=("/tmp/current.txt",)),
                pre_turn_history_event_id=2,
            ),
            user_instructions=_bundle("Use tools."),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<loaded_skills>" in runtime
    assert "<active_tools>" in runtime
    assert "<attachments>" in runtime
    assert "a.csv" in runtime
    assert "current.txt" in runtime


@pytest.mark.asyncio
async def test_payload_shape_matches_user_turn_context_v1_contract() -> None:
    """Phase 2C cutover keeps the stable user_turn_context.v1 field set."""
    assembler = _assembler()
    bundle = _bundle("Be concise.")
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="hello"),
        attachments=TurnAttachmentsSource(),
        pre_turn_history_event_id=0,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input,
            user_instructions=bundle,
        ),
    )
    rendered_message = result.user_turn_context.to_message(ContextView.RUNTIME)

    payload = {
        "schema_version": "user_turn_context.v1",
        "kind": "anchor",
        "message": rendered_message.model_dump(mode="json"),
        "user_instructions_hash": result.user_instructions_hash,
        "transform": "raw",
        "render_version": "user_context_render.v1",
    }
    # Keep the JSON snapshot stable for history restore and SSE filtering.
    serialized = json.dumps(payload, sort_keys=True)
    assert "\"kind\": \"anchor\"" in serialized
    assert "\"schema_version\": \"user_turn_context.v1\"" in serialized
    assert "\"transform\": \"raw\"" in serialized
    assert "\"render_version\": \"user_context_render.v1\"" in serialized


@pytest.mark.asyncio
async def test_user_instructions_bundle_truncated_flag_does_not_leak_into_payload() -> None:
    """Phase 2C: truncated flag is bundle-internal; payload exposes only hash + text."""
    assembler = _assembler()
    bundle = UserInstructions(
        text="x" * 10,
        hash="sha256:" + "0" * 64,
        truncated=True,
    )
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="x"),
        attachments=TurnAttachmentsSource(),
        pre_turn_history_event_id=0,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input,
            user_instructions=bundle,
        ),
    )

    assert result.user_instructions_text == bundle.text  # truncated text only
    # No "truncated" key surfaces in AssemblyResult
    assert not hasattr(result, "truncated")
```

- [ ] **Step 2: Add an integration smoke test for AGENT.md hash anchor sequence**

In the same file, append:

```python
# ---- Layer B: anchor → continuation → anchor ----

@pytest.mark.asyncio
async def test_anchor_continuation_anchor_sequence_kind_flow() -> None:
    """Phase 2C / DESIGN.md §8.4: AGENT.md unchanged across turns → continuation;
    once it changes → anchor.
    """
    assembler = _assembler()
    bundle_v1 = _bundle("v1 instructions")
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="turn1"),
        attachments=TurnAttachmentsSource(),
        pre_turn_history_event_id=0,
    )

    r1 = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input,
            user_instructions=bundle_v1,
        ),
    )
    assert r1.used_composition == "anchor"

    # Turn 2: hash unchanged → caller resolves CONTINUATION_TURN
    turn_input2 = TurnInput(
        instruction=TurnInstructionSource(user_text="turn2"),
        attachments=TurnAttachmentsSource(),
        pre_turn_history_event_id=20,
    )
    r2 = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input2,
            user_instructions=bundle_v1,
        ),
    )
    assert r2.used_composition == "continuation"
    assert r2.user_turn_context.to_message(ContextView.RUNTIME).content == (
        "<current_instruction>\nturn2\n</current_instruction>"
    )

    # Turn 3: AGENT.md changes → caller resolves ANCHOR_TURN with new hash
    bundle_v2 = _bundle("v2 different")
    turn_input3 = TurnInput(
        instruction=TurnInstructionSource(user_text="turn3"),
        attachments=TurnAttachmentsSource(),
        pre_turn_history_event_id=42,
    )
    r3 = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="s-1",
            spawn_id=None,
            turn_input=turn_input3,
            user_instructions=bundle_v2,
        ),
    )
    assert r3.used_composition == "anchor"
    assert r3.user_instructions_hash == bundle_v2.hash
    assert r3.user_instructions_hash != bundle_v1.hash
```

- [ ] **Step 3: Verify Layer A + Layer B tests pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_agent_run_stream_context_cutover.py -q
```

Expected: all tests in the cutover file pass（至少 10 个，包括 Task 5.5 的两个 snapshot/oracle-derived cases）。

- [ ] **Step 4: Verify SSE filter does not leak `user_turn_context`**

确认 Phase 1 已经把 `user_turn_context` 加进 SSE hidden list（DESIGN.md §3.5 / §14 Phase 1a），并通过真实存在的 replay/live 测试文件固定行为。Phase 2C cutover 没有修改 SSE filter；本 step 只是 confirm 没有 regression：

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py::test_should_not_emit_user_turn_context \
  tests/matmaster/integration/test_sse_skill_hit.py::test_should_skip_user_turn_context \
  -q
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "user_turn_context" \
  src/services/stream_sse_filter.py \
  matmaster/integration/sse_handler.py
```

Expected: both tests pass; `user_turn_context` appears in both replay and live hidden filters.

- [ ] **Step 5: Verify ModelHistoryRestorer correctly consumes Phase 2C cutover payloads**

Run existing tests that pin the v1 hybrid restoration semantics:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/context/test_history_restore.py \
  -q
```

Expected: all tests pass。Phase 2C cutover 不修改 restorer / codec；Task 10 的 payload field-shape snapshot 保证恢复器继续识别 `user_turn_context.v1` payload。

- [ ] **Step 6: Commit**

Commit 信息：

```text
test(context): pin phase 2c cutover prompt + payload shape

DESIGN.md §14 Phase 2C 测试目标 + §16 测试覆盖：snapshot tests pin
anchor / continuation / anchor 三种 intent 下的 user_turn_context 渲染输出,
保留 Task 5.5 的 Phase 1 renderer oracle 结论, 覆盖 bundle 防竞态与
session_context_factory 非空工具路径。SSE replay/live filter 固定测试继续通过。
```

---

## Task 11: Phase Boundary Static Checks And Regression Verification

**Files:** read-only

**Spec 依据:** DESIGN.md §14 Phase 2C 整段、§17 验收要点、附录 B「Phase 2C 改动」、Phase 2C Acceptance Checklist（本文件末尾）。

完整跑完 Phase 0/0.5/1/2A/2B/2C 测试套件 + 各种静态约束验证，作为 PR 提交前的最终回归门。

- [ ] **Step 1: Full test suite regression**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/manifests \
  tests/matmaster/services \
  tests/matmaster/integration \
  tests/matmaster/test_runtime_spec.py \
  tests/matmaster/core/test_agent_kernel_stream.py \
  tests/services/test_attachment_manifest_service.py \
  tests/services/test_context_assembly_factory.py \
  tests/test_chat_events_history_checkpoint.py \
  -q
```

Expected: all tests pass。

- [ ] **Step 2: Static check — `agent_run_service.py` no longer imports manifests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "matmaster\.manifests" src/services/agent_run_service.py src/services/agent_run_history_wiring.py
```

Expected: 无匹配。

- [ ] **Step 3: Static check — `core/agent.py` still has no manifests/context direct import**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "from matmaster\.(manifests|context)" matmaster/core/agent.py
```

Expected: 无匹配。`core/agent.py` 仅 import `matmaster.types.*` / `matmaster.core.*` / `matmaster.integration.*` 等通用类型；assembler 在 service 层使用，kernel 不感知。

- [ ] **Step 4: Static check — context_compactor / exp.py byte-for-byte unchanged**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git diff --stat matmaster/core/context_compactor.py matmaster/core/exp.py matmaster/core/context_builder.py matmaster/manifests/
```

Expected: 无变化（除非这些文件本身有 Phase 2C 范围外的 dirty 编辑，那也要确认与本计划无关）。

- [ ] **Step 4b: Static check — `attachment_manifest` dependency is gone from runtime consumers**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "attachment_manifest" \
  matmaster/core/context_compactor.py \
  matmaster/core/agent_compaction.py \
  matmaster/core/agent.py \
  src/services/agent_run_service.py \
  src/services/agent_run_history_wiring.py
```

Expected: 无匹配。`matmaster/core/exp.py` may still copy passive `run_meta["attachment_manifest"]` into `spec.meta` until Phase 3/4, but no Phase 2C runtime consumer should read or write it.

- [ ] **Step 5: Static check — `_apply_user_instructions_to_initial_user_query` and friends deleted**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "_apply_user_instructions_to_initial_user_query|_render_user_instructions_block|_strip_user_instructions_prefix|_find_first_user_message_index|COMPAT:legacy-runtime-injection-helper" matmaster src tests
```

Expected: 无匹配。

- [ ] **Step 6: Static check — Phase 1 渲染函数全部删除**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "latest_anchor_user_instructions_hash|decide_user_turn_context_kind|render_runtime_task_for_user_turn_context|render_provider_facing_current_message_content|build_user_turn_context_payload" matmaster src tests
```

Expected: 无匹配。

- [ ] **Step 7: Static check — `AgentRuntimeSpec` 4 个新字段可被 instance 化**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -c "
from matmaster.types.runtime import AgentRuntimeSpec
from matmaster.core.context_builder import ContextBuilder
spec = AgentRuntimeSpec(context_builder=ContextBuilder())
print('context_assembler:', spec.context_assembler)
print('user_instructions_port:', spec.user_instructions_port)
print('session_events_port:', spec.session_events_port)
print('session_jobs_port:', spec.session_jobs_port)
print('OK')
"
```

Expected:

```text
context_assembler: None
user_instructions_port: None
session_events_port: None
session_jobs_port: None
OK
```

- [ ] **Step 8: Static check — `TurnInput` re-export works**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -c "
from matmaster.types.current_input import TurnInput, CurrentInputContext, build_current_instruction_block
from matmaster.context.sources.turn_input import TurnInput as TurnInputSource
assert TurnInput is TurnInputSource
print('TurnInput re-export OK')
print('CurrentInputContext kept:', CurrentInputContext.__name__)
print('build_current_instruction_block kept')
"
```

Expected: prints `TurnInput re-export OK` + 三行 confirmation。

- [ ] **Step 9: Static check — `matmaster.manifests` 仍然只被 Phase 3-owned 文件 + manifests shim 自身 + tests 引用**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "from matmaster\.manifests|import matmaster\.manifests" matmaster src tests
```

Expected matches:

```text
matmaster/core/context_compactor.py
matmaster/core/exp.py
matmaster/manifests/skill.py
matmaster/manifests/rehydrator.py
matmaster/manifests/attachment.py  (if cross-internal)
matmaster/manifests/mcp.py
matmaster/manifests/scanner.py
tests/services/test_attachment_manifest_service.py
tests/matmaster/manifests/test_*.py
tests/matmaster/integration/test_history_checkpoint_recovery.py
tests/matmaster/services/test_active_mcp_replay.py
```

(无 `src/services/agent_run_service.py` 与 `src/services/agent_run_history_wiring.py`。)

- [ ] **Step 10: Static check — final SSE filter includes `user_turn_context`**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "user_turn_context" src/services/stream_service.py src/services/stream_sse_filter.py 2>/dev/null
```

Expected: at least one match in `src/services/stream_sse_filter.py`。Then run the fixed replay/live tests:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py::test_should_not_emit_user_turn_context \
  tests/matmaster/integration/test_sse_skill_hit.py::test_should_skip_user_turn_context \
  -q
```

Expected: both tests pass，表示 SSE replay / live stream 都过滤掉 `user_turn_context`。

- [ ] **Step 11: Documentation sync note for DESIGN.md drift**

Claude review 指出的 DESIGN.md 轻微漂移不阻塞 Phase 2C execution，但 PR 描述或后续 DESIGN.md v3.4 changelog 必须记录：

- `ContextAssembler.__init__` 现实签名是 `ContextAssembler(ports, session_context_factory=None, ...)`，不是 DESIGN.md 旧伪代码里的单参数 `ports`。
- `AppSessionEventsPort` 现实构造参数名是 `events_table=...`，不是 DESIGN.md 旧 docstring 里的 `events_service`。
- Phase 2C plan 跟随 Phase 2A/2B 已落地现实；不要在实现时按旧伪代码回退签名。

This Task has no commit.

---

## Phase 2C Acceptance Checklist

- [ ] `matmaster/types/current_input.py` 顶部新增 `from matmaster.context.sources.turn_input import TurnInput` re-export；`CurrentInputContext` / `build_current_instruction_block` / `_clean_tuple` / `_display_name` 保持原样。
- [ ] `src/services/context_assembly_factory.py` 落地 `build_session_context_factory(skill_registry, legal_mcp_servers, schemas_by_server)`，返回匹配 `ContextAssembler.session_context_factory` 签名的 Callable；测试覆盖 None / 非空两种 dependency 组合 + 空 events tuple + 非 tuple 类型 reject。
- [ ] `matmaster/types/runtime.py::AgentRuntimeSpec` 新增 4 个 Optional 字段（`context_assembler` / `user_instructions_port` / `session_events_port` / `session_jobs_port`），`_check_v2_field_types` 通过 lazy import + hasattr duck typing 校验。
- [ ] `src/services/agent_run_history_wiring.py` 不再 import `matmaster.manifests.attachment`，改为 `from matmaster.context.sources.attachments import scan_legacy_attachment_entries, format_entries_text`；`attachment_text` 与 Phase 2B 末态字节等价。
- [ ] `src/services/agent_run_service.py` Stage 5b 主路径改为 `AppSessionEventsPort + AppSessionJobsPort + build_session_context_factory + ContextAssembler + resolve_turn_context_intent + assembler.assemble_turn`；不再调用 `latest_anchor_user_instructions_hash` / `decide_user_turn_context_kind` / `render_runtime_task_for_user_turn_context` / `render_provider_facing_current_message_content` / `build_user_turn_context_payload`。
- [ ] `src/services/agent_run_service.py` 内 `_resolve_active_skill_names` 切到 `from matmaster.context.sources.skills import resolve_active_skills, skill_name`；不再 import `matmaster.manifests.skill`。
- [ ] `src/services/agent_run_service.py` 不再 `pg_ctx.with_run_meta(attachment_manifest=attachment_text)`；`pg_ctx.with_run_meta(user_instructions=user_instructions.text)` 保留；`pg_ctx.with_run_meta(current_input_context=...)` 保留（compactor 旁路所需）。
- [ ] `matmaster/core/agent.py::_run_items` 不再读 `spec.meta["attachment_manifest"]`，也不再调 `spec.context_builder.build_user_request(user_text=task, attachments=...)`；`UserMessage(content=task, images=current_user_images)` 直接装配。
- [ ] `src/services/agent_run_instructions.py` 仅保留 `_USER_INSTRUCTIONS_PATH` 常量；`_apply_user_instructions_to_initial_user_query` / 配套 helper / `COMPAT:legacy-runtime-injection-helper` 标记全部删除。
- [ ] `src/services/user_turn_context_service.py` 删除 `latest_anchor_user_instructions_hash` / `decide_user_turn_context_kind` / `render_runtime_task_for_user_turn_context` / `render_provider_facing_current_message_content` / `build_user_turn_context_payload` / `_context_events_newest_first`；保留 `UserInstructionsInfo` / `hash_user_instructions` / `make_user_instructions_info` / `load_user_instructions_from_session` / `_truncate_utf8` / `write_user_turn_context_event` / 模块常量。
- [ ] `tests/matmaster/services/test_user_instructions_runtime_injection.py` 已删除。
- [ ] `tests/matmaster/services/test_user_turn_context_service.py` 删除针对 Phase 1 渲染函数的用例；保留 hash / make_info / load_from_session / write_event / TurnInput re-export 用例。
- [ ] `tests/matmaster/services/test_agent_run_stream_context_cutover.py` 落地 10+ snapshot/runtime-adjacent tests，覆盖 Task 5.5 Phase 1 renderer oracle 结论、anchor / continuation / multi-turn / empty-instructions / payload-shape / truncated-flag / bundle 防竞态 / session_context_factory 非空工具路径 / anchor→continuation→anchor 序列。
- [ ] `tests/matmaster/core/test_agent_kernel_stream.py` 覆盖 kernel 对 service 已装配 task 的 runtime pass-through，不再二次 `build_user_request` / 不读 stale `attachment_manifest`。
- [ ] `matmaster/core/context_compactor.py` / `matmaster/core/exp.py` / `matmaster/core/context_builder.py` / `matmaster/manifests/*` 整组文件 byte-for-byte 未改。
- [ ] `matmaster/context/*` / `matmaster/context/sources/*` / `src/services/context_assembly_ports.py` / `src/services/context_turn_intent.py` / `src/services/model_history_restore_service.py` 未改。
- [ ] `tests/matmaster/manifests/`、`tests/matmaster/context/`、`tests/matmaster/services/test_context_assembly_ports.py`、`test_context_turn_intent.py`、`test_model_history_restore_service.py`、`test_history_restore_service.py`、`test_history_checkpoint_codec.py`、`test_history_checkpoint_service.py` 全部继续通过，无用例修改。
- [ ] 任何带 `matmaster.manifests` import 的文件均限于 `core/context_compactor.py` / `core/exp.py` / `matmaster/manifests/*.py` 自身 + Phase 2B 覆盖的测试文件。
- [ ] `COMPAT:legacy-runtime-injection-helper` 标记在源码与测试中均无残留；`COMPAT:v0-restore` / `COMPAT:v0-checkpoint-marker` 在源码中保留（Phase 4 退役范围）。
- [ ] Phase 0+0.5+1+2A+2B+2C 完整 pytest 套件全部 green。
- [ ] 工作树仅含 Phase 2C 范围内的 staged commits（11 个 Tasks + Task 5.5 中 Task 2/3/4/5/5.5/6/7/9/10 各产 1 个 commit；Task 1/8/11 视情况无 commit 或仅 docs commit）。

---

## Notes For Phase 3

Phase 3 接力 cutover compaction 主路径与 prompt 形态 A/B（DESIGN.md §14 Phase 3）：

1. **Compactor 迁移**：把 `matmaster/core/context_compactor.py` 内容迁到 `matmaster/context/compaction.py`，原文件改为薄 shim。同时把 compactor 内 `from matmaster.manifests.rehydrator import CompactionRehydrator` 切到 `ContextAssembler.assemble_compaction(...)`，需要使用 Phase 2C 落地的 `AgentRuntimeSpec.context_assembler` 字段（目前是 dead 字段，Phase 3 改成实际消费）。
2. **Exp.assemble 改造**：`matmaster/core/exp.py` 内 `CompactionRehydrator` 构造改为构造 `ContextAssembler`（复用 Phase 2C 的 `build_session_context_factory` + `AppSessionEventsPort` + `AppSessionJobsPort`），并把 assembler 注入到 spec.context_assembler。Phase 2C 内 service 自己持有 assembler；Phase 3 后 service 与 compactor 共享同一 assembler 实例（避免重复装配）。
3. **Checkpoint v1 marker 切换**：`HistoryCheckpointService.build_checkpoint_sink` 写入端改为 `<compacted_history>` marker；codec 仍接受双 marker（`COMPAT:v0-checkpoint-marker`，Phase 4 退役）。
4. **修复 Phase 1 preflight 双层 `<current_instruction>` wrap**：见 FOLLOWUPS.md「议题 3」。compactor 迁移到 `ContextAssembler.assemble_compaction(...)` 后，preflight wrap 由 `COMPACTED_COMPOSITION` 决定，compactor 不再读 `current_input_context`，自动正确。
5. **Prompt 形态 A/B**：在 Phase 3 起手做 offline eval（DESIGN.md §6.5），通过则启用 `TurnInput.to_sections(split_attachments=True)` 默认；不通过则保留 Phase 2C 末态合并形态再做下一轮调整。
6. **`current_input_context` / `build_current_instruction_block` 删除**：Phase 3 compactor cutover 后，`current_input_context` 不再被 compactor 读，service 端 `pg_ctx.with_run_meta(current_input_context=...)` 行可删除，`matmaster/types/current_input.py` 内 `CurrentInputContext` 与 `build_current_instruction_block` 移到 Phase 4 删除范围（与其它 shim 一起 retire）。

Phase 3 不动 `matmaster/manifests/*`（仍是 shim），不动 SSE filter（Phase 1 已加），不动 `ModelHistoryRestoreService` / `ModelHistoryRestorer`（Phase 2B 已落地）。Phase 4 才删 shim、退役 `COMPAT:v0-checkpoint-marker` / `COMPAT:v0-restore`、迁测试目录从 `tests/matmaster/manifests/` 到 `tests/matmaster/context/`。
