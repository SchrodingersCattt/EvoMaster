# Phase 36: 去总线化 + 高级调度 - Research

**Researched:** 2026-04-03
**Domain:** Service-layer event transport removal, handler lifecycle ownership, DevShell local observation, scheduler scope validation
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

### 去总线化范围
- **D-01:** MessageBus（bus.py）和 EventRouter（event_router.py）物理删除。不降级保留、不标记 deprecated。一次性完成。
- **D-02:** 所有 bus.emit / bus.emit_nowait 调用点改为 fanout 直连 handler。包括 run_agent_stream() 主循环、后处理事件（CancelledEvent / StreamClosedEvent）、以及 BohriumSetupService._make_event_bridge() 的线程安全桥接。

### Bohrium 事件桥接（线程安全）
- **D-11:** `agent_run_bohrium.py` L360-414 的 `_make_event_bridge()` 当前通过 `loop.call_soon_threadsafe(bus.emit_nowait)` 从 Bohrium 工作线程推送 ErrorEvent/StreamClosedEvent/BohriumNodeEvent。Bus 删除后此路径必须同步改造。替代方案：`_make_event_bridge()` 接受 fanout 的同步入口（`loop.call_soon_threadsafe` 调度一个将事件送入 fanout 的闭包），或者 BohriumSetupService 接受 `event_sink: Callable` 替代 `bus` 参数。此改造直接关系 ROADMAP 成功标准 3（Kernel 外事件通过 async fanout 直连消费者）。

### ConfirmationHook 处置
- **D-03:** ConfirmationHook 直接删除。不改造为 event_sink 模式。当前确认功能前端未使用，后续在 generator 双向流设计中重建（v2.3+）。
- **D-04:** 随 ConfirmationHook 删除，清除 `matmaster/hooks/` 目录（仅剩 confirmation.py）。Hook 基础设施（Hook Protocol / BaseHook / HookAction）的清理程度由 Claude 根据 DevStreamHook 和剩余消费者的实际依赖决定。

### async fanout 设计
- **D-05:** SSE 同步 await 优先 + 持久化 asyncio.create_task() 异步。SSEHandler.handle() 在事件循环中同步执行保证前端低延迟，PersistenceHandler.handle() 通过 create_task 异步执行不阻塞事件流。
- **D-06:** 持久化 task 通过 TaskGroup（或 set[Task]）收集管理生命周期。run 结束时 drain 剩余 task（类似当前 EventRouter.stop() 的 drain 逻辑）。不丢事件。
- **D-12:** fanout 必须接管 EventRouter.stop() 当前承担的 handler 生命周期管理。具体：run 结束时（正常或异常）先 drain 所有 pending persistence task，然后对所有 handler 调用 close()（如果存在）。WorkspaceHandler.close()（L173-183）依赖此阶段等待挂起的 workspace 上传结束——丢失 close() 调用会导致归档上传静默丢弃。

### run_agent() 旧路径
- **D-07:** 删除 run_agent() 方法。上游调用者（`src/worker/agent_worker.py` L254）迁移到 run_agent_stream()。
- **D-08:** 改造完成后 run_agent_stream() 更名为 run_agent()。最终 AgentRunService 只有一个执行入口。
- **D-09:** Worker 模式（无 SSE）的 send_cb 处理方式由 Claude 决定（no-op callback 或可选 handler 列表）。

### ASCH-01 高级调度
- **D-10:** Phase 36 跳过 ASCH-01。当前 LocalSession 和 SSHSession 均为 shell_persistence="stateless"，无 persistent shell 消费场景。待未来 persistent shell 实现后再追加调度增强。

### DevShell 范围界定
- **D-13:** DevShell（runner.py / cli.py / repl.py / debug_run.py）当前直接使用 MessageBus 作为 runner→EventLogger 的事件通道。Phase 36 删除 MessageBus 后这些代码会破损。改造范围：将 MessageBus 依赖替换为轻量替代（asyncio.Queue 或直接调用 EventLogger.log_event()），保持 DevShell 现有架构不变。这不是 FUTR-03（DevShell 消费 run_stream() 替代 run() + Bus），FUTR-03 仍属 v2.3+ 范围。Phase 36 只做 MessageBus 符号消除，不改变 DevShell 的执行模型。

