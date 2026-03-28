# Phase 17: AgentKernel 异步化 - Research

**Researched:** 2026-03-29
**Domain:** Python asyncio -- sync-to-async kernel migration
**Confidence:** HIGH

## Summary

Phase 17 是一次高度确定性的机械改造：将 AgentKernel 执行循环从 sync 转为 async def，移除 Phase 13-16 遗留的全部 bridge 代码。前置工作已经彻底完成：LLM Provider (Phase 13)、Tool (Phase 14)、Hook (Phase 15)、MessageBus (Phase 16) 全部是 async。Kernel 是最后一个同步组件，其内部全部异步调用都通过 `_sync_call_async` / `_sync_iterate_async` 桥接，改造就是将这些桥接全部替换为原生 await / async for。

agent.py 当前 550 行，包含 47 处 bridge 引用（_sync_call_async 调用 + _bridge_loop 传参）、2 处 time.sleep、3 层嵌套 try/finally 管理 provider 生命周期。改造后代码量预计减少约 80 行，结构大幅简化。关键额外改动是 Exp.run() 和 spawn_fn 需要加入临时 bridge loop 来调用 async Kernel（Phase 18 Exp async 化时删除）。

测试迁移是本阶段的第二个主要工作量。test_agent.py 有 35 个测试全部是 sync def，需改为 async def。另有约 10 个外部测试文件直接调用 kernel.run()（集成测试、compactor 测试、DevShell 测试），这些不直接迁移，而是通过 Exp.run() bridge 间接适配。

**Primary recommendation:** agent.py 改造和测试迁移应在同一个 plan 中完成（代码改动和测试紧耦合），Exp.run() bridge 作为第二个 plan 或同 plan 第二步。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** `async def run() -> KernelRunResult`，不改为 async generator。事件继续走 MessageBus 传输路径。KERN-01 需修订：将 async generator 改为 async def。
- **D-02:** Bridge 模式从 agent.py 移入 Exp.run()。Phase 17 完成后，sync Exp.run() 内创建临时 bridge loop (`asyncio.new_event_loop()` + `run_until_complete(kernel.run(...))`) 调用 async Kernel。Phase 18 删除。
- **D-03:** spawn 子 agent 路径（exp.py 的 spawn_fn 闭包内 `child_runtime.kernel.run()`）同理使用 bridge loop 包装。
- **D-04:** agent.py 中 `_sync_call_async()` / `_sync_iterate_async()` / module-level `_bridge_loop` 全部删除。agent.py 变为纯 async 模块，零 bridge 残留。
- **D-05:** 不创建 matmaster/utils/async_bridge.py 共享模块。Exp.run() 内联 5 行桥接模式即可。

### Claude's Discretion
- ConfirmationHook loop 注入方式：async run() 内改用 asyncio.get_running_loop()
- Provider 生命周期管理：async run() 改用 `async with spec.llm_provider:` 语法
- 测试迁移策略：随实现同步迁移为 async def test
- GuardPipeline 保持同步调用
- stop_event 保持 threading.Event

### Deferred Ideas (OUT OF SCOPE)
None
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| KERN-01 | AgentKernel.run() 改为 async def (修订：不用 async generator，返回 KernelRunResult) | agent.py run() 签名改 async def，移除 bridge loop 创建，改用 async with provider |
| KERN-02 | Kernel 内部 LLM 调用改为 await | _call_llm / _do_stream_llm 改 async def，_sync_call_async -> await，_sync_iterate_async -> async for |
| KERN-03 | Kernel 内部 tool dispatch 改为 await | tool_registry.execute() 已是 async，直接 await（不含并行 gather，并行是 TOOL-06 / Phase 19） |
| KERN-04 | ContextCompactor 内部 LLM 调用 async | 已在 Phase 13 完成，compact_if_needed 已是 async def，Kernel 直接 await |
| KERN-05 | stop_event 保留 threading.Event | is_set() 同步检查不变，无代码改动需求 |
| KERN-06 | time.sleep 替换为 asyncio.sleep | agent.py L323 和 L342 两处 time.sleep(backoff) -> await asyncio.sleep(backoff) |
| TEST-02 | 现有测试随实现同步迁移为 async | test_agent.py 35 个 def test_ -> async def test_，pytest-asyncio auto mode 自动识别 |
| TEST-03 | 迁移后全部测试通过，无回归 | 1187 tests collected，改造后需全部通过 |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| asyncio (stdlib) | Python 3.13 | async/await runtime | 项目标准，不引入 anyio/trio |
| pytest-asyncio | 1.3.0 | async test support | 已安装并配置 asyncio_mode="auto" |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| threading (stdlib) | Python 3.13 | stop_event 跨线程信号 | stop_event 保持 threading.Event |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| asyncio.Event (for stop) | threading.Event | threading.Event 跨线程安全，is_set() 零开销同步调用。asyncio.Event 需要在同一 event loop 中使用，不适合跨线程场景 |

