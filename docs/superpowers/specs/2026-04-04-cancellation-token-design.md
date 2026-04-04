# CancellationToken: Event-Driven Cancellation Design

## Problem

The stop/cancellation mechanism relies on polling throughout the stack and has inconsistent protocol compliance.

### Protocol Inconsistency

`RedisBackedStopEvent` (`src/worker/agent_worker.py:57-66`) only implements `is_set()`. Both `LocalSession._exec_bash_with_stop()` and `SshSession.exec_bash()` use `getattr` defensive checks to fall back to `time.sleep()` when `wait()` is unavailable. This means Worker mode does not crash, but silently degrades: `LocalSession` polls `is_set()` every 100ms (each call hitting Redis), and `SshSession` polls every 50ms. The formal protocol `StopEventLike` (`src/services/agent_run_service.py:99-106`) only declares `is_set()`, and the Session Protocol's type hint `stop_event: threading.Event | Any | None` masks the real requirement.

### Polling Throughout the Stack

Every layer relies on polling, even when the stop signal source is in-process:

| Location | Polling mechanism |
|----------|------------------|
| `RedisBackedStopEvent.is_set()` | Every call queries Redis |
| `LocalSession._exec_bash_with_stop()` | 100ms poll loop (`getattr`-guarded `wait()` or `time.sleep()`) |
| `SshSession.exec_bash()` | 50ms poll loop with `time.sleep()` |
| `agent.py _sleep_backoff_with_stop_async()` | Sliced `asyncio.sleep()` loop checking `is_set()` |
| `lazy_mcp.py _wait_for_stop()` | 500ms `asyncio.sleep()` loop checking `is_set()` |
| `monitor_job/_lifecycle.py _sleep_until_stop()` | `getattr`-guarded `is_set()` + `time.sleep()` loop |

### Reference: Claude Code's Approach

Claude Code uses `AbortController` / `AbortSignal` (JS web standard):
- Event-driven: `abort()` sets signal and fires listeners immediately
- Pre-execution check: tool runner checks `signal.aborted` before starting tools
- Process tree cleanup: `tree-kill` kills entire process tree on abort
- Hierarchical: child AbortControllers linked to parent

## Design