### Claude's Discretion
- Hook 基础设施（Hook Protocol / BaseHook / HookAction / dispatch 函数）的清理范围——取决于 DevStreamHook 和 InlineToolRunner 的实际依赖
- fanout 函数/类的具体形式和放置位置
- Worker 模式的 SSE 处理（no-op send_cb vs handlers 列表可选）
- DevShell 中 MessageBus 的具体替代实现（asyncio.Queue vs 直调 vs 其他）
- Bohrium 事件桥接的线程安全入口的具体实现（BohriumSetupService 接受 event_sink 还是 fanout 暴露 sync 入口）
- InlineToolRunner 是否同步删除（FullToolRunner 已是默认路径）
- ContextCompactor bus= 兼容参数的清理

### Deferred Ideas (OUT OF SCOPE)
- ASCH-01 高级调度 — 待 persistent shell 实现后追加
- ConfirmationHook generator 双向流重建 — v2.3+
- InlineToolRunner 清理 — 如果 Phase 36 未删除，留给后续
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DBUS-01 | 审计 MessageBus + EventRouter 的全部消费者（SSEHandler / PersistenceHandler / DevShell EventLogger / 其他） | Audit surface confirmed in `AgentRunService`, `BohriumSetupService`, `Exp`/spawn signatures, DevShell, exports, and multiple stale bus-based tests. |
| DBUS-02 | 设计并实现消费侧 async fanout 替代方案，确保 SSE 先发、持久化不阻塞 token 流 | Research recommends a per-run fanout owner with SSE awaited first, persistence background tasks held strongly and drained, workspace handler close preserved, and Bohrium bridge scheduling back onto the loop thread. |
| DBUS-03 | 移除 MessageBus + EventRouter，generator 事件直连消费者 | Research identifies the deletion order, final single-entrypoint shape, bus signature cleanup in `Exp`, and the tests/docs/exports that must be rewritten or removed. |
| ASCH-01 | ToolScheduler 根据 SessionCapabilities 动态调整并发策略（如 persistent shell 下支持 shell 并发） | Research confirms current sessions are still stateless and `ToolScheduler` itself does not inspect `SessionCapabilities`; Phase 36 should document and preserve the current no-op/defer state rather than invent persistent-shell behavior. |
</phase_requirements>

## Summary

Phase 36 should be planned as a transport and lifecycle consolidation phase, not a kernel execution rewrite. The generator path is already the live business-event source (`AgentKernel.run_stream()` -> `Exp.run_stream()` -> `AgentRunService.run_agent_stream()`); the remaining work is to replace the per-run `MessageBus` + `EventRouter` transport with an explicit fanout owner, delete the legacy `run_agent()` path, and clean `bus` signatures out of `Exp`, spawn, Bohrium, DevShell, exports, and tests.

The highest-risk behavior is hidden in cleanup, not dispatch. `EventRouter.stop()` currently does three critical things: it stops consumption, drains remaining queued events, and calls `close()` on handlers. `WorkspaceHandler.close()` is what waits for queued uploads to finish; if fanout only forwards events and does not take over this lifecycle, Phase 36 will silently lose final persistence rows or workspace uploads. A second hidden dependency is worker publishing: the only production caller of `AgentRunService.run_agent()` is `src/worker/agent_worker.py`, and its `send_cb` publishes live events to Redis, so worker mode cannot use a no-op callback.

Focused baseline checks already reveal migration debt. `uv run pytest tests/matmaster/core/test_tool_scheduler.py tests/matmaster/integration/test_event_router.py -q` passed (58 tests), and `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q` passed (11 tests). But `tests/matmaster/devshell/test_integration.py`, `tests/matmaster/integration/test_pipeline_alignment.py`, and `tests/matmaster/integration/test_e2e_minimal.py` already fail because they still expect pre-Phase-34 bus delivery from `kernel.run()`. Plan these as stale contract cleanup, not as behavior to preserve.