## Architecture Patterns

### Pattern 1: async def Kernel 方法签名

**What:** run() / _run_loop() / _call_llm() / _do_stream_llm() 全部改为 async def
**When to use:** 方法内部包含任何 await 或 async for 调用
**Example:**
```python
# Before (sync with bridge):
def _call_llm(self, spec, messages, *, _bridge_loop=None) -> LLMResponse:
    response = self._do_stream_llm(spec, messages, timeout=timeout, _bridge_loop=_bridge_loop)
    time.sleep(backoff)
    ...

# After (async):
async def _call_llm(self, spec, messages) -> LLMResponse:
    response = await self._do_stream_llm(spec, messages, timeout=timeout)
    await asyncio.sleep(backoff)
    ...
```

### Pattern 2: async with Provider 生命周期

**What:** 用 async context manager 语法替代手动 __aenter__/__aexit__
**When to use:** run() 方法内管理 provider 生命周期
**Example:**
```python
# Before (3-layer try/finally):
_bridge_loop.run_until_complete(spec.llm_provider.__aenter__())
try:
    if _summary_provider:
        _bridge_loop.run_until_complete(sp.__aenter__())
    try:
        return self._run_loop(...)
    finally:
        if _summary_provider:
            _bridge_loop.run_until_complete(sp.__aexit__(None, None, None))
finally:
    _bridge_loop.run_until_complete(spec.llm_provider.__aexit__(None, None, None))

# After (async with):
async with spec.llm_provider:
    # summary_provider 可能与 llm_provider 是同一实例，需条件判断
    if _summary_provider is not None:
        async with _summary_provider:
            return await self._run_loop(...)
    else:
        return await self._run_loop(...)
```

**CAUTION:** summary_provider 可能与 llm_provider 是同一实例。当两者相同时，不能嵌套两次 `async with`（会重复 __aenter__/__aexit__）。需保留当前的条件检查逻辑：`if sp is not spec.llm_provider`。

### Pattern 3: _sync_iterate_async -> async for

**What:** 将 sync iterator bridge 替换为原生 async for
**When to use:** 消费 chat_stream() 的 AsyncIterator
**Example:**
```python
# Before:
for chunk in _sync_iterate_async(
    spec.llm_provider.chat_stream(api_messages, tool_defs, timeout=timeout),
    _bridge_loop,
):
    ...

# After:
async for chunk in spec.llm_provider.chat_stream(api_messages, tool_defs, timeout=timeout):
    ...
```

### Pattern 4: Exp.run() 临时 Bridge

**What:** sync Exp.run() 内创建 event loop 调用 async kernel.run()
**When to use:** Phase 17 过渡期，Exp 层仍是 sync
**Example:**
```python
# Exp.run() bridge pattern:
def run(self, ctx, task, *, bus=None, history=None, stop_event=None, ...):
    runtime = self.build_runtime(ctx, bus=bus, ...)
    # ... stop_event injection ...
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            runtime.kernel.run(
                runtime.spec, task, history=history, stop_event=stop_event
            )
        )
        return result.result
    finally:
        loop.close()
        runtime.cleanup()
```

### Pattern 5: ConfirmationHook loop 注入

**What:** async run() 内用 asyncio.get_running_loop() 替代手动创建的 bridge loop
**When to use:** 注入 loop 到需要跨线程调度的 hook
**Example:**
```python
# Before (in sync _run_loop):
for hook in spec.hooks:
    if hasattr(hook, "set_loop"):
        hook.set_loop(_bridge_loop)

# After (in async _run_loop):
loop = asyncio.get_running_loop()
for hook in spec.hooks:
    if hasattr(hook, "set_loop"):
        hook.set_loop(loop)
```

**NOTE:** 当前 ConfirmationHook 实际上不使用 set_loop（它使用 ReplyQueueLike 同步阻塞）。但 duck-typed hasattr 检查保留，未来 ConfirmationHook 如果迁移到 asyncio.Future 模式会用到。保持注入逻辑无害。

