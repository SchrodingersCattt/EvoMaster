---
phase: 05-integration-quality
verified: 2026-03-22T09:30:52Z
status: passed
score: 8/8 must-haves verified
re_verification: false
---

# Phase 5: Integration and Quality Verification Report

**Phase Goal:** mat_master 和 minimal 在新骨架上端到端跑通，三层契约有测试覆盖，上游场景对齐验证，迁移差异有文档记录
**Verified:** 2026-03-22T09:30:52Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | mat_master 在新三层管线上端到端可运行（mock LLM） | VERIFIED | `test_mat_master_e2e_pipeline`, `test_mat_master_run_agent_sync_e2e` 均通过；`AgentKernel.run()` 接收 `history`，管线全程连通 |
| 2 | minimal 在新三层管线上端到端可运行（mock LLM） | VERIFIED | `test_minimal_e2e_pipeline` 通过；与 mat_master 共用同一 `Playground + DirectExp + AgentKernel` 链路 |
| 3 | 三层契约（PlaygroundContext/AgentRuntimeSpec/AgentEvent）有单元测试覆盖 | VERIFIED | 66 个类型层测试全通；包括 discriminated union roundtrip、frozen 变异拒绝、默认值验证 |
| 4 | agent_run_service.py 简化为薄编排层（6 阶段管线，无旧闭包）| VERIFIED | 文件头注释显示 ~200 行精简版；不含 `def event_callback`、`_should_persist_event`；含完整 6 阶段流程 |
| 5 | 上游场景有覆盖（run_interrupted / 跨 pod / workspace / Bohrium）| VERIFIED | `test_upstream_scenarios.py` 含 `test_run_interrupted_detection_deploy/restart`、`test_cross_pod_reply_queue`、`test_workspace_upload_*`、`test_bohrium_setup_lifecycle` |
| 6 | 配额扣减在成功路径执行、取消/异常路径跳过 | VERIFIED | `test_quota_pipeline.py` 含 `test_quota_deducted_on_success`、`test_quota_not_deducted_on_cancel`、`test_quota_not_deducted_on_error`、`test_quota_async_mode`、`test_quota_sync_mode` |
| 7 | 迁移文档清晰记录新旧架构差异 | VERIFIED | `docs/migration-guide.md` 含 `## Architecture Changes`、`## New Components`、`## Pipeline Flow`、`## Breaking Changes`、`DeprecationWarning`、`x_master` 说明 |
| 8 | x_master playground_type 请求抛出 ValueError | VERIFIED | `_get_or_create_playground()` 中有 `if playground_type == "x_master": raise ValueError(...)`；`test_x_master_raises_value_error` 覆盖 |

**Score:** 8/8 truths verified

---

### Required Artifacts