**Primary recommendation:** Introduce a small per-run `RunEventFanout` owner in the service layer, keep `SSEHandler`/`PersistenceHandler`/`WorkspaceHandler` as the canonical consumers, delete `run_agent()` and `MessageBus`/`EventRouter`, and treat ASCH-01 as an explicit no-op/defer decision unless the Python floor or session model changes.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python `asyncio` | stdlib (`TaskGroup` added in 3.11) | per-run fanout, pending-task draining, `loop.call_soon_threadsafe()` bridge | already underpins the current router, kernel, worker, and Bohrium bridge; no new runtime dependency needed |
| `matmaster.integration.sse_handler.SSEHandler` | repo-local | live public event delivery | already owns frontend skip rules and payload serialization entrypoint |
| `matmaster.integration.persistence_handler.PersistenceHandler` | repo-local | DB event persistence | already owns persistence skip rules, `spawn_id` propagation, and replay contract |
| `matmaster.integration.workspace_handler.WorkspaceHandler` | repo-local | workspace archival side effects | already encapsulates debouncing, upload threading, and must retain `close()` ownership |
| `matmaster.integration.event_payloads` | repo-local | shared public payload mapping | keeps SSE and persistence payload semantics aligned |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `inspect.isawaitable` | stdlib | handler `close()` compatibility for sync/async closers | preserve `EventRouter._close_handlers()` semantics inside fanout shutdown |
| `queue.SimpleQueue` | stdlib (3.7+) | DevShell thread-safe local event handoff | prefer for DevShell replacement because official docs say `asyncio.Queue` is not thread-safe |
| `pytest` | `>=9.0.2` | focused regression suite | current project test runner from `pyproject.toml` |
| `pytest-asyncio` | `>=0.24.0` | async test support | current async test harness from `pyproject.toml` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| direct per-run fanout | keep `EventRouter` with a renamed internal queue pump | violates locked deletion goal and preserves the same lifecycle indirection under a different name |
| tracked `set[Task]` for persistence tasks | `asyncio.TaskGroup` | `TaskGroup` is cleaner and officially recommended, but it requires Python 3.11+ while `pyproject.toml` still declares `>=3.10` |
| `queue.SimpleQueue` in DevShell | direct `EventLogger.log_event()` calls from runner thread | direct calls are simpler, but a queue preserves current REPL/CLI polling structure and decouples the producer from the logger |

**Installation:**
```bash
uv sync --extra dev
# No new runtime dependency is required for Phase 36.
```

**Version verification:**
- `pyproject.toml` currently declares `requires-python = ">=3.10"`.
- `pyproject.toml` declares `pytest>=9.0.2` and `pytest-asyncio>=0.24.0`.
- Official Python docs confirm `asyncio.TaskGroup` was added in Python 3.11 and that `asyncio.create_task()` background work must be kept in a strong-reference collection.
- Official Python docs confirm `asyncio.Queue` is not thread-safe, while `queue.Queue` / `queue.SimpleQueue` are the thread-safe stdlib choices for threaded producer/consumer flows.

## Architecture Patterns

### Recommended Project Structure
```text
src/services/
├── agent_run_service.py      # final single run entrypoint + fanout owner usage
├── agent_run_bohrium.py      # Bohrium callback -> threadsafe event sink
└── stream_service.py         # unchanged outer SSE queueing / worker enqueue flow

matmaster/integration/
├── fanout.py                 # new: EventHandler protocol + RunEventFanout owner
├── sse_handler.py            # existing live consumer
├── persistence_handler.py    # existing persist consumer
└── workspace_handler.py      # existing archival consumer

matmaster/devshell/
├── runner.py                 # local observer injection (no MessageBus)
├── repl.py                   # thread-safe local queue polling
├── cli.py                    # thread-safe local queue polling
└── event_logger.py           # unchanged log sink
```

### Pattern 1: Per-Run Fanout Owner
**What:** A small owner object created by `AgentRunService` that holds ordered handlers, tracks background persistence tasks, exposes `dispatch()` for same-loop events, and exposes `drain_and_close()` for end-of-run shutdown.