### Anti-Patterns to Avoid
- **在 async def 内创建新的 event loop:** async kernel 方法内不应再创建 `asyncio.new_event_loop()`。_call_llm 当前有 `_owns_loop` fallback 逻辑，async 版本中直接移除。
- **混用 time.sleep 和 asyncio.sleep:** async 函数内 time.sleep 会阻塞 event loop。所有 backoff sleep 必须改为 `await asyncio.sleep()`。
- **重复进入 async context manager:** 如果 summary_provider is llm_provider，不能嵌套两次 `async with`。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| sync-to-async bridge | 自定义 thread pool executor | asyncio.new_event_loop() + run_until_complete | Exp.run() 只需 5 行内联 bridge，不需要复杂封装 |
| async test runner | 手动 asyncio.run() 在 test 中 | pytest-asyncio auto mode | 已配置好，async def test 自动识别 |
| event loop cleanup | 手动管理 loop 生命周期 | try/finally + loop.close() | Exp.run() bridge 需要确保 loop 正确关闭 |

## Common Pitfalls

### Pitfall 1: summary_provider 双重 async with
**What goes wrong:** 当 summary_provider 与 llm_provider 是同一实例时，嵌套 `async with` 会导致 __aenter__ 被调用两次
**Why it happens:** ContextCompactor 可以配置独立的 summary LLM，也可以复用主 provider
**How to avoid:** 保留当前的条件检查：`if sp is not spec.llm_provider`，只在独立实例时管理生命周期
**Warning signs:** provider 的内部 httpx client 被重复初始化或意外关闭

### Pitfall 2: _call_llm 的 _owns_loop fallback
**What goes wrong:** 当前 _call_llm 有 `if _bridge_loop is None: _bridge_loop = asyncio.new_event_loop()` 的 fallback 逻辑
**Why it happens:** 测试中直接调用 _call_llm 而不经过 run() 时需要自建 loop
**How to avoid:** async 版本中完全移除 _bridge_loop 参数和 _owns_loop 逻辑。_call_llm 变为纯 async def，测试通过 `await kernel._call_llm(...)` 直接调用
**Warning signs:** 测试中出现 "no running event loop" 错误

### Pitfall 3: Exp.run() bridge 中的 cleanup 时机
**What goes wrong:** 如果 loop.run_until_complete() 抛异常，cleanup 可能不执行
**Why it happens:** try/finally 结构不当
**How to avoid:** cleanup 必须在 finally 块中，且在 loop.close() 之前执行（cleanup 本身可能是 sync 的，不需要 loop）
**Warning signs:** 资源泄漏（MCP 连接未关闭、session 未清理）

### Pitfall 4: spawn_fn 中的 bridge loop 与父 Exp.run() loop 冲突
**What goes wrong:** spawn_fn 在 Exp.run() 的 bridge loop 内被调用（通过 tool dispatch），但 spawn_fn 自己也需要创建 bridge loop
**Why it happens:** spawn_fn 是 sync 函数，调用 async child kernel.run()。但 spawn_fn 本身是通过 async ToolRegistry.execute() -> SubAgentTool.execute() -> spawn_fn() 调用的
**How to avoid:** 分析调用链：Exp.run() bridge loop -> kernel.run() [async] -> tool_registry.execute() [async] -> SubAgentTool.execute() [async] -> spawn_fn() [sync]。spawn_fn 在 async 上下文中被调用，但它是 sync 的，所以它创建新的 event loop 不会与 running loop 冲突（asyncio.new_event_loop 创建的是独立 loop）。注意不能用 asyncio.get_event_loop() 或 asyncio.run() 因为当前已有 running loop。
**Warning signs:** "This event loop is already running" RuntimeError

### Pitfall 5: ExplodingTool 测试 -- sync execute()
**What goes wrong:** test_agent.py 中 ExplodingTool 的 execute() 是 sync def（非 async）
**Why it happens:** 测试遗留代码，Phase 14 前定义的 mock tool
**How to avoid:** 将 ExplodingTool.execute() 改为 async def。ToolRegistry.execute() 已能处理 async tool，但 Kernel 现在直接 await，如果返回非 coroutine 会出错。实际上 ToolRegistry.execute() 内部已做了 normalize，所以可能不会出错 -- 需验证。安全起见改为 async def。
**Warning signs:** "object str can't be used in 'await' expression" TypeError

