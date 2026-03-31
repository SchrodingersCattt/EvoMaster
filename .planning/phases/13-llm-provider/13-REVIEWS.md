# Phase 13 — Cross-AI Review

**Reviewer:** GPT
**Date:** 2026-03-27
**Verdict:** REVISE — 2 HIGH, 3 MEDIUM issues

---

## HIGH-1: summary_provider 生命周期遗漏

Plan 13-02 只管理了主 `spec.llm_provider` 的 `__aenter__/__aexit__`，遗漏了 compaction 独立创建的 `summary_provider`。

**Evidence:**
- `exp.py:208-215`: 当 `spec.compaction.compaction_llm` 存在时，新建独立 `OpenAIProvider(**resolved)` 作为 summary_provider
- 13-01 要求未进入 async context 时调用 chat 抛 RuntimeError
- 结果：compaction 路径首次触发时 summary_provider 未 enter → RuntimeError → 静默回退 sliding window
- `tests/matmaster/integration/test_compaction_real_api.py:201` 的集成测试正是用独立 compaction provider 构造的

**Fix required:** Kernel 或 Exp 层需要管理 summary_provider 的生命周期，不能只管主 provider。

---

## HIGH-2: LLMProvider Protocol 缺少 __aenter__/__aexit__

Plan 13-02 让 Kernel 依赖 `spec.llm_provider.__aenter__()/__aexit__()`，但 `LLMProvider` Protocol (`llm_provider.py:14-35`) 没有声明这些方法。

**Evidence:**
- `llm_provider.py` Protocol 只有 `chat` 和 `chat_stream`
- `test_agent.py:82` StreamingProvider — 无 async context manager
- `test_agent.py:105` ToolCallingProvider — 无 async context manager
- `test_agent.py:895` UsageTrackingProvider — 无 async context manager
- 按计划实现后，所有 mock provider 在 `run()` 入口直接 AttributeError

**Fix required:** 要么将 `__aenter__/__aexit__` 升格为 Protocol 正式契约并迁移所有 mock/实现，要么不在 Kernel 做 lifecycle ownership。

---

## MEDIUM-1: bridge loop 设计不一致

Plan 13-02 在 `run()` 创建 `_bridge_loop` 用于 provider lifecycle，但 `_sync_iterate_async` 和 `_sync_call_async` 各自 `asyncio.new_event_loop()`。provider 在一个 loop enter，在另一个 loop 执行 I/O。

**Evidence:**
- 13-02-PLAN.md 步骤 3: `_bridge_loop = asyncio.new_event_loop()` 用于 `__aenter__/__aexit__`
- 13-02-PLAN.md 步骤 2: `_sync_iterate_async` 内部 `loop = asyncio.new_event_loop()`
- 13-RESEARCH.md 推荐复用同一 loop，计划与自己的 research 矛盾

**Fix required:** 统一使用一个 bridge loop，通过参数传入桥接函数。

---

## MEDIUM-2: 受影响测试面低估

`compact_if_needed()` 改 async 后，受影响范围远超 VALIDATION.md 跟踪的文件：

**Evidence:**
- `test_agent.py:854` SpyCompactor — sync `compact_if_needed`，`_sync_call_async(None)` 会 TypeError
- `test_agent.py:883` UsageSpyCompactor — 同上
- `test_compaction_via_devshell.py` — 40+ 处直接同步调用 `compactor.compact_if_needed()`
- Kernel mock provider (`test_agent.py:82,105,895`) 都是 sync chat_stream()
- VALIDATION.md 只跟踪 provider 测试和 test_context_compactor.py

**Fix required:** VALIDATION.md 和计划任务需覆盖 test_agent.py 和 test_compaction_via_devshell.py 的适配。

---

## MEDIUM-3: 路径和命令错误

**Evidence:**
- 13-02-PLAN.md 验证命令引用 `tests/matmaster/core/test_agent_kernel.py` — 文件不存在，实际是 `test_agent.py`
- 13-01-PLAN.md `validate_async_protocol` 从不存在模块导入，实际在 `matmaster/validation.py`
- `pytest ... | tail -30` 吞掉 pytest 非零退出码，测试失败也可能被报告为通过

**Fix required:** 修正所有文件路径和验证命令。