**When to use:** For all events emitted by `Exp.run_stream()` plus service-generated terminal/system events (`RunResultEvent`, `CancelledEvent`, `StreamClosedEvent`, `ErrorEvent`, Bohrium bridge events).

**Example:**
```python
# Source: adapted from `matmaster/integration/event_router.py`
# and https://docs.python.org/3/library/asyncio-task.html
import asyncio
import inspect
import logging

logger = logging.getLogger(__name__)

class RunEventFanout:
    def __init__(self, *, sse_handler, persistence_handler, workspace_handler=None):
        self._sse = sse_handler
        self._persistence = persistence_handler
        self._workspace = workspace_handler
        self._pending_persistence: set[asyncio.Task[None]] = set()

    async def dispatch(self, event) -> None:
        await self._safe_handle(self._sse, event)  # latency-sensitive path first
        self._spawn_persistence(event)
        if self._workspace is not None:
            await self._safe_handle(self._workspace, event)

    def _spawn_persistence(self, event) -> None:
        task = asyncio.create_task(self._persistence.handle(event), name="persist-event")
        self._pending_persistence.add(task)
        task.add_done_callback(self._pending_persistence.discard)

    async def drain_and_close(self) -> None:
        if self._pending_persistence:
            await asyncio.gather(*self._pending_persistence, return_exceptions=True)
        for handler in (self._sse, self._persistence, self._workspace):
            if handler is None:
                continue
            close = getattr(handler, "close", None)
            if not callable(close):
                continue
            result = close()
            if inspect.isawaitable(result):
                await result

    async def _safe_handle(self, handler, event) -> None:
        try:
            await handler.handle(event)
        except Exception:
            logger.warning("Handler failed for %s", getattr(event, "type", "?"), exc_info=True)
```

### Pattern 2: Source-Normalizing Service Loop
**What:** Keep the current `run_agent_stream()` normalization rule at the service edge: normalize only generator-originated `source` labels before dispatch, then let existing handlers reuse the normalized event objects.

**When to use:** Only for `Exp.run_stream()` events coming from kernel/generator code. Do not blindly normalize service-generated `System` or Bohrium callback sources.

**Example:**
```python
# Source: `src/services/agent_run_service.py` + `matmaster/integration/event_payloads.py`
async with aclosing(exp.run_stream(..., source_override=exp_name)) as stream:
    async for event in stream:
        if hasattr(event, "source"):
            normalized = _normalize_public_source(event.source)
            if event.source != normalized:
                event = event.model_copy(update={"source": normalized})
        await fanout.dispatch(event)
        if isinstance(event, RunResultEvent):
            run_result_event = event
```

### Pattern 3: Thread-Safe External Bridge
**What:** Keep Bohrium callback mapping in `BohriumSetupService`, but replace `bus.emit_nowait()` with a sync sink that schedules fanout dispatch onto the owning event loop using `loop.call_soon_threadsafe()`.

**When to use:** For all Bohrium setup/cleanup callbacks executed inside `run_in_executor()` worker threads.

**Example:**
```python
# Source: adapted from `src/services/agent_run_bohrium.py::_make_event_bridge`
import asyncio

def make_threadsafe_sink(loop, dispatch_async):
    def emit_from_thread(event):
        loop.call_soon_threadsafe(lambda: asyncio.create_task(dispatch_async(event)))
    return emit_from_thread
```

### Pattern 4: DevShell Local Observer Adapter
**What:** Keep DevShell on `kernel.run()` if Phase 36 must avoid `run_stream()`, but replace the dead bus path with a DevShell-only observer adapter: thread-safe queue for local logging, a tiny hook for `thought`/`response` segment snapshots, a wrapped ToolRunner `on_result` path for `tool_call`/`tool_result`, and direct compactor `event_sink` wiring for `ContextCompactionEvent`.

**When to use:** Only in `matmaster/devshell/*`. Do not generalize this back into production service transport.