### Pitfall 6: DevShell runner.py 直接调用 kernel.run()
**What goes wrong:** DevShell 的 DevRunner 直接调用 runtime.kernel.run()，kernel 变 async 后这个调用会返回 coroutine 而非结果
**Why it happens:** DevRunner 和 Exp.run() 一样直接调用 kernel.run()
**How to avoid:** DevRunner 也需要加 bridge loop 包装。但 CONTEXT.md 明确说 "本阶段不改 DevShell"，所以 DevShell 改动范围：仅 runner.py 的 kernel.run() 调用加 bridge，与 Exp.run() 相同模式
**Warning signs:** DevShell 运行时 result 是 coroutine 对象

## Code Examples

### 改造后的 async run() 签名

```python
async def run(
    self,
    spec: AgentRuntimeSpec,
    task: str,
    history: list[Message] | None = None,
    stop_event: threading.Event | None = None,
) -> KernelRunResult:
    """Execute the agent loop until termination."""
    async with spec.llm_provider:
        _summary_provider = None
        if spec.compactor and hasattr(spec.compactor, '_summary_provider'):
            sp = spec.compactor._summary_provider
            if sp is not spec.llm_provider:
                _summary_provider = sp

        if _summary_provider is not None:
            async with _summary_provider:
                return await self._run_loop(spec, task, history, stop_event)
        else:
            return await self._run_loop(spec, task, history, stop_event)
```

### 改造后的 async _run_loop() 核心循环

```python
async def _run_loop(
    self,
    spec: AgentRuntimeSpec,
    task: str,
    history: list[Message] | None,
    stop_event: threading.Event | None,
) -> KernelRunResult:
    messages = [SystemMessage(content=spec.system_prompt), *(history or []), UserMessage(content=task)]
    guard_pipeline = GuardPipeline(spec.guards)

    # Inject running loop to hooks that need it
    loop = asyncio.get_running_loop()
    for hook in spec.hooks:
        if hasattr(hook, "set_loop"):
            hook.set_loop(loop)

    turn = 0
    # ... (same logic, but all _sync_call_async -> await) ...

    while turn < spec.max_turns:
        if stop_event and stop_event.is_set():
            return self._finish(...)

        turn += 1
        await run_pre_llm_call(spec.hooks, messages, turn)

        if not await run_should_continue(spec.hooks, messages, turn):
            return self._finish(...)

        if spec.compactor:
            await spec.compactor.compact_if_needed(messages, last_usage, turn)

        response = await self._call_llm(spec, messages)
        # ... rest of loop ...
```

### 改造后的 async _do_stream_llm()

```python
async def _do_stream_llm(
    self,
    spec: AgentRuntimeSpec,
    messages: list[Message],
    *,
    timeout: float | None = None,
) -> LLMResponse:
    api_messages = [m.to_api_dict() for m in messages]
    tool_defs = (...)

    await run_on_stream_chunk(spec.hooks, StreamChunk(stream_state="start", stream_id=stream_id))
    try:
        async for chunk in spec.llm_provider.chat_stream(api_messages, tool_defs, timeout=timeout):
            if chunk.content or chunk.reasoning_content:
                await run_on_stream_chunk(spec.hooks, chunk.model_copy(...))
            # ... accumulation logic unchanged ...
    finally:
        # segment complete hooks
        if producing_reasoning:
            await run_on_segment_complete(spec.hooks, "thought", "".join(reasoning_parts), stream_id)
        if producing_content:
            await run_on_segment_complete(spec.hooks, "response", "".join(content_parts), stream_id)
        await run_on_stream_chunk(spec.hooks, StreamChunk(stream_state="end", stream_id=stream_id))
```

### Exp.run() bridge 模式

```python
def run(self, ctx, task, *, bus=None, history=None, stop_event=None, skills=None, mcp=None):
    runtime = self.build_runtime(ctx, bus=bus, skills=skills, mcp=mcp)
    # ... stop_event injection ...
    _loop = asyncio.new_event_loop()
    try:
        result = _loop.run_until_complete(
            runtime.kernel.run(runtime.spec, task, history=history, stop_event=stop_event)
        )
        return result.result
    finally:
        _loop.close()
        runtime.cleanup()
```

### 测试迁移模式

```python
# Before:
class TestNaturalFinish:
    def test_natural_finish(self) -> None:
        kernel = AgentKernel()
        result = kernel.run(spec, "test task")
        assert result.result.reason == "natural"

# After:
class TestNaturalFinish:
    async def test_natural_finish(self) -> None:
        kernel = AgentKernel()
        result = await kernel.run(spec, "test task")
        assert result.result.reason == "natural"
```