Introduce `CancellationToken` + `CancellationController` (inspired by C#/.NET's CancellationToken, adapted for Python's threading model). Push Redis polling to a single boundary daemon thread; all downstream code becomes event-driven.

### Scope

| Change | File | Description |
|--------|------|-------------|
| Core primitives | `matmaster/types/cancellation.py` (new) | `CancellationToken`, `CancellationController`, `CancelledError` |
| Session Protocol | `matmaster/types/session.py` | `stop_event` param → `cancel_token: CancellationToken \| None` |
| LocalSession | `matmaster/sessions/local.py` | Poll loop → `communicate()` + `on_cancel` callback + process group kill |
| SshSession | `matmaster/sessions/ssh.py` | `getattr` defense + `time.sleep` → `cancel_token.wait()` + `on_cancel` |
| Exp orchestrator | `matmaster/core/exp.py` | `stop_event` param → `cancel_token` in `run_stream()` signature; replace `catalog.inject_stop_event()` with `catalog.inject_cancel_token()` |
| Agent kernel | `matmaster/core/agent.py` | `stop_event` → `cancel_token`; delete `_sleep_backoff_with_stop_async` |
| Batch context | `matmaster/core/tool_runner.py` | `BatchExecutionContext.stop_event` → `.cancel_token`; alias `ToolExecutionContext` unchanged |
| Per-call context | `matmaster/types/tool_spec.py` | `ToolExecutionContext.stop_event` → `.cancel_token` |
| Tool catalog | `matmaster/tools/tool_catalog.py` | `inject_stop_event()` → `inject_cancel_token()` |
| Lazy MCP | `matmaster/tools/lazy_mcp.py` | Delete `_wait_for_stop`; use `cancel_token.wait_async()` in race |
| ToolResult status | `matmaster/tools/tool_result.py` | Add `"timeout"` status literal |
| Builtin base | `matmaster/tools/builtin/base.py` | `_stop_event_for_exec()` → `_cancel_token_for_exec()`; propagate to `exec_bash` |
| BashTool | `matmaster/tools/builtin/bash_tool.py` | `self._stop_event` → `self._cancel_token`; add cancel to `_execute_async` path |
| GlobTool | `matmaster/tools/builtin/glob_tool.py` | `stop_event=self._stop_event_for_exec()` → `cancel_token=self._cancel_token_for_exec()` |
| GrepTool | `matmaster/tools/builtin/grep_tool.py` | Same as GlobTool |
| ListDirTool | `matmaster/tools/builtin/listdir_tool.py` | Same as GlobTool |
| SpawnTool | `matmaster/tools/builtin/spawn_tool.py` | `self._stop_event` → `self._cancel_token`; use `controller.child().token` for child agent |
| Monitor job lifecycle | `matmaster/tools/builtin/monitor_job/_lifecycle.py` | Replace `getattr` defense; delete `_sleep_until_stop`, replace with `cancel_token.wait()` |
| Monitor job tool | `matmaster/tools/builtin/monitor_job/_tool.py` | `stop_ev = self._stop_event_for_exec()` → `cancel_token=self._cancel_token_for_exec()` |
| Redis bridge | `src/worker/agent_worker.py` | `RedisBackedStopEvent` → `RedisCancellationBridge` + `CancellationController` |
| Service layer | `src/services/agent_run_service.py` | Delete `StopEventLike`; pass `cancel_token` |
| Devshell runner | `matmaster/devshell/runner.py` | `stop_event` param → `cancel_token` |
| Devshell debug_run | `matmaster/devshell/debug_run.py` | `threading.Event()` → `CancellationController()` |
| Devshell repl | `matmaster/devshell/repl.py` | `threading.Event()` → `CancellationController()`; SIGINT handler calls `controller.cancel()` |

| Not changed | Reason |
|-------------|--------|
| `stop_mode` three-tier mechanism | Already well-designed (`cancellable` / `best_effort` / `non_cancellable`) |
| `ToolResult(status="cancelled")` paths | Preserved as-is |
| `_KernelStopRequested` exception | Internal control flow, name still accurate |
| `_STOP_CHECK_EVERY_N_STREAM_CHUNKS` | Throttling strategy preserved (now pure memory read, but throttle has no cost) |
| `src/services/stream_service.py` | Uses `threading.Event` for Redis pubsub SSE control, independent of agent cancel chain |

### Deleted code

| Location | What |
|----------|------|
| `src/services/agent_run_service.py` | `StopEventLike` Protocol class |
| `src/worker/agent_worker.py` | `RedisBackedStopEvent` class |
| `matmaster/core/agent.py` | `_sleep_backoff_with_stop_async()` static method |
| `matmaster/tools/lazy_mcp.py` | `_wait_for_stop()` module-level function |
| `matmaster/tools/builtin/monitor_job/_lifecycle.py` | `_sleep_until_stop()` function |

### 1. Core Primitives (`matmaster/types/cancellation.py`)

Two classes with separated responsibilities: Controller owns control, Token is the read-only signal passed downstream.

```python
class CancelledError(Exception):
    """Raised by CancellationToken.raise_if_cancelled()."""

class CancellationToken:
    """Read-only cancellation signal. Backed by threading.Event internally."""

    # --- Query ---
    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested. Pure memory read."""

    # --- Sync wait ---
    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled or timeout.
        Returns True if cancelled, False if timed out.
        Uses OS-level condition variable (threading.Event.wait), not polling."""

    # --- Async wait ---
    async def wait_async(self, timeout: float | None = None) -> bool:
        """Async-compatible wait. Uses asyncio.Future bridged via on_cancel callback.
        No executor thread, no polling. Safe to cancel via task.cancel()."""

    # --- Callback ---
    def on_cancel(self, callback: Callable[[], None]) -> None:
        """Register callback fired when cancel() is called.
        If already cancelled at registration time, callback fires immediately.
        Callbacks execute synchronously in the cancel() caller's thread.
        Fire-once guarantee: each callback executes at most once (see implementation notes)."""

    # --- Guard ---
    def raise_if_cancelled(self) -> None:
        """Raise CancelledError if cancelled."""


class CancellationController:
    """Holds control authority over a CancellationToken."""

    def __init__(self) -> None:
        self.token = CancellationToken()

    def cancel(self) -> None:
        """Trigger cancellation: set internal Event, fire all on_cancel callbacks."""

    def child(self) -> CancellationController:
        """Create child controller. Parent cancel cascades to child; child cancel does not affect parent."""
```

Design decisions:
- No `set()` / `clear()` on Token -- prevents downstream code from accidentally triggering or resetting the signal.
- `wait_async()` implementation uses `asyncio.Future` bridged via `on_cancel`, NOT `run_in_executor`. This is critical for correctness in race patterns (`asyncio.wait(FIRST_COMPLETED)`). See Section 1a for details.
- `on_cancel` fire-once guarantee: implementation must ensure each callback executes at most once despite concurrent `cancel()` and `on_cancel()` calls. Recommended approach: use a `threading.Lock` to guard the callback list and a `_fired` flag, so `cancel()` atomically drains the list and `on_cancel()` either appends (if not yet fired) or immediately invokes (if already fired).
- `child()` implemented via `self.token.on_cancel(child_controller.cancel)`.

#### 1a. `wait_async()` Implementation: Why Not `run_in_executor`

The naive implementation `await loop.run_in_executor(None, self._event.wait, timeout)` leaks threads in race patterns:

```python
# In lazy_mcp.py:
call_task = asyncio.create_task(call_coro)
stop_task = asyncio.create_task(cancel_token.wait_async())
done, pending = await asyncio.wait({call_task, stop_task}, FIRST_COMPLETED)
for task in pending:
    task.cancel()   # cancels the asyncio Task, but NOT the executor thread
```

When `call_task` finishes first (normal path), `stop_task.cancel()` cancels the asyncio Task but the underlying `threading.Event.wait()` keeps blocking in the executor thread until cancel or timeout. Each successful MCP tool call leaks one thread, eventually exhausting the default ThreadPoolExecutor.

Correct implementation uses `asyncio.Future` + `on_cancel` callback:

```python
async def wait_async(self, timeout: float | None = None) -> bool:
    if self._event.is_set():
        return True

    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def _resolve():
        def _safe_set():
            if not fut.done():
                fut.set_result(True)
        try:
            loop.call_soon_threadsafe(_safe_set)
        except RuntimeError:
            pass  # event loop closed

    self.on_cancel(_resolve)

    try:
        if timeout is not None:
            try:
                return await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                return False
        return await fut
    except asyncio.CancelledError:
        return self._event.is_set()
```

Properties:
- No executor thread occupied. Cancel resolves the future via `call_soon_threadsafe`.
- `task.cancel()` cleanly cancels the `await fut` (raises `CancelledError`), no resource leak.
- Timeout handled by `asyncio.wait_for`, standard asyncio machinery.
- A dangling callback remains in the on_cancel list if the token is never cancelled; this is a negligible memory cost cleaned up when the token is GC'd.

### 2. Redis Bridge (`src/worker/agent_worker.py`)

Replaces `RedisBackedStopEvent`. Isolates Redis polling to a single daemon thread.

```python
class RedisCancellationBridge:
    """Daemon thread that polls Redis stop flag and triggers CancellationController.

    Lifecycle: start() begins polling, stop() terminates it. Caller MUST
    call stop() in a finally block to prevent thread leaks on normal completion.
    """

    def __init__(
        self,
        controller: CancellationController,
        session_id: str,
        task_id: str,
        interval: float = 0.5,
    ) -> None: ...

    def start(self) -> None:
        """Start daemon polling thread."""

    def stop(self) -> None:
        """Signal the polling thread to exit and join it (idempotent)."""
```

Polling loop (daemon thread):
```python
# Internal _shutdown event ensures the thread exits on both cancel and normal completion.
while not self._shutdown.is_set() and not controller.token.is_cancelled:
    if dao.is_stop_requested(session_id, task_id):
        controller.cancel()
        break
    self._shutdown.wait(interval)   # wakes immediately on stop(), else sleeps 500ms
```

`stop()` sets `self._shutdown` and joins the thread:
```python
def stop(self) -> None:
    self._shutdown.set()
    if self._thread is not None:
        self._thread.join(timeout=2.0)
```

Worker entry point changes:
```python
# Before:
stop_event = RedisBackedStopEvent(session_id, task_id)
# ... pass stop_event through call chain ...

# After:
controller = CancellationController()
bridge = RedisCancellationBridge(controller, session_id, task_id)
bridge.start()
try:
    # ... pass controller.token through call chain ...
finally:
    bridge.stop()   # MUST be in finally to prevent thread leak on normal completion
```

SIGTERM handler also calls `controller.cancel()`, unifying the stop path.

Local debug mode (mm-devshell) uses `CancellationController` directly with `signal.signal(SIGINT, ...)`, no bridge needed.

### 3. Session Layer

#### LocalSession (`matmaster/sessions/local.py`)

Current `_exec_bash_with_stop()` (hand-written poll loop) replaced by callback + `communicate()`:

```python
def _exec_bash_with_token(self, command: str, timeout: int, cancel_token: CancellationToken) -> dict[str, Any]:
    if cancel_token.is_cancelled:
        return {"stdout": "", "stderr": "Cancelled by stop request",
                "exit_code": 130, "working_dir": str(self._workspace_path),
                "output": "Cancelled by stop request"}

    proc = subprocess.Popen(
        ["bash", "-c", command],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(self._workspace_path), text=True,
        start_new_session=True,                           # creates new process group (Python 3.10+)
    )

    def _kill_group():
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    cancel_token.on_cancel(_kill_group)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group()
        proc.wait()
        return {"stdout": "", "stderr": f"Command timeout after {timeout}s",
                "exit_code": 124, "working_dir": str(self._workspace_path),
                "output": f"Command timeout after {timeout}s"}

    if cancel_token.is_cancelled:
        return {"stdout": "", "stderr": "Cancelled by stop request",
                "exit_code": 130, "working_dir": str(self._workspace_path),
                "output": "Cancelled by stop request"}

    return {"stdout": stdout, "stderr": stderr,
            "exit_code": proc.returncode, "working_dir": str(self._workspace_path),
            "output": stdout or stderr}
```

Key improvements over current implementation:
- Pre-check: if already cancelled, skip process creation entirely. The window between pre-check and `Popen` is negligible; if cancel arrives during that window, `on_cancel` fires immediately at registration (fire-once guarantee ensures `_kill_group` runs exactly once).
- `proc.communicate(timeout)` replaces hand-written poll loop (kernel-level blocking, no CPU cost).
- `on_cancel` callback kills process immediately when cancellation fires.
- `start_new_session=True` + `os.killpg(os.getpgid(proc.pid), ...)` kills entire process tree (prevents leaked child processes from shell pipelines). Uses `start_new_session` instead of `process_group=0` for Python 3.10+ compatibility.
- `_kill_group` catches `ProcessLookupError` / `OSError` to handle the race where cancel and timeout fire near-simultaneously (process already exited).
- Timeout vs cancellation distinguished by `TimeoutExpired` exception vs `is_cancelled` check. Timeout takes precedence (local deterministic event vs external async event).

#### SshSession (`matmaster/sessions/ssh.py`)

Paramiko channels don't support event-driven recv, so the data-read polling loop stays. Cancellation detection becomes instant:

```python
def exec_bash(self, command: str, timeout: int | None = None,
              cancel_token: CancellationToken | None = None) -> dict[str, Any]:
    # ... setup channel, exec_command ...

    if cancel_token:
        cancel_token.on_cancel(channel.close)

    while not channel.exit_status_ready():
        if channel.recv_ready():
            stdout_chunks.append(channel.recv(65536))
        if channel.recv_stderr_ready():
            stderr_chunks.append(channel.recv_stderr(65536))

        if time.monotonic() >= deadline:
            channel.close()
            return {... "exit_code": -1, timeout message ...}

        if cancel_token and cancel_token.wait(0.05):   # instant return on cancel
            return {... "exit_code": 130, cancelled message ...}
        elif not cancel_token:
            time.sleep(0.05)

    # drain remaining data ...
```

Changes from current:
- `getattr(stop_event, "is_set", None)` defense removed (type system guarantees interface)
- `time.sleep(0.05)` replaced by `cancel_token.wait(0.05)` (wakes instantly on cancel)
- `on_cancel(channel.close)` registered as backup cleanup

### 4. Agent + Tool Runner Layer

#### 4a. Two ToolExecutionContext classes

The codebase has two context classes that both carry `stop_event`:

| Class | Location | Scope | Fields |
|-------|----------|-------|--------|
| `ToolExecutionContext` | `matmaster/types/tool_spec.py:84` | Per-call, passed to `tool_executor()` / `execute_with_context()` | `stop_event`, `on_progress`, `runner_state` |
| `BatchExecutionContext` | `matmaster/core/tool_runner.py:42` | Per-batch, used by `FullToolRunner.execute_batch()` | `turn`, `max_turns`, `stop_event`, `progress_sink` |

Note: `tool_runner.py:55` creates an alias `ToolExecutionContext = BatchExecutionContext` for backward compatibility. Both classes must be migrated: `stop_event` → `cancel_token` in each.

The per-call `ToolExecutionContext` (from `types/tool_spec.py`) is what `LazyMcpTool.tool_executor()`, `BuiltinTool.execute_with_context()`, and `BashTool.execute_with_context()` receive. Missing this would leave the tool-executor boundary on the old `stop_event` field while the batch layer sends `cancel_token`.

#### 4b. Propagation chain

```
Exp.run_stream(cancel_token=CancellationToken)
  → catalog.inject_cancel_token(cancel_token)    # monkey-patch to all tool instances
  → session._cancel_token = cancel_token          # session-level access for builtin tools
  → AgentKernel.run_stream(cancel_token)
    → _run_items(cancel_token)
      → _call_llm_streaming(cancel_token)
      → BatchExecutionContext(cancel_token=cancel_token)
        → FullToolRunner.execute_batch(ctx)
          → ToolExecutionContext(cancel_token=ctx.cancel_token)  # per-call
            → LazyMcpTool.tool_executor(exec_ctx)
            → BashTool.execute_with_context(exec_ctx)
            → BuiltinTool._execute() via session.exec_bash(cancel_token)
```

`ToolCatalog.inject_stop_event(stop_event)` renamed to `inject_cancel_token(cancel_token)`. The monkey-patch pattern is preserved (out of scope to refactor in this change).

`SpawnTool` creates child agents with `controller.child().token` instead of passing the same token, so parent cancel cascades to child but child cancel does not affect parent.

#### 4c. agent.py: cancellation checks

Mechanical rename, no logic change:

```python
# Turn-level check:
if cancel_token and cancel_token.is_cancelled:
    yield self._terminal(state, 'cancelled')

# Stream chunk throttled check:
if cancel_token and chunk_idx % _STOP_CHECK_EVERY_N_STREAM_CHUNKS == 0 and cancel_token.is_cancelled:
    stream_cancelled = True
    break
```

#### 4d. agent.py: backoff sleep (polling eliminated)

```python
# Before: 15-line polling loop
@staticmethod
async def _sleep_backoff_with_stop_async(seconds, stop_event):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_event.is_set():
            raise _KernelStopRequested()
        await asyncio.sleep(min(_SLICE, remaining))

# After: 2-line event-driven wait
@staticmethod
async def _sleep_backoff_with_cancel(seconds, cancel_token):
    if cancel_token and await cancel_token.wait_async(seconds):
        raise _KernelStopRequested()
```

#### 4e. lazy_mcp.py: delete `_wait_for_stop`, use `wait_async`

```python
# Before:
async def _wait_for_stop(stop_event):
    while not stop_event.is_set():
        await asyncio.sleep(0.5)

call_task = asyncio.create_task(call_coro)
stop_task = asyncio.create_task(_wait_for_stop(stop_event))

# After:
call_task = asyncio.create_task(call_coro)
stop_task = asyncio.create_task(cancel_token.wait_async())
```

Race structure (`asyncio.wait(FIRST_COMPLETED)`) unchanged. `_wait_for_stop` function deleted.

Because `wait_async()` uses `asyncio.Future` (not `run_in_executor`), `task.cancel()` on the losing task cleanly cancels the future with no thread leak. This is the key reason `run_in_executor` was rejected (see Section 1a).

#### 4f. BashTool `_execute_async` path

`BashTool._execute_async()` (`bash_tool.py:100`) uses `asyncio.create_subprocess_exec` directly, bypassing `session.exec_bash()` entirely. The current implementation has NO cancel handling -- it only handles timeout via `asyncio.wait_for`.

Add cancel support:

```python
async def _execute_async(self, arguments: dict[str, Any]) -> str:
    cancel_token = self._cancel_token   # injected via execute_with_context

    if cancel_token and cancel_token.is_cancelled:
        return "Command cancelled."

    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", command,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=wd,
    )

    if cancel_token:
        cancel_token.on_cancel(proc.kill)

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Command timeout after {timeout}s ..."

    if cancel_token and cancel_token.is_cancelled:
        return "Command cancelled."

    # ... format output as before ...
```

The pattern mirrors LocalSession: pre-check → `on_cancel(proc.kill)` → communicate → post-check.

#### 4g. monitor_job/_lifecycle.py

`_sleep_until_stop()` function deleted. All three call sites replaced with `cancel_token.wait(seconds)`:

```python
# Before:
if _sleep_until_stop(min(poll_interval, 10), stop_event):
    return {'status': 'cancelled', ...}

# After:
if cancel_token and cancel_token.wait(min(poll_interval, 10)):
    return {'status': 'cancelled', ...}
```

Direct `getattr` defense checks also replaced:

```python
# Before:
if stop_event and getattr(stop_event, 'is_set', None) and stop_event.is_set():

# After:
if cancel_token and cancel_token.is_cancelled:
```

### 5. ToolResult: Add `timeout` Status

Current status values: `success`, `error`, `cancelled`. Timeouts return `status="error"`, making them indistinguishable from real exceptions.

Add `"timeout"` as a fourth status literal. Affected return paths:

| Location | Current | After |
|----------|---------|-------|
| `lazy_mcp.py _timeout_result()` | `status="error"` | `status="timeout"` |
| `LocalSession` timeout path | `exit_code=124` (unchanged) | `exit_code=124` (no ToolResult change here; session returns dict, not ToolResult) |
| `SshSession` timeout path | `exit_code=-1` (unchanged) | `exit_code=-1` (same) |

Note: Session layer returns raw dicts, not ToolResult. The `timeout` status is relevant at the tool/runner level, not session level. The primary consumer is `lazy_mcp.py` where MCP tool timeouts currently produce `status="error"`.

Note: `ToolResult.status` is currently typed as `str`. Tightening it to `Literal["success", "error", "cancelled", "timeout", "blocked"]` is a worthwhile follow-up but out of scope for this change.

Downstream benefit: agent retry logic can differentiate -- retry with larger timeout for `timeout`, exponential backoff for `error`, no retry for `cancelled`.

## Test Plan

### Verification Matrix

| Scenario | Layer | What to verify |
|----------|-------|----------------|
| Normal completion, no cancel | Session, Bridge | Bridge thread exits cleanly (no thread leak); no dangling on_cancel callbacks affecting subsequent runs |
| Cancel during exec_bash | LocalSession | Process group killed immediately; `communicate()` returns; result has `exit_code=130` |
| Cancel during exec_bash | SshSession | `channel.close()` called; `wait(0.05)` returns True; result has `exit_code=130` |
| Cancel during `_execute_async` | BashTool | `proc.kill()` via on_cancel; result indicates cancellation |
| Cancel during MCP tool call | lazy_mcp.py | `wait_async()` resolves future; call_task cancelled; no thread leak |
| Cancel during LLM retry backoff | agent.py | `wait_async(seconds)` returns True; `_KernelStopRequested` raised |
| Timeout during MCP tool call | lazy_mcp.py | `ToolResult.status == "timeout"` (not `"error"`) |
| Cancel after normal completion | all | `on_cancel` callback fires but `_kill_group` / `proc.kill` gets `ProcessLookupError` → caught silently |
| Bridge normal shutdown | worker | `bridge.stop()` in finally → daemon thread joins within 2s |
| Bridge cancel shutdown | worker | Redis stop detected → `controller.cancel()` → bridge thread exits |
| SIGTERM during run | worker | Handler calls `controller.cancel()`; same cancel path as user stop |
| child() cascade | SpawnTool | Parent cancel → child cancelled; child cancel → parent unaffected |
| wait_async task.cancel() | lazy_mcp.py | Pending stop_task cancelled cleanly; no executor thread leaked; verify via `ThreadPoolExecutor._threads` count |

### Existing Test Files to Update

| File | Changes needed |
|------|---------------|
| `tests/matmaster/core/test_full_tool_runner.py` | `stop_event` → `cancel_token` in test fixtures |
| `tests/matmaster/tools/test_lazy_mcp.py` | Same; add thread-leak regression test for race pattern |
| `tests/matmaster/core/test_local_session_stop.py` | Rewrite with `CancellationController`; verify process group kill |
| `tests/matmaster/sessions/test_ssh_session.py` | Same; verify `wait(0.05)` instant return |

### New Tests

| Test | Purpose |
|------|---------|
| `tests/matmaster/types/test_cancellation.py` | Unit tests for CancellationToken/Controller: fire-once, child cascade, wait_async cancellation, concurrent on_cancel+cancel |
| `tests/matmaster/worker/test_redis_bridge.py` | Bridge lifecycle: start/stop/thread-join; mock Redis DAO |