**Example:**
```python
# Source: `matmaster/devshell/repl.py` thread model +
# `matmaster/core/tool_runner.py::execute_batch(on_result=...)`
from queue import SimpleQueue

event_queue = SimpleQueue()

# main thread polls event_queue.get_nowait()
# runner thread puts ToolCallEvent / ToolResultEvent / RunResultEvent into it
```

### Anti-Patterns to Avoid
- **Hidden replacement bus:** deleting `MessageBus` but introducing another queue + dedicated consumer task under a new name defeats the phase goal.
- **Fire-and-forget persistence without ownership:** plain `create_task()` with no retained reference can drop work; official docs require a strong-reference collection.
- **`asyncio.Queue` in DevShell threads:** official docs say it is not thread-safe; it is the wrong replacement for the current REPL/CLI worker-thread pattern.
- **Worker `send_cb` as no-op:** the worker callback publishes live events to Redis; making it a no-op breaks cross-pod streaming.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Production event transport | a renamed generic event bus or extra queue pump | a small per-run fanout owner around existing handlers | the remaining problem is lifecycle ordering, not general messaging infrastructure |
| Public payload mapping | duplicate `tool_result` / `confirmation_request` / `spawn_id` shaping logic | `matmaster.integration.event_payloads` | it already centralizes the live/replay contract and has existing tests |
| DevShell cross-thread transport | `asyncio.Queue` or ad-hoc shared list | `queue.SimpleQueue` or `queue.Queue` | official stdlib docs explicitly describe queue module as the thread-safe choice |
| Persistent-shell scheduling | speculative new scheduler branches | current ToolCompiler claim relaxation + explicit ASCH-01 defer | there is no persistent session implementation to validate against today |

**Key insight:** This phase should delete transport indirection, not invent a new transport abstraction.

## Runtime State Inventory

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None requiring migration. `evo_chat_events` stores business event types (`thought`, `tool_result`, `run_result`, etc.), not `MessageBus` / `EventRouter` identifiers. Redis keys are generic stream/run keys, not bus-named. | Code edit only; no data migration |
| Live service config | None found in repo-managed Redis/MySQL integration or planning docs that stores `MessageBus` / `EventRouter` as runtime config. | None |
| OS-registered state | None found. This phase changes in-process Python transport only. | None |
| Secrets/env vars | None found. No env var names or secret keys reference the bus/router symbols. | None |
| Build artifacts | None identified that cache these names as installed artifact identifiers. Test/docs imports will need code updates, but no artifact migration was found. | Code edit only |

**Nothing found in category:** verified from repo schema/config/code review; no external data migration surfaced for this transport refactor.

## Common Pitfalls

### Pitfall 1: Losing Final Events on Shutdown
**What goes wrong:** The frontend sees the final answer, but DB rows or workspace uploads are missing.
**Why it happens:** `EventRouter.stop()` currently owns both queue draining and handler `close()`. Removing it without an equivalent owner drops late persistence work.
**How to avoid:** Make fanout shutdown explicit: `dispatch` during run, then `drain pending persistence`, then `close handlers` after Bohrium + Exp cleanup.
**Warning signs:** Missing `run_result` / `stream_closed` rows, or archived workspace missing the last tool-written files.

### Pitfall 2: Breaking Bohrium Thread Safety
**What goes wrong:** `bohrium_node`, `error`, or `stream_closed` events vanish or raise loop/thread errors.
**Why it happens:** Bohrium setup/cleanup callbacks run in executor threads; direct async dispatch from that thread is invalid.
**How to avoid:** Keep a sync bridge that uses `loop.call_soon_threadsafe()` to schedule fanout dispatch on the owning loop.
**Warning signs:** Bohrium setup logs show progress, but no matching SSE/persisted node events appear.

### Pitfall 3: Source Normalization Drift
**What goes wrong:** History replay or `ChatHistoryConverter` stops recognizing main-agent rows, or persisted `System`/`User` sources become over-normalized.
**Why it happens:** Current service logic normalizes generator events before dispatch; removing the bus does not remove this responsibility.
**How to avoid:** Normalize only generator-originated agent events at the service edge, exactly once.
**Warning signs:** persisted rows start storing `agent` again, or `System` rows get flattened to `MatMaster`.