pytest-asyncio auto mode (asyncio_mode="auto" in pyproject.toml) 自动识别 async def test_ 函数，无需 @pytest.mark.asyncio。

## Scope and Impact Analysis

### 需要修改的文件

| File | Change Type | LOC Impact |
|------|-------------|------------|
| `matmaster/core/agent.py` | 核心改造：async 化 + 删除 bridge | ~-80 行（删除 bridge 函数和参数传递） |
| `matmaster/core/exp.py` | Exp.run() + spawn_fn 加 bridge | +10 行 |
| `matmaster/devshell/runner.py` | kernel.run() 加 bridge | +5 行 |
| `tests/matmaster/core/test_agent.py` | 35 个 test 改 async def + await | ~+35 行（每个 test 加 async/await） |

### 不需要修改的文件

| File | Reason |
|------|--------|
| `matmaster/core/hooks.py` | 已经是 async def，不变 |
| `matmaster/core/context_compactor.py` | 已经是 async def，不变 |
| `matmaster/tools/registry.py` | 已经是 async def，不变 |
| `matmaster/providers/openai_provider.py` | 已经是 async，不变 |
| `matmaster/core/bus.py` | 不变 |
| `src/services/agent_run_service.py` | 通过 Exp.run() 间接调用，Exp.run() bridge 屏蔽了变化 |

### 间接受影响的测试（通过 Exp.run() bridge 屏蔽）

| Test File | kernel.run() Calls | Impact |
|-----------|-------------------|--------|
| tests/matmaster/integration/test_pipeline_alignment.py | 1 | 直接调用 kernel.run() -- 需加 bridge 或改 async |
| tests/matmaster/integration/test_e2e_mat_master.py | 3 | 直接调用 kernel.run() -- 需加 bridge 或改 async |
| tests/matmaster/integration/test_e2e_minimal.py | 1 | 直接调用 kernel.run() -- 需加 bridge 或改 async |
| tests/matmaster/integration/test_upstream_scenarios.py | 2 | 直接调用 kernel.run() -- 需加 bridge 或改 async |
| tests/matmaster/integration/test_stream_timeout_retry.py | 1 | 直接调用 kernel.run() -- 需加 bridge 或改 async |
| tests/matmaster/core/test_context_compactor.py | 1 | 直接调用 kernel.run() -- 需加 bridge 或改 async |
| tests/matmaster/devshell/test_compaction_via_devshell.py | 2 | 直接调用 kernel.run() -- 需加 bridge 或改 async |
| tests/matmaster/integration/test_compaction_real_api.py | 3 | 直接调用 kernel.run() -- 需加 bridge 或改 async |
| tests/matmaster/core/test_exp.py | 通过 patch mock | Mock kernel 需适配 async run() 签名 |

**关键发现:** 除 test_agent.py 外，还有 8 个测试文件共 ~14 处直接调用 kernel.run()。这些测试有两种处理方案：
1. 改为 async def test + await kernel.run()（与 test_agent.py 一致）
2. 测试内部使用 asyncio.run(kernel.run(...)) bridge

方案 1 更干净（pytest-asyncio auto mode 支持），方案 2 对测试结构改动更小。推荐方案 1，因为项目已全面采用 pytest-asyncio。

### test_exp.py 的 mock 适配

test_exp.py 通过 `patch("matmaster.core.agent.AgentKernel")` mock kernel。mock 的 kernel.run() 返回值需要是 awaitable（因为 Exp.run() 通过 bridge loop 调用 async kernel.run()）。当前 mock 返回的是同步值，bridge `loop.run_until_complete()` 需要 coroutine。处理方案：
- MagicMock 的 return_value 不是 coroutine，`run_until_complete(mock_result)` 会 TypeError
- 需要使用 `AsyncMock` 或确保 mock kernel.run() 返回 coroutine

但实际上 test_exp.py 中 Exp.run() 调用 kernel.run() 的部分是否被 mock 拦截取决于 patch 的精确性。如果 mock 完全替代了 AgentKernel class，那么 mock.return_value.run.return_value 需要是一个 coroutine 才能被 run_until_complete 消费。