| Artifact | Provides | Level 1 (Exists) | Level 2 (Substantive) | Level 3 (Wired) | Status |
|----------|----------|------------------|-----------------------|-----------------|--------|
| `matmaster/engine/agent.py` | `AgentKernel.run()` with history parameter | PRESENT | 含 `history: list[Message] \| None = None` 和 `*(history or [])` | 被 `tests/` 和 `agent_run_service.py` 调用 | VERIFIED |
| `matmaster/types/context.py` | `PlaygroundContext.with_bohrium()` | PRESENT | 含完整 `def with_bohrium` 实现，`model_copy` 模式 | 被 `agent_run_service.py` 第 237 行调用 | VERIFIED |
| `matmaster/hooks/__init__.py` | 4 个业务 Hook 导出 | PRESENT | 导出 `ConfirmationHook`、`OutputProcessorHook`、`SkillHitHook`、`AssistantStateHook` | 被 `agent_run_service.py` `from matmaster.hooks import ...` 导入并在 `exp.assemble()` 中使用 | VERIFIED |
| `matmaster/hooks/confirmation.py` | `ConfirmationHook` 实现 | PRESENT | `class ConfirmationHook(BaseHook):`，`ReplyQueueLike` Protocol，`pre_tool_call` 实现 | 被 `__init__.py` 导出，被 `test_confirmation.py` 和上游场景测试使用 | VERIFIED |
| `matmaster/hooks/output_processor.py` | `OutputProcessorHook` 实现 | PRESENT | `class OutputProcessorHook(BaseHook):`，`post_tool_call` 模式匹配 | 被 `__init__.py` 导出 | VERIFIED |
| `matmaster/hooks/skill_hit.py` | `SkillHitHook` 实现 | PRESENT | 含 `SkillHitEvent` 发射逻辑 | 被 `__init__.py` 导出 | VERIFIED |
| `matmaster/hooks/assistant_state.py` | `AssistantStateHook` 实现 | PRESENT | 含 `AssistantStateEvent` 发射逻辑 | 被 `__init__.py` 导出 | VERIFIED |
| `matmaster/integration/event_router.py` | `EventRouter` + `PersistenceHandler` + `SSEHandler` | PRESENT | 含 `class EventRouter`、`threading.Thread`、`drain_timeout`、`asyncio.run_coroutine_threadsafe`、`_should_persist`、`_should_skip` | 被 `agent_run_service.py` 导入，`router.start()/stop()` 调用 | VERIFIED |
| `matmaster/integration/workspace_handler.py` | `WorkspaceHandler` 防抖快照上传 | PRESENT | 含 `isinstance(event, ToolResultEvent)`、`self._ssh_attached`、`self._debounce_seconds`、`self._last_snapshot` | 被 `integration/__init__.py` 导出，被 `agent_run_service.py` 使用 | VERIFIED |
| `matmaster/integration/bohrium_setup.py` | `BohriumSetupService` 薄包装 | PRESENT | 含 `setup_bohrium_for_run`、`cleanup_bohrium_after_run` 委托 | 被 `agent_run_service.py` 第 198 行实例化 | VERIFIED |
| `src/services/agent_run_service.py` | 重写后的薄编排层（6 阶段管线） | PRESENT | 含 `from matmaster.engine.agent import AgentKernel`、`from matmaster.integration import EventRouter,...`、`from matmaster.hooks import`、`kernel.run(`、`router.start()`、`router.stop()`、`yaml.safe_load`、`DeprecationWarning`、`use_quota(` | 是服务层入口，被 E2E 测试直接调用 | VERIFIED |
| `src/services/chat_history.py` | `events_to_messages()` 新增方法 | PRESENT | 含 `def events_to_messages`，`from matmaster.engine.types import ...`，旧 `events_to_dialog_messages` 保留（D-14 兼容性） | 被 `agent_run_service.py` 第 280 行调用 | VERIFIED |
| `matmaster/assembly/direct_exp.py` | `assemble()` 接受外部 hooks 合并 | PRESENT | 含 `external_hooks = kwargs.get("hooks") or []`、`all_hooks = [emitter_hook, *external_hooks]`、`hooks=all_hooks` | 被 `agent_run_service.py` `exp.assemble(pg_ctx, hooks=[...])` 调用 | VERIFIED |
| `tests/matmaster/integration/test_e2e_mat_master.py` | mat_master E2E 管线测试 | PRESENT | 含 `test_mat_master_e2e_pipeline`、`test_mat_master_e2e_with_tool_call`、`test_mat_master_e2e_with_history`、`test_mat_master_run_agent_sync_e2e` | 5 个测试全通 | VERIFIED |
| `tests/matmaster/integration/test_e2e_minimal.py` | minimal E2E 管线测试 | PRESENT | 含 `test_minimal_e2e_pipeline` | 通过 | VERIFIED |
| `tests/matmaster/integration/test_upstream_scenarios.py` | 上游场景测试 | PRESENT | 含 `test_cross_pod_reply_queue`、`test_run_interrupted_*`、`test_workspace_upload_*`、`test_bohrium_*`、`test_x_master_raises_value_error` | 15 个测试全通 | VERIFIED |
| `tests/matmaster/integration/test_quota_pipeline.py` | 配额管线测试 | PRESENT | 含 `test_quota_deducted_on_success`、`test_quota_not_deducted_on_cancel`、`test_quota_not_deducted_on_error`、`test_quota_async_mode`、`test_quota_sync_mode` | 5 个测试全通 | VERIFIED |
| `docs/migration-guide.md` | 迁移文档（QUAL-03） | PRESENT | 含 `## Architecture Changes`、`## New Components`、`## Pipeline Flow`、`## Breaking Changes`、`StreamingMatMasterAgent`、`AgentKernel`、`EventRouter`、`DeprecationWarning`、`x_master` | 人工审核已批准 | VERIFIED |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `agent_run_service.py` | `matmaster/engine/agent.py` | `kernel.run(spec, task, history=history, stop_event=stop_event)` | WIRED | 第 314 行直接调用，E2E 测试验证 |
| `agent_run_service.py` | `matmaster/integration/event_router.py` | `EventRouter(bus=bus, handlers=[...]).start()/.stop()` | WIRED | 第 284/310/358 行，router 生命周期绑定单次 run |
| `agent_run_service.py` | `matmaster/hooks/__init__.py` | `from matmaster.hooks import ConfirmationHook,...` | WIRED | 第 25-30 行导入，第 263-268 行在 `assemble()` 中传递 |
| `agent_run_service.py` | `matmaster/playground/playground.py` | `playground.prepare(run_meta)` | WIRED | 第 190 行调用，返回 `PlaygroundContext` |
| `agent_run_service.py` | `src/services/chat_history.py` | `ChatHistoryConverter.events_to_messages(raw_events)` | WIRED | 第 280 行调用 |
| `matmaster/integration/event_router.py` | `matmaster/bus/queue.py` | `self._bus.get(timeout=0.1)` 在消费循环中 | WIRED | `grep self._bus.get` 确认 |
| `tests/test_upstream_scenarios.py` | `matmaster/hooks/confirmation.py` | `ConfirmationHook` 与 mock `RedisReplyQueue` 的跨 pod 确认测试 | WIRED | `test_cross_pod_reply_queue` 和 `test_cross_pod_reply_queue_cancel` 覆盖 |
| `tests/test_quota_pipeline.py` | `src/services/quota_service.py` | `use_quota` mock 验证 | WIRED | `patch("src.services.agent_run_service.use_quota")` 用于断言调用/不调用 |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| MIGR-01 | 05-01, 05-02, 05-03 | mat_master 在新骨架上端到端跑通 | SATISFIED | `test_mat_master_e2e_pipeline` + `test_mat_master_run_agent_sync_e2e` 通过；6 阶段管线完整连通 |
| MIGR-02 | 05-01, 05-03 | minimal 在新骨架上端到端跑通 | SATISFIED | `test_minimal_e2e_pipeline` 通过；与 mat_master 共享同一管线代码路径 |
| QUAL-01 | 05-04 | 三层契约有单元测试覆盖 | SATISFIED | 66 个类型层测试通过，涵盖构造、frozen 变异拒绝、序列化 roundtrip |
| QUAL-02 | 05-04 | mat_master 和 minimal 有 E2E 测试 | SATISFIED | 5+1 个 E2E 测试全通（包含 run_agent_sync 服务层 E2E）|
| QUAL-03 | 05-05 | 迁移文档记录新旧架构差异 | SATISFIED | `docs/migration-guide.md` 存在且包含所有必需章节，人工审核已批准 |
| QUAL-04 | 05-02, 05-04 | 上游场景端到端验证 | SATISFIED | 15 个上游场景测试全通：run_interrupted（deploy/restart）、cross-pod 确认（RedisReplyQueue mock）、workspace upload trigger/skip、Bohrium lifecycle |
| QUAL-05 | 05-03, 05-04 | 配额扣减在新管线正确执行 | SATISFIED | 5 个配额测试全通：success 路径扣减，cancel/error 路径跳过，async/sync 两种模式均覆盖 |