### Pitfall 4: Worker Mode “No-Op send_cb”
**What goes wrong:** Worker executions stop reaching active SSE subscribers on other pods.
**Why it happens:** In production, the worker `send_cb` publishes to Redis; it is not an optional local UI callback.
**How to avoid:** Keep `SSEHandler` (or an equivalent publisher handler) in the worker path, backed by the existing Redis-publishing callback.
**Warning signs:** runs complete and persist, but live streams stay idle until replay.

### Pitfall 5: Choosing `asyncio.Queue` for DevShell
**What goes wrong:** DevShell event logging becomes flaky or racy across the REPL main thread and runner worker thread.
**Why it happens:** Official Python docs state `asyncio.Queue` is not thread-safe.
**How to avoid:** Use `queue.SimpleQueue` / `queue.Queue`, or keep logging entirely within one thread.
**Warning signs:** intermittent missing local events, polling races, or thread-related test flakiness.

### Pitfall 6: Preserving Obsolete Bus Contracts Through Tests
**What goes wrong:** The implementation gets pulled back toward dead behavior just to satisfy stale tests.
**Why it happens:** Several existing tests still assert that `kernel.run()` or DevShell populates a bus, even though the Phase 34 generator transition already bypassed that path.
**How to avoid:** Rewrite or delete stale bus-based tests before using them as a phase gate.
**Warning signs:** you feel pressure to re-add bus hooks just to make `test_devshell_integration`, `test_pipeline_alignment`, or `test_e2e_minimal` pass.

### Pitfall 7: Over-Implementing ASCH-01
**What goes wrong:** The phase grows into speculative scheduler work with no real runtime consumer.
**Why it happens:** The roadmap wording still mentions persistent-shell concurrency, but current `LocalSession` and `SSHSession` both report `shell_persistence="stateless"`.
**How to avoid:** Treat ASCH-01 as a documented defer/no-op for Phase 36 unless the team explicitly chooses to raise scope.
**Warning signs:** edits start changing session persistence semantics or inventing scheduler branches with no testable caller.

## Code Examples

Verified patterns from official sources and current codebase:

### Service-Level Fanout with Strong Task Ownership
```python
# Source: https://docs.python.org/3/library/asyncio-task.html
# "Save a reference ... The event loop only keeps weak references to tasks."
self._pending_persistence: set[asyncio.Task[None]] = set()

task = asyncio.create_task(self._persistence.handle(event), name="persist-event")
self._pending_persistence.add(task)
task.add_done_callback(self._pending_persistence.discard)
```

### Reusing Router Close Semantics Without Keeping the Router
```python
# Source: `matmaster/integration/event_router.py::_close_handlers`
close = getattr(handler, "close", None)
if callable(close):
    result = close()
    if inspect.isawaitable(result):
        await result
```