这是需要仔细处理的测试适配点。

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.x + pytest-asyncio 1.3.0 |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/matmaster/core/test_agent.py -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| KERN-01 | run() is async def returning KernelRunResult | unit | `uv run pytest tests/matmaster/core/test_agent.py -x` | Exists (needs async migration) |
| KERN-02 | _call_llm / _do_stream_llm use await | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry -x` | Exists (needs async migration) |
| KERN-03 | tool dispatch uses await | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestFullCycle -x` | Exists (needs async migration) |
| KERN-04 | ContextCompactor already async | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestCompactorIntegration -x` | Exists (needs async migration) |
| KERN-05 | stop_event is threading.Event | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestExternalCancel -x` | Exists (needs async migration) |
| KERN-06 | asyncio.sleep replaces time.sleep | unit | `uv run pytest tests/matmaster/core/test_agent.py::TestCallLlmRetry -x` | Exists (verify no time.sleep in agent.py) |
| TEST-02 | Tests migrated to async | integration | `uv run pytest tests/matmaster/core/test_agent.py -x` | Exists (needs migration) |
| TEST-03 | All tests pass, no regression | full suite | `uv run pytest` | N/A (gate check) |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/core/test_agent.py -x`
- **Per wave merge:** `uv run pytest tests/matmaster/ -x`
- **Phase gate:** `uv run pytest` (full suite, 1187+ tests green)

### Wave 0 Gaps
None -- existing test infrastructure covers all phase requirements. pytest-asyncio auto mode and async mock factories are already in place from Phase 12.

## Open Questions

1. **test_exp.py mock 适配**
   - What we know: Exp.run() 将使用 bridge loop 调用 async kernel.run()。test_exp.py 通过 patch 替换 AgentKernel class。
   - What's unclear: MagicMock.return_value.run.return_value 是否需要改为 AsyncMock 以返回 coroutine，或者 Exp.run() 的 bridge `loop.run_until_complete()` 能否接受 non-coroutine。
   - Recommendation: 在实现时验证。如果 run_until_complete 要求 coroutine，则 test_exp.py 中使用 `AsyncMock` 或配置 `mock.return_value.run = AsyncMock(return_value=...)` 。
   - Confidence: HIGH -- 这是已知的 mock 兼容性问题，有标准解决方案。

2. **ConfirmationHook sync pre_tool_call**
   - What we know: ConfirmationHook.pre_tool_call() 当前是 sync def（非 async），但 BaseHook 和 Hook Protocol 定义的是 async def。
   - What's unclear: 当 Kernel 直接 await hook.pre_tool_call() 时，sync 函数的返回值不是 coroutine，await 会 TypeError。
   - Recommendation: 这不是 Phase 17 直接的问题（ConfirmationHook 通过 run_pre_tool_call helper 调用，helper 已经是 async def 并 await 每个 hook 的 pre_tool_call），但 run_pre_tool_call 内部 await 一个 sync 函数也会出错。需要确认 ConfirmationHook 是否在 Phase 15 已经修复为 async，或者当前代码是否存在潜在 bug。
   - Confidence: MEDIUM -- 需要验证运行时行为。如果 ConfirmationHook 实际被注入到 kernel（而非仅在 service 层），这会是一个 bug。

## Sources

### Primary (HIGH confidence)
- `matmaster/core/agent.py` -- 当前 AgentKernel 实现，完整阅读 550 行
- `matmaster/core/exp.py` -- Exp.run() 和 spawn_fn，完整阅读 488 行
- `matmaster/core/hooks.py` -- Hook Protocol + run_* helpers，完整阅读 280 行
- `matmaster/core/context_compactor.py` -- compact_if_needed 已 async，完整阅读 363 行
- `tests/matmaster/core/test_agent.py` -- 35 个测试，完整阅读 1196 行
- `tests/conftest.py` -- async mock factories，完整阅读 135 行
- `.planning/phases/17-agentkernel/17-CONTEXT.md` -- 用户决策
- `.planning/REQUIREMENTS.md` -- 需求定义
- `.planning/STATE.md` -- 项目状态

### Secondary (MEDIUM confidence)
- `matmaster/hooks/confirmation.py` -- ConfirmationHook 当前实现（sync pre_tool_call 发现）
- `matmaster/devshell/runner.py` -- DevRunner 直接 kernel.run() 调用
- 10 个外部测试文件 -- grep kernel.run() 调用站点

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 不引入新依赖，纯 asyncio stdlib
- Architecture: HIGH - 模式完全确定（CONTEXT.md locked decisions），前置 Phase 13-16 已验证
- Pitfalls: HIGH - 基于完整代码阅读的分析，所有 edge case 已识别
- Test migration: HIGH - pytest-asyncio auto mode 已验证，模式清晰

**Research date:** 2026-03-29
**Valid until:** 2026-04-28 (stable domain, 30 days)