**REQUIREMENTS.md 追踪表注记：** QUAL-03 和 QUAL-05 在文件中标注为 Complete；MIGR-01、MIGR-02、QUAL-01、QUAL-02、QUAL-04 标注为 Pending。经代码验证，所有 7 个需求均已有实现和测试支撑。

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/services/agent_run_service.py` | 137-138 | `_build_llm_provider` 抛出 `NotImplementedError`（有意存根） | INFO | 不阻断测试路径（测试中 mock 绕过）；生产环境调用此方法前需完成 LLMProvider 工厂接入（属 v2 需求 LLMP-02，不在 Phase 5 范围） |
| `src/services/agent_run_service.py` | 140-142 | `_get_builtin_tools` 返回 `[]`（有意存根） | INFO | 同上；工具注册是独立的后续工作 |

**分类：** 两个存根均属 INFO 级别，为 PLAN 05-03 任务 H 中有意设计（"Add stub helper methods that the new pipeline needs"）。不阻断 Phase 5 的验收目标。存根不在任何事件渲染路径中，均被测试通过 mock 正确绕过。

---

### Human Verification Required

无必须的人工验证项。`docs/migration-guide.md` 的文档质量审核已在 Plan 05-05 Task 2（`checkpoint:human-verify`）中由用户批准完成（提示语中明确说明"Migration guide at docs/migration-guide.md (human-verified/approved)"）。

---

### Test Summary

| Test Group | Count | Result |
|-----------|-------|--------|
| `tests/matmaster/hooks/` | 15 | 全通 |
| `tests/matmaster/integration/test_event_router.py` | 13 | 全通 |
| `tests/matmaster/integration/test_workspace_handler.py` | 8 | 全通 |
| `tests/matmaster/integration/test_events_to_messages.py` | 5 | 全通 |
| `tests/matmaster/integration/test_e2e_mat_master.py` | 5 | 全通 |
| `tests/matmaster/integration/test_e2e_minimal.py` | 1 | 全通 |
| `tests/matmaster/integration/test_pipeline_alignment.py` | 2 | 全通 |
| `tests/matmaster/integration/test_upstream_scenarios.py` | 15 | 全通 |
| `tests/matmaster/integration/test_quota_pipeline.py` | 5 | 全通 |
| `tests/matmaster/types/` | 66 | 全通 |
| **全部 matmaster 测试** | **368** | **全通** |

---

### Gaps Summary

无 gap。所有 8 条可观测真值均已验证通过，所有关键链路均已连通，所有 7 个需求 ID（MIGR-01、MIGR-02、QUAL-01~QUAL-05）均有实现证据支撑，368 个测试全部通过。

两个有意存根（`_build_llm_provider`、`_get_builtin_tools`）是 Phase 5 计划中明确记录的遗留项（PLAN 05-03 任务 H），属 v2 需求范围，不影响 Phase 5 验收。

---

_Verified: 2026-03-22T09:30:52Z_
_Verifier: Claude (gsd-verifier)_