### DevShell Thread-Safe Replacement for MessageBus
```python
# Source: https://docs.python.org/3/library/asyncio-queue.html
# asyncio queues are not thread-safe; use queue.SimpleQueue for threaded handoff.
from queue import Empty, SimpleQueue

event_queue = SimpleQueue()

# worker thread
event_queue.put(event)

# main thread
try:
    event = event_queue.get_nowait()
    event_logger.log_event(event)
except Empty:
    pass
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `Hook -> MessageBus -> EventRouter -> handlers` | `AgentKernel.run_stream()` -> `Exp.run_stream()` -> service transport | Phase 34 introduced the generator path | Phase 36 can remove the queue/consumer middle layer because the generator path is already live |
| Two service entrypoints: legacy `run_agent()` plus transitional `run_agent_stream()` | single generator-driven service entrypoint | Phase 36 target | reduces duplicated setup/cleanup code and removes the dead old event path |
| “Scheduler is session-capability aware” as a spec narrative | current code only uses `SessionCapabilities` in ToolCompiler/StructuralValidation; `ToolScheduler` itself is generic | Phases 33-35 | ASCH-01 is not a live feature yet; it should not drive scope expansion in Phase 36 |

**Deprecated/outdated:**
- `matmaster/core/bus.py` and `matmaster/integration/event_router.py` as service transport.
- `bus=` compatibility parameters in `Exp.build_runtime()`, `Exp.run()`, `Exp.run_stream()`, `Exp._make_spawn_fn()`, and `ContextCompactor`.
- DevShell and kernel integration tests that still assume `kernel.run()` populates a `MessageBus`.

## Open Questions

1. **Do we still mean `requires-python >=3.10` as a hard compatibility promise?**
   - What we know: `pyproject.toml` still declares `>=3.10`, but dev/CI guidance in project docs assumes the uv-managed environment (currently 3.13-class runtime).
   - What's unclear: whether Phase 36 may legally adopt `asyncio.TaskGroup` without a fallback.
   - Recommendation: decide this in Wave 0. If 3.10 support still matters, use a tracked `set[Task]`; otherwise raise the floor explicitly.

2. **How much confirmation plumbing should Phase 36 remove?**
   - What we know: `_CONFIRM_TOOLS` is empty, `ConfirmationHook` is the only runtime producer of `confirmation_request`, and current generator/service paths do not actively use confirmation gating.
   - What's unclear: whether API-level confirmation reply plumbing should be cleaned now or left as dormant future scaffolding for FUTR-02.
   - Recommendation: remove hook/runtime integration now, but leave API/reply-queue plumbing unless the milestone owner explicitly broadens scope.

3. **Where should the `EventHandler` Protocol live after deleting `event_router.py`?**
   - What we know: `SSEHandler`, `PersistenceHandler`, and `WorkspaceHandler` all still fit the same `handle()` / optional `close()` contract.
   - What's unclear: whether to keep explicit protocol typing or rely on structural typing only.
   - Recommendation: move the protocol into the new fanout module so typing survives deletion cleanly.

4. **Does DevShell need full event parity or only enough logging for local debugging?**
   - What we know: current DevShell tests expect `tool_call`, `tool_result`, and compaction-style bus events, but the current implementation is already stale.
   - What's unclear: whether Phase 36 should re-establish full EventLogger parity or accept a narrower local log surface until FUTR-03.
   - Recommendation: re-establish at least `thought`, `response`, `tool_call`, `tool_result`, and `run_result`; defer anything broader only if tests are updated to match.

## Validation Architecture

### Baseline Observations
- `uv run pytest tests/matmaster/core/test_tool_scheduler.py tests/matmaster/integration/test_event_router.py -q` -> **58 passed**
- `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q` -> **11 passed**
- `uv run pytest tests/matmaster/devshell/test_integration.py -q` -> **2 failed** (stale bus expectations)
- `uv run pytest tests/matmaster/integration/test_pipeline_alignment.py tests/matmaster/integration/test_e2e_minimal.py -q` -> **2 failed** (stale bus expectations)

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest>=9.0.2` + `pytest-asyncio>=0.24.0` |
| Config file | `pyproject.toml` |
| Quick run command | `uv run pytest tests/matmaster/core/test_tool_scheduler.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q` |
| Full suite command | `uv run pytest tests/matmaster/core/test_tool_scheduler.py tests/matmaster/integration/test_event_router.py tests/matmaster/integration/test_workspace_handler.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py tests/matmaster/devshell/test_integration.py -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DBUS-01 | Audit every remaining consumer/import/call-site of `MessageBus` and `EventRouter` across service, Bohrium, Exp/spawn, DevShell, exports, and tests | static + focused integration | `rg -n "MessageBus|EventRouter" matmaster src tests` | ✅ |
| DBUS-02 | Fanout preserves SSE-first behavior, async persistence, error isolation, drain, and handler close semantics | integration | `uv run pytest tests/matmaster/integration/test_event_fanout.py tests/matmaster/integration/test_workspace_handler.py -q` | ❌ Wave 0 |
| DBUS-03 | Legacy bus/router removed; generator/service path dispatches directly to consumers and still emits terminal/system events correctly | service integration | `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q` | ✅ |
| ASCH-01 | Preserve/verify current stateless-session scheduling behavior without inventing persistent-shell support | unit | `uv run pytest tests/matmaster/tools/test_tool_compiler.py tests/matmaster/core/test_tool_scheduler.py -q` | ✅ |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py tests/matmaster/core/test_tool_scheduler.py -q`
- **Per wave merge:** `uv run pytest tests/matmaster/core/test_tool_scheduler.py tests/matmaster/tools/test_tool_compiler.py tests/matmaster/integration/test_workspace_handler.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/test_bohrium_setup_injection.py -q`
- **Phase gate:** fanout replacement suite green, DevShell replacement suite green, and `rg -n "MessageBus|EventRouter" matmaster src tests` only returns intentionally preserved doc/history references

### Wave 0 Gaps
- [ ] `tests/matmaster/integration/test_event_fanout.py` — replace router dispatch/drain/close/error-isolation coverage after deleting `event_router.py`
- [ ] `tests/matmaster/services/test_agent_run_stream.py` — rename or refocus around the final single `run_agent()` entrypoint and fanout spy instead of bus spy
- [ ] `tests/matmaster/test_bohrium_setup_injection.py` — add real `error` / `stream_closed` / `bohrium_node` bridge mapping tests, not just orchestration patching
- [ ] `tests/matmaster/devshell/test_integration.py` — rewrite around the non-bus DevShell observer path; the current file is already red
- [ ] `tests/matmaster/devshell/test_compaction_via_devshell.py` — remove MessageBus assumptions or reattach compaction events to the new DevShell observer path
- [ ] `tests/matmaster/integration/test_pipeline_alignment.py` — rewrite/delete; current assertions assume dead bus delivery from `kernel.run()`
- [ ] `tests/matmaster/integration/test_e2e_minimal.py` — rewrite/delete; current assertions assume dead bus delivery from `kernel.run()`
- [ ] `tests/matmaster/core/test_bus.py` — delete with `matmaster/core/bus.py`

## Sources

### Primary (HIGH confidence)
- Local code: `src/services/agent_run_service.py` — current run paths, cleanup order, source normalization, worker callback contract
- Local code: `src/services/agent_run_bohrium.py` — thread-safe bridge shape and external event mapping
- Local code: `matmaster/integration/event_router.py` — current drain, dispatch, and close semantics that must survive deletion
- Local code: `matmaster/integration/sse_handler.py`, `matmaster/integration/persistence_handler.py`, `matmaster/integration/workspace_handler.py` — canonical consumer behaviors to preserve
- Local code: `matmaster/core/exp.py`, `matmaster/core/tool_runner.py`, `matmaster/core/tool_scheduler.py`, `matmaster/tools/tool_compiler.py` — current bus signatures, ToolRunner behavior, and scheduler reality
- Local code: `matmaster/devshell/runner.py`, `matmaster/devshell/repl.py`, `matmaster/devshell/cli.py`, `matmaster/devshell/event_logger.py`, `matmaster/devshell/stream_hook.py` — current DevShell threading/observer pattern
- Local docs: `.planning/phases/36-debus-scheduling/36-CONTEXT.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`
- Official docs: [Python `asyncio` tasks](https://docs.python.org/3/library/asyncio-task.html) — `create_task()` strong-reference guidance and `TaskGroup` semantics/version
- Official docs: [Python `asyncio` queues](https://docs.python.org/3/library/asyncio-queue.html) — `asyncio.Queue` is not thread-safe
- Official docs: [Python `queue` module](https://docs.python.org/3/library/queue.html) — thread-safe queue semantics for threaded producer/consumer flows
- Official docs: [What’s New in Python 3.11](https://docs.python.org/3/whatsnew/3.11.html) — `TaskGroup` introduction/recommendation

### Secondary (MEDIUM confidence)
- None

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - based on verified local modules plus official Python docs for task/queue semantics
- Architecture: HIGH - based on direct trace of actual service, Bohrium, handler, worker, and DevShell call paths
- Pitfalls: HIGH - grounded in current code plus reproduced failing stale bus-based tests

**Research date:** 2026-04-03
**Valid until:** 2026-04-17
