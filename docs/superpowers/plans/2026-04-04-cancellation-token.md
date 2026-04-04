# CancellationToken Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace polling-based `stop_event` with event-driven `CancellationToken` + `CancellationController` across the entire agent runtime.

**Architecture:** New `CancellationToken` backed by `threading.Event` + `asyncio.Future` bridge. Redis polling isolated to one boundary daemon thread. All downstream code is event-driven.

**Tech Stack:** Python 3.10+, asyncio, threading, pytest

**Spec:** `docs/superpowers/specs/2026-04-04-cancellation-token-design.md`

---

## Chunk 1: Core Primitives

### Task 1: CancellationToken + CancellationController

**Files:**
- Create: `matmaster/types/cancellation.py`
- Create: `tests/matmaster/types/test_cancellation.py`

- [ ] **Step 1: Write failing tests for CancellationToken basics**

```python
# tests/matmaster/types/test_cancellation.py
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from matmaster.types.cancellation import (
    CancelledError,
    CancellationController,
    CancellationToken,
)


class TestCancellationToken:
    def test_initial_state_not_cancelled(self):
        ctrl = CancellationController()
        assert ctrl.token.is_cancelled is False

    def test_cancel_sets_is_cancelled(self):
        ctrl = CancellationController()
        ctrl.cancel()
        assert ctrl.token.is_cancelled is True

    def test_wait_returns_true_when_cancelled(self):
        ctrl = CancellationController()
        ctrl.cancel()
        assert ctrl.token.wait(timeout=0.01) is True

    def test_wait_returns_false_on_timeout(self):
        ctrl = CancellationController()
        assert ctrl.token.wait(timeout=0.05) is False

    def test_wait_wakes_immediately_on_cancel(self):
        ctrl = CancellationController()
        t0 = time.monotonic()
        threading.Timer(0.1, ctrl.cancel).start()
        result = ctrl.token.wait(timeout=5.0)
        elapsed = time.monotonic() - t0
        assert result is True
        assert elapsed < 1.0  # should wake in ~100ms, not 5s

    def test_raise_if_cancelled(self):
        ctrl = CancellationController()
        ctrl.token.raise_if_cancelled()  # should not raise
        ctrl.cancel()
        with pytest.raises(CancelledError):
            ctrl.token.raise_if_cancelled()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/matmaster/types/test_cancellation.py -v`
Expected: ImportError (module does not exist yet)

- [ ] **Step 3: Implement CancellationToken and CancellationController**

```python
# matmaster/types/cancellation.py
"""Event-driven cancellation primitives.

CancellationController owns the cancel authority.
CancellationToken is the read-only signal passed to downstream consumers.

Inspired by C#/.NET CancellationToken and Claude Code's AbortController.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable


class CancelledError(Exception):
    """Raised by CancellationToken.raise_if_cancelled()."""


class CancellationToken:
    """Read-only cancellation signal backed by threading.Event."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._fired = False

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled or timeout. Returns True if cancelled."""
        return self._event.wait(timeout)

    async def wait_async(self, timeout: float | None = None) -> bool:
        """Async-compatible wait using asyncio.Future + on_cancel bridge.

        Safe to cancel via task.cancel() -- no executor thread leak.
        """
        if self._event.is_set():
            return True

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()

        def _resolve() -> None:
            def _safe_set() -> None:
                if not fut.done():
                    fut.set_result(True)
            try:
                loop.call_soon_threadsafe(_safe_set)
            except RuntimeError:
                pass  # event loop closed

        self.on_cancel(_resolve)

        if timeout is not None:
            try:
                return await asyncio.wait_for(asyncio.ensure_future(fut), timeout)
            except asyncio.TimeoutError:
                return False
        return await fut

    def on_cancel(self, callback: Callable[[], None]) -> None:
        """Register callback. Fires immediately if already cancelled.

        Fire-once guarantee: each callback executes at most once.
        """
        with self._lock:
            if self._fired:
                # Already cancelled -- fire immediately, outside lock
                pass
            else:
                self._callbacks.append(callback)
                return
        # Fired path: invoke outside lock to avoid deadlock
        callback()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError("Operation cancelled")

    def _fire_callbacks(self) -> None:
        """Called by CancellationController.cancel(). Drains and fires all callbacks."""
        with self._lock:
            self._fired = True
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for cb in callbacks:
            cb()


class CancellationController:
    """Holds control authority over a CancellationToken."""

    def __init__(self) -> None:
        self.token = CancellationToken()

    def cancel(self) -> None:
        """Trigger cancellation: set Event, fire all on_cancel callbacks."""
        self.token._event.set()
        self.token._fire_callbacks()

    def child(self) -> CancellationController:
        """Create child controller. Parent cancel cascades to child."""
        child_ctrl = CancellationController()
        self.token.on_cancel(child_ctrl.cancel)
        return child_ctrl
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/matmaster/types/test_cancellation.py -v`
Expected: 6 passed

- [ ] **Step 5: Write tests for on_cancel and child()**

Append to `tests/matmaster/types/test_cancellation.py`:

```python
class TestOnCancel:
    def test_callback_fires_on_cancel(self):
        ctrl = CancellationController()
        called = []
        ctrl.token.on_cancel(lambda: called.append(1))
        assert called == []
        ctrl.cancel()
        assert called == [1]

    def test_callback_fires_immediately_if_already_cancelled(self):
        ctrl = CancellationController()
        ctrl.cancel()
        called = []
        ctrl.token.on_cancel(lambda: called.append(1))
        assert called == [1]

    def test_callback_fires_at_most_once(self):
        ctrl = CancellationController()
        count = []
        ctrl.token.on_cancel(lambda: count.append(1))
        ctrl.cancel()
        ctrl.cancel()  # idempotent
        assert len(count) == 1

    def test_multiple_callbacks(self):
        ctrl = CancellationController()
        results = []
        ctrl.token.on_cancel(lambda: results.append("a"))
        ctrl.token.on_cancel(lambda: results.append("b"))
        ctrl.cancel()
        assert results == ["a", "b"]


class TestChild:
    def test_parent_cancel_cascades_to_child(self):
        parent = CancellationController()
        child = parent.child()
        assert child.token.is_cancelled is False
        parent.cancel()
        assert child.token.is_cancelled is True

    def test_child_cancel_does_not_affect_parent(self):
        parent = CancellationController()
        child = parent.child()
        child.cancel()
        assert child.token.is_cancelled is True
        assert parent.token.is_cancelled is False
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `uv run pytest tests/matmaster/types/test_cancellation.py -v`
Expected: 11 passed

- [ ] **Step 7: Write tests for wait_async**

Append to `tests/matmaster/types/test_cancellation.py`:

```python
class TestWaitAsync:
    @pytest.mark.asyncio
    async def test_returns_true_when_already_cancelled(self):
        ctrl = CancellationController()
        ctrl.cancel()
        assert await ctrl.token.wait_async(timeout=0.01) is True

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self):
        ctrl = CancellationController()
        assert await ctrl.token.wait_async(timeout=0.05) is False

    @pytest.mark.asyncio
    async def test_resolves_on_cancel_from_another_thread(self):
        ctrl = CancellationController()
        threading.Timer(0.1, ctrl.cancel).start()
        t0 = time.monotonic()
        result = await ctrl.token.wait_async(timeout=5.0)
        elapsed = time.monotonic() - t0
        assert result is True
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_task_cancel_does_not_leak_thread(self):
        """Cancelling the asyncio task should not leak executor threads."""
        ctrl = CancellationController()
        task = asyncio.create_task(ctrl.token.wait_async())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_race_pattern_no_thread_leak(self):
        """Simulates the lazy_mcp race: call_task wins, stop_task cancelled cleanly."""
        ctrl = CancellationController()

        async def fast_work():
            await asyncio.sleep(0.05)
            return "done"

        call_task = asyncio.create_task(fast_work())
        stop_task = asyncio.create_task(ctrl.token.wait_async())
        done, pending = await asyncio.wait(
            {call_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        assert call_task in done
        assert call_task.result() == "done"

    @pytest.mark.asyncio
    async def test_token_cancel_after_waiter_timeout_no_invalid_state(self):
        """Regression: token cancel after wait_async timeout must not raise InvalidStateError."""
        ctrl = CancellationController()
        result = await ctrl.token.wait_async(timeout=0.05)
        assert result is False
        # Now cancel AFTER the future is already done/timed-out
        ctrl.cancel()  # should not raise InvalidStateError

    @pytest.mark.asyncio
    async def test_token_cancel_after_task_cancel_no_invalid_state(self):
        """Regression: token cancel after task.cancel() must not raise InvalidStateError."""
        ctrl = CancellationController()
        task = asyncio.create_task(ctrl.token.wait_async())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Now cancel AFTER the task was already cancelled
        ctrl.cancel()  # should not raise InvalidStateError
```

- [ ] **Step 8: Run tests, verify they pass**

Run: `uv run pytest tests/matmaster/types/test_cancellation.py -v`
Expected: 18 passed

- [ ] **Step 9: Commit**

```bash
git add matmaster/types/cancellation.py tests/matmaster/types/test_cancellation.py
git commit -m "feat: add CancellationToken and CancellationController primitives"
```

---

## Chunk 2: Type Layer + Redis Bridge

### Task 2: Update type definitions

**Files:**
- Modify: `matmaster/types/tool_spec.py:92` (`stop_event` → `cancel_token`)
- Modify: `matmaster/core/tool_runner.py:50` (`BatchExecutionContext.stop_event` → `.cancel_token`)
- Modify: `matmaster/types/session.py:98` (Session Protocol `stop_event` → `cancel_token`)
- Modify: `matmaster/tools/tool_result.py` (add `timeout` to docstring/comment)

- [ ] **Step 1: Update ToolExecutionContext (per-call)**

In `matmaster/types/tool_spec.py:92`:
```python
# Before:
    stop_event: threading.Event | None = None
# After:
    cancel_token: CancellationToken | None = None
```

Add import at top of file:
```python
from matmaster.types.cancellation import CancellationToken
```

Remove `import threading` if no longer used.

- [ ] **Step 2: Update BatchExecutionContext (per-batch)**

In `matmaster/core/tool_runner.py:41-51`:
```python
# Before:
@dataclass
class BatchExecutionContext:
    turn: int
    max_turns: int
    stop_event: threading.Event | None = None
    progress_sink: Callable[[str, str, str], Awaitable[None]] | None = None

# After:
@dataclass
class BatchExecutionContext:
    turn: int
    max_turns: int
    cancel_token: CancellationToken | None = None
    progress_sink: Callable[[str, str, str], Awaitable[None]] | None = None
```

Add import: `from matmaster.types.cancellation import CancellationToken`

- [ ] **Step 3: Update Session Protocol**

In `matmaster/types/session.py:94-101`:
```python
# Before:
    def exec_bash(
        self,
        command: str,
        timeout: int | None = None,
        stop_event: threading.Event | Any | None = None,
    ) -> dict[str, Any]:

# After:
    def exec_bash(
        self,
        command: str,
        timeout: int | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> dict[str, Any]:
```

Add import: `from matmaster.types.cancellation import CancellationToken`
Remove `import threading` and `Any` import if no longer used elsewhere.

- [ ] **Step 4: Delete StopEventLike**

In `src/services/agent_run_service.py:99-106`, delete the `StopEventLike` Protocol class and its `@runtime_checkable` decorator.

- [ ] **Step 5: Run type-related tests**

Run: `uv run pytest tests/matmaster/types/ -v --timeout=30`
Expected: some tests may fail due to downstream `stop_event` references -- that's expected, we fix consumers in later tasks.

- [ ] **Step 6: Commit**

```bash
git add matmaster/types/tool_spec.py matmaster/core/tool_runner.py matmaster/types/session.py src/services/agent_run_service.py
git commit -m "refactor: rename stop_event to cancel_token in type definitions"
```

### Task 3: RedisCancellationBridge

**Files:**
- Modify: `src/worker/agent_worker.py:57-66` (replace `RedisBackedStopEvent`)
- Create: `tests/matmaster/worker/test_redis_bridge.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/matmaster/worker/test_redis_bridge.py
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest


class TestRedisCancellationBridge:
    def test_bridge_detects_stop_and_cancels(self):
        from matmaster.types.cancellation import CancellationController
        from src.worker.agent_worker import RedisCancellationBridge

        ctrl = CancellationController()
        # Mock DAO that returns True on 2nd call
        mock_dao = MagicMock()
        mock_dao.is_stop_requested.side_effect = [False, True]

        bridge = RedisCancellationBridge(
            ctrl, "sid", "tid", interval=0.05, _dao_override=mock_dao
        )
        bridge.start()
        time.sleep(0.3)
        bridge.stop()

        assert ctrl.token.is_cancelled is True

    def test_bridge_stops_cleanly_on_normal_completion(self):
        from matmaster.types.cancellation import CancellationController
        from src.worker.agent_worker import RedisCancellationBridge

        ctrl = CancellationController()
        mock_dao = MagicMock()
        mock_dao.is_stop_requested.return_value = False

        bridge = RedisCancellationBridge(
            ctrl, "sid", "tid", interval=0.05, _dao_override=mock_dao
        )
        bridge.start()
        time.sleep(0.1)
        bridge.stop()

        # Thread should be joined, no leak
        assert not bridge._thread.is_alive()
        # Controller NOT cancelled (normal completion)
        assert ctrl.token.is_cancelled is False

    def test_bridge_stop_is_idempotent(self):
        from matmaster.types.cancellation import CancellationController
        from src.worker.agent_worker import RedisCancellationBridge

        ctrl = CancellationController()
        mock_dao = MagicMock()
        mock_dao.is_stop_requested.return_value = False

        bridge = RedisCancellationBridge(
            ctrl, "sid", "tid", interval=0.05, _dao_override=mock_dao
        )
        bridge.start()
        bridge.stop()
        bridge.stop()  # should not raise
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/matmaster/worker/test_redis_bridge.py -v`
Expected: ImportError

- [ ] **Step 3: Implement RedisCancellationBridge**

In `src/worker/agent_worker.py`, replace `RedisBackedStopEvent` (lines 57-66):

```python
class RedisCancellationBridge:
    """Daemon thread that polls Redis stop flag and triggers CancellationController.

    Lifecycle: start() begins polling, stop() terminates it.
    Caller MUST call stop() in a finally block.
    """

    def __init__(
        self,
        controller: CancellationController,
        session_id: str,
        task_id: str,
        interval: float = 0.5,
        *,
        _dao_override: Any = None,
    ) -> None:
        self._controller = controller
        self._session_id = session_id
        self._task_id = task_id
        self._interval = interval
        self._dao = _dao_override or get_redis_dao()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _poll(self) -> None:
        while not self._shutdown.is_set() and not self._controller.token.is_cancelled:
            if self._dao.is_stop_requested(self._session_id, self._task_id):
                self._controller.cancel()
                break
            self._shutdown.wait(self._interval)
```

Add import at top: `from matmaster.types.cancellation import CancellationController`

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/matmaster/worker/test_redis_bridge.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/worker/agent_worker.py tests/matmaster/worker/test_redis_bridge.py
git commit -m "feat: add RedisCancellationBridge, replace RedisBackedStopEvent"
```

---

## Chunk 3: Session Layer

### Task 4: LocalSession

**Files:**
- Modify: `matmaster/sessions/local.py:60-146`
- Modify: `tests/matmaster/core/test_local_session_stop.py`

- [ ] **Step 1: Rewrite test with CancellationController**

```python
# tests/matmaster/core/test_local_session_stop.py
from __future__ import annotations

import shlex
import sys
import threading
import time

from matmaster.sessions.local import LocalSession
from matmaster.types.cancellation import CancellationController


def test_local_session_exec_bash_honors_cancel_token(tmp_path) -> None:
    session = LocalSession(str(tmp_path), timeout=15)
    session.open()
    ctrl = CancellationController()
    threading.Timer(0.5, ctrl.cancel).start()
    started_at = time.time()

    try:
        cmd = f'{shlex.quote(sys.executable)} -c "import time; time.sleep(10)"'
        result = session.exec_bash(cmd, timeout=15, cancel_token=ctrl.token)
    finally:
        session.close()

    assert result['exit_code'] == 130
    assert time.time() - started_at < 3.0  # should cancel in ~0.5s, not 10s


def test_local_session_pre_check_skips_if_already_cancelled(tmp_path) -> None:
    session = LocalSession(str(tmp_path), timeout=15)
    session.open()
    ctrl = CancellationController()
    ctrl.cancel()

    try:
        result = session.exec_bash("echo hello", timeout=5, cancel_token=ctrl.token)
    finally:
        session.close()

    assert result['exit_code'] == 130
    assert "Cancelled" in result['stderr']


def test_local_session_timeout_still_works(tmp_path) -> None:
    session = LocalSession(str(tmp_path), timeout=15)
    session.open()

    try:
        cmd = f'{shlex.quote(sys.executable)} -c "import time; time.sleep(10)"'
        result = session.exec_bash(cmd, timeout=1)
    finally:
        session.close()

    assert result['exit_code'] == 124
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/matmaster/core/test_local_session_stop.py -v`
Expected: FAIL (exec_bash doesn't accept `cancel_token` yet)

- [ ] **Step 3: Update LocalSession.exec_bash**

In `matmaster/sessions/local.py`, update `exec_bash` signature and add `_exec_bash_with_token`:

Change the `exec_bash` method's `stop_event` parameter to `cancel_token: CancellationToken | None = None`. Route to `_exec_bash_with_token` when `cancel_token is not None`.

Replace `_exec_bash_with_stop` (lines 99-146) with `_exec_bash_with_token` using the design from spec Section 3:
- Pre-check `cancel_token.is_cancelled`
- `subprocess.Popen(..., start_new_session=True)`
- `cancel_token.on_cancel(_kill_group)` with `ProcessLookupError` guard
- `proc.communicate(timeout=timeout)`
- `TimeoutExpired` → exit_code 124
- `is_cancelled` → exit_code 130

Add imports: `import os`, `import signal`, `from matmaster.types.cancellation import CancellationToken`

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/matmaster/core/test_local_session_stop.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add matmaster/sessions/local.py tests/matmaster/core/test_local_session_stop.py
git commit -m "refactor: LocalSession uses CancellationToken with communicate() + on_cancel"
```

### Task 5: SshSession

**Files:**
- Modify: `matmaster/sessions/ssh.py:134-204`
- Modify: `tests/matmaster/sessions/test_ssh_session.py`

- [ ] **Step 1: Update SshSession.exec_bash signature**

In `matmaster/sessions/ssh.py:134-139`, change:
```python
# Before:
    stop_event: threading.Event | Any | None = None,
# After:
    cancel_token: CancellationToken | None = None,
```

Add import: `from matmaster.types.cancellation import CancellationToken`

- [ ] **Step 2: Update exec_bash body**

Replace the stop_event handling in the `while not channel.exit_status_ready()` loop (lines 161-187):

- Add `cancel_token.on_cancel(channel.close)` after `channel.exec_command(wrapped)` (line 155).
- Replace lines 176-187 (the `getattr` defensive check + `time.sleep`):
```python
        if cancel_token and cancel_token.wait(0.05):
            out = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            return {
                "stdout": out,
                "stderr": "Command cancelled.",
                "exit_code": 130,
                "working_dir": self._workdir,
                "output": out + "\nCommand cancelled.",
            }
        elif not cancel_token:
            time.sleep(0.05)
```

- [ ] **Step 3: Update existing SSH tests**

In `tests/matmaster/sessions/test_ssh_session.py`, update any test fixtures that pass `stop_event` to use `cancel_token` instead. If no stop-related tests exist, add a basic one.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/matmaster/sessions/test_ssh_session.py -v --timeout=30`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/sessions/ssh.py tests/matmaster/sessions/test_ssh_session.py
git commit -m "refactor: SshSession uses CancellationToken with wait() + on_cancel"
```

---

## Chunk 4: Agent Kernel + Tool Runner + Tool Catalog

### Task 6: ToolCatalog

**Files:**
- Modify: `matmaster/tools/tool_catalog.py:123-125`

- [ ] **Step 1: Rename inject_stop_event → inject_cancel_token**

```python
# Before:
    def inject_stop_event(self, stop_event: threading.Event) -> None:
        for tool in self._registry.all_tools:
            tool._stop_event = stop_event

# After:
    def inject_cancel_token(self, cancel_token: CancellationToken) -> None:
        for tool in self._registry.all_tools:
            tool._cancel_token = cancel_token  # type: ignore[attr-defined]
```

Add import: `from matmaster.types.cancellation import CancellationToken`
Remove `import threading` if unused.

- [ ] **Step 2: Update test_tool_catalog.py**

If `inject_stop_event` is referenced in tests, rename to `inject_cancel_token`.

- [ ] **Step 3: Commit**

```bash
git add matmaster/tools/tool_catalog.py tests/matmaster/tools/test_tool_catalog.py
git commit -m "refactor: ToolCatalog.inject_stop_event → inject_cancel_token"
```

### Task 7: FullToolRunner pre-check

**Files:**
- Modify: `matmaster/core/tool_runner.py:190-205` (cancel check)
- Modify: `matmaster/core/tool_runner.py:276-281` (per-call context construction)

- [ ] **Step 1: Update cancel pre-check**

At line 190-191:
```python
# Before:
            if ctx.stop_event is not None and ctx.stop_event.is_set():
# After:
            if ctx.cancel_token is not None and ctx.cancel_token.is_cancelled:
```

- [ ] **Step 2: Update per-call context construction**

At line 278:
```python
# Before:
            exec_ctx = _ExecCtx(
                stop_event=ctx.stop_event,
                runner_state=self._state,
            )
# After:
            exec_ctx = _ExecCtx(
                cancel_token=ctx.cancel_token,
                runner_state=self._state,
            )
```

- [ ] **Step 3: Run tool runner tests**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner.py -v --timeout=30`

- [ ] **Step 4: Commit**

```bash
git add matmaster/core/tool_runner.py
git commit -m "refactor: FullToolRunner uses cancel_token for pre-check and context"
```

### Task 8: Agent Kernel

**Files:**
- Modify: `matmaster/core/agent.py` (all `stop_event` references)

- [ ] **Step 1: Update run_stream signature and all internal references**

In `matmaster/core/agent.py`, perform the following mechanical replacements:

1. `stop_event: threading.Event | None = None` → `cancel_token: CancellationToken | None = None` (lines 110, 210, 427, 510, 534)
2. `stop_event` → `cancel_token` in all passing positions (lines 131, 321, 387, 447, 466, 492)
3. `stop_event.is_set()` → `cancel_token.is_cancelled` (lines 255, 439, 520, 571-572)
4. Add import: `from matmaster.types.cancellation import CancellationToken`
5. Remove `import threading` if unused.

- [ ] **Step 2: Replace _sleep_backoff_with_stop_async**

Delete the existing method (lines 507-525) and replace with:

```python
    @staticmethod
    async def _sleep_backoff_with_cancel(
        seconds: float,
        cancel_token: CancellationToken | None,
    ) -> None:
        """Async sleep for *seconds*, wake early if cancelled."""
        if seconds <= 0:
            return
        if not cancel_token:
            await asyncio.sleep(seconds)
            return
        if await cancel_token.wait_async(seconds):
            raise _KernelStopRequested()
```

Update the two call sites (lines 466, 492) to call `_sleep_backoff_with_cancel` instead of `_sleep_backoff_with_stop_async`.

- [ ] **Step 3: Run agent kernel tests**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py -v --timeout=60`

- [ ] **Step 4: Commit**

```bash
git add matmaster/core/agent.py
git commit -m "refactor: AgentKernel uses CancellationToken, eliminate backoff polling"
```

---

## Chunk 5: Tools Migration

### Task 9: Builtin base + shell tools

**Files:**
- Modify: `matmaster/tools/builtin/base.py:112-119`
- Modify: `matmaster/tools/builtin/bash_tool.py:86-98, 100-150`
- Modify: `matmaster/tools/builtin/glob_tool.py`
- Modify: `matmaster/tools/builtin/grep_tool.py`
- Modify: `matmaster/tools/builtin/listdir_tool.py`

- [ ] **Step 1: Update BuiltinTool base**

In `matmaster/tools/builtin/base.py:112-119`:
```python
# Before:
    def _stop_event_for_exec(self) -> Any:
        ev = getattr(self, '_stop_event', None)
        if ev is not None:
            return ev
        if self._session is not None:
            return getattr(self._session, '_stop_event', None)
        return None

# After:
    def _cancel_token_for_exec(self) -> CancellationToken | None:
        ct = getattr(self, '_cancel_token', None)
        if ct is not None:
            return ct
        if self._session is not None:
            return getattr(self._session, '_cancel_token', None)
        return None
```

Add import: `from matmaster.types.cancellation import CancellationToken`

- [ ] **Step 2: Update BashTool.execute_with_context**

In `matmaster/tools/builtin/bash_tool.py:86-98`:
```python
# Before:
        if exec_ctx is not None and hasattr(exec_ctx, "stop_event"):
            self._stop_event = exec_ctx.stop_event

# After:
        if exec_ctx is not None and hasattr(exec_ctx, "cancel_token"):
            self._cancel_token = exec_ctx.cancel_token
```

- [ ] **Step 3: Add cancel support to BashTool._execute_async**

In `matmaster/tools/builtin/bash_tool.py:100-150`, update `_execute_async`:

Add `start_new_session=True` to `asyncio.create_subprocess_exec(...)` (line 116-123) to create a new process group, matching LocalSession's behavior.

After the `create_subprocess_exec` call, add:
```python
        cancel_token = getattr(self, '_cancel_token', None)

        def _kill_group():
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

        if cancel_token and cancel_token.is_cancelled:
            _kill_group()
            await proc.wait()
            obs = "Command cancelled."
            if wd:
                obs += f"\n[Current working directory: {wd}]"
            obs += "\n[Command finished with exit code 130]"
            return obs

        if cancel_token:
            cancel_token.on_cancel(_kill_group)
```

Add imports at top of file: `import os`, `import signal` (if not already present).

After `await asyncio.wait_for(proc.communicate(), ...)` returns (before formatting), add:
```python
        if cancel_token and cancel_token.is_cancelled:
            obs = "Command cancelled."
            if wd:
                obs += f"\n[Current working directory: {wd}]"
            obs += "\n[Command finished with exit code 130]"
            return obs
```

- [ ] **Step 4: Update BashTool._execute**

In line 166:
```python
# Before:
            stop_event=self._stop_event_for_exec(),
# After:
            cancel_token=self._cancel_token_for_exec(),
```

- [ ] **Step 5: Update GlobTool, GrepTool, ListDirTool**

In each tool, find `stop_event=self._stop_event_for_exec()` and replace with `cancel_token=self._cancel_token_for_exec()`.

- `matmaster/tools/builtin/glob_tool.py:93`
- `matmaster/tools/builtin/grep_tool.py:100`
- `matmaster/tools/builtin/listdir_tool.py` (find the `exec_bash` call)

- [ ] **Step 6: Run builtin tool tests**

Run: `uv run pytest tests/matmaster/tools/test_bash_tool.py tests/matmaster/tools/test_glob_tool.py tests/matmaster/tools/test_grep_tool.py tests/matmaster/tools/test_listdir_tool.py tests/matmaster/tools/test_builtin_base.py -v --timeout=30`

- [ ] **Step 7: Commit**

```bash
git add matmaster/tools/builtin/base.py matmaster/tools/builtin/bash_tool.py matmaster/tools/builtin/glob_tool.py matmaster/tools/builtin/grep_tool.py matmaster/tools/builtin/listdir_tool.py
git commit -m "refactor: builtin tools use cancel_token; BashTool._execute_async gets cancel support"
```

### Task 10: LazyMcpTool

**Files:**
- Modify: `matmaster/tools/lazy_mcp.py`

- [ ] **Step 1: Delete _wait_for_stop and update tool_executor**

1. Delete `_wait_for_stop` function (lines 37-40).
2. In `tool_executor` (line 185+), replace all `stop_event` references with `cancel_token`:
   - Line 189: `cancel_token = getattr(exec_ctx, "cancel_token", None) if exec_ctx else None`
   - Line 191: `if cancel_token is not None and cancel_token.is_cancelled:`
   - Line 203: `stop_task = asyncio.create_task(cancel_token.wait_async())`
3. In `_timeout_result()`, change `status="error"` to `status="timeout"`.
4. In `tests/matmaster/tools/test_lazy_mcp.py`, update `test_timeout_fires_on_hung_server`:
   `assert result.status == "error"` → `assert result.status == "timeout"`
5. In `matmaster/devshell/stream_hook.py:60`, update error detection to include timeout:
   `is_error = event.status == "error"` → `is_error = event.status in ("error", "timeout")`

- [ ] **Step 2: Run lazy MCP tests**

Run: `uv run pytest tests/matmaster/tools/test_lazy_mcp.py -v --timeout=30`

- [ ] **Step 3: Commit**

```bash
git add matmaster/tools/lazy_mcp.py tests/matmaster/tools/test_lazy_mcp.py matmaster/devshell/stream_hook.py
git commit -m "refactor: LazyMcpTool uses cancel_token.wait_async(), delete _wait_for_stop; timeout status"
```

### Task 11: SpawnTool + Monitor Job

**Files:**
- Modify: `matmaster/tools/builtin/spawn_tool.py:79, 148`
- Modify: `matmaster/tools/builtin/monitor_job/_lifecycle.py`
- Modify: `matmaster/tools/builtin/monitor_job/_tool.py`

- [ ] **Step 1: Update SpawnTool**

In `matmaster/tools/builtin/spawn_tool.py`:
- Line 79: `self._stop_event` → `self._cancel_token`
- Line 148: `self._stop_event` → `self._cancel_token`

- [ ] **Step 2: Update monitor_job/_lifecycle.py**

1. Delete `_sleep_until_stop` function (line 49+).
2. Replace three call sites (lines 273, 301, 315). Must include `else: time.sleep()` fallback to prevent tight polling when no token is present:
```python
# Before:
            if _sleep_until_stop(min(poll_interval, 10), stop_event):
                return {'status': 'cancelled', ...}

# After:
            sleep_secs = min(poll_interval, 10)
            if cancel_token:
                if cancel_token.wait(sleep_secs):
                    return {'status': 'cancelled', ...}
            else:
                time.sleep(sleep_secs)
```
3. Replace `getattr` defense checks:
```python
# Before:
        if stop_event and getattr(stop_event, 'is_set', None) and stop_event.is_set():
# After:
        if cancel_token and cancel_token.is_cancelled:
```
4. Update function signature: `stop_event` → `cancel_token: CancellationToken | None` in the lifecycle function that receives it.

- [ ] **Step 3: Update monitor_job/_tool.py**

Replace `stop_ev = self._stop_event_for_exec()` with `cancel_token=self._cancel_token_for_exec()` and pass as `cancel_token` to the lifecycle function.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/matmaster/tools/test_spawn_tool.py tests/matmaster/tools/test_monitor_job.py -v --timeout=30`

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/spawn_tool.py matmaster/tools/builtin/monitor_job/
git commit -m "refactor: SpawnTool and MonitorJob use cancel_token"
```

---

## Chunk 6: Entry Points + Cleanup

### Task 12: Exp orchestrator

**Files:**
- Modify: `matmaster/core/exp.py:108-110, 390-424`

- [ ] **Step 1: Update run_stream and spawn signatures**

1. `Exp.run_stream()` (line 396): `stop_event: threading.Event | None = None` → `cancel_token: CancellationToken | None = None`
2. Line 415: `ctx.session._stop_event = stop_event` → `ctx.session._cancel_token = cancel_token`
3. Lines 419-420: `catalog.inject_stop_event(stop_event)` → `catalog.inject_cancel_token(cancel_token)`
4. Line 423: `stop_event=stop_event` → `cancel_token=cancel_token`
5. Spawn helper (line 110): `stop_event: threading.Event | None = None` → `cancel_token: CancellationToken | None = None`; update the pass-through at line 135.

Add import: `from matmaster.types.cancellation import CancellationToken`

- [ ] **Step 2: Run exp tests**

Run: `uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/core/test_exp_runtime_v2.py -v --timeout=60`

- [ ] **Step 3: Commit**

```bash
git add matmaster/core/exp.py
git commit -m "refactor: Exp.run_stream uses cancel_token"
```

### Task 13: Worker entry point + SIGTERM unification

**Files:**
- Modify: `src/worker/agent_worker.py:42-44, 196-259, 457-471`

- [ ] **Step 1: Add module-level `_active_controller`**

At `src/worker/agent_worker.py`, near existing module-level variables (line 42-44):
```python
# Before:
_drain_requested = False

# After:
_drain_requested = False
_active_controller: CancellationController | None = None
```

- [ ] **Step 2: Replace stop_ev creation and register controller**

At line 196, in the per-run function:
```python
# Before:
        stop_ev = RedisBackedStopEvent(session_id, task_id)

# After:
        global _active_controller
        controller = CancellationController()
        _active_controller = controller
        bridge = RedisCancellationBridge(controller, session_id, task_id)
        bridge.start()
```

Add to the existing `finally` block:
```python
        finally:
            bridge.stop()
            _active_controller = None
```

At line 256:
```python
# Before:
                        stop_event=stop_ev,
# After:
                        cancel_token=controller.token,
```

- [ ] **Step 3: SIGTERM handler: drain only, do NOT cancel**

The existing `_on_sigterm` stays as-is (drain + publish_run_interrupted_deploy). It does NOT call `controller.cancel()`. Rationale:

- SIGTERM is a deploy signal: "finish current work gracefully, then exit." It pushes a deploy-specific interruption event to the frontend ("任务因服务升级中断，请重新发送").
- User stop is a cancel signal: "abort immediately." Detected by Redis bridge, triggers `controller.cancel()`, agent exits with reason `cancelled`.
- If SIGTERM also cancelled, the agent would emit both `run_interrupted(deploy)` AND `CancelledEvent(reason='Task cancelled by user.')`, giving the frontend conflicting end reasons.

The `_active_controller` variable is still needed for the bridge `finally` cleanup, but SIGTERM does not touch it. K8s SIGKILL after grace period is the hard stop if the run doesn't finish in time.

- [ ] **Step 4: Commit**

```bash
git add src/worker/agent_worker.py
git commit -m "refactor: Worker uses RedisCancellationBridge; SIGTERM cancels active run"
```

### Task 14: Service layer

**Files:**
- Modify: `src/services/agent_run_service.py:138-141, 296-320`

- [ ] **Step 1: Update run_agent signature and body**

1. Line 140: `stop_event: StopEventLike` → `cancel_token: CancellationToken`
2. Line 298: `ctx.session._stop_event = stop_event` → `ctx.session._cancel_token = cancel_token`
3. Line 319: `stop_event=stop_event` → `cancel_token=cancel_token`

Add import: `from matmaster.types.cancellation import CancellationToken`
Remove `StopEventLike` import.

- [ ] **Step 2: Commit**

```bash
git add src/services/agent_run_service.py
git commit -m "refactor: AgentRunService uses cancel_token, delete StopEventLike"
```

### Task 15: Devshell entry points + cancel_token injection

**Files:**
- Modify: `matmaster/devshell/runner.py:111-151`
- Modify: `matmaster/devshell/debug_run.py:85-88`
- Modify: `matmaster/devshell/repl.py:131-159`

- [ ] **Step 1: Update runner.py signature and add inject logic**

Line 115: `stop_event: threading.Event | None = None` → `cancel_token: CancellationToken | None = None`

The devshell runner calls `runtime.kernel.run_stream()` directly (line 143), bypassing `Exp.run_stream()`. This means the inject logic that `Exp.run_stream()` does (session._cancel_token, catalog.inject_cancel_token) never executes. Without this, builtin tools (glob, grep, list_dir, monitor_job, spawn) won't receive the cancel_token.

Add inject before `kernel.run_stream()` (after `runtime = await exp.build_runtime(...)`, before `drain_run_stream`):

```python
            runtime = await exp.build_runtime(self._pg_ctx)
            spec = runtime.spec

            # Inject cancel_token into tools and session (Exp.run_stream does
            # this for the worker path; devshell runner bypasses Exp.run_stream
            # so must do it here).
            if cancel_token is not None:
                catalog = getattr(spec, "tool_catalog", None)
                if catalog is not None:
                    catalog.inject_cancel_token(cancel_token)
                session = self._pg_ctx.session
                if session is not None:
                    session._cancel_token = cancel_token

            return await drain_run_stream(
                runtime.kernel.run_stream(
                    spec, task, history=self.history, cancel_token=cancel_token
                ),
                on_event=_on_event,
            )
```

- [ ] **Step 2: Update debug_run.py**

Line 85: `stop_event = threading.Event()` → `controller = CancellationController()`
Line 88: `stop_event=stop_event` → `cancel_token=controller.token`

- [ ] **Step 3: Update repl.py**

Line 132: `stop_event = threading.Event()` → `controller = CancellationController()`
Lines 140, 153: `ev: threading.Event = stop_event` → `ct: CancellationToken = controller.token`
Line 159: `stop_event=ev` → `cancel_token=ct`
SIGINT handler: change `ev.set()` to `controller.cancel()` (or create new controller per run).

- [ ] **Step 4: Commit**

```bash
git add matmaster/devshell/runner.py matmaster/devshell/debug_run.py matmaster/devshell/repl.py
git commit -m "refactor: devshell entry points use CancellationController with inject"
```

### Task 16: Test file updates + final cleanup

**Known test/helper files that reference `stop_event` and must be updated:**
- `tests/matmaster/core/agent_kernel_test_helpers.py` (test helpers for agent kernel)
- `tests/matmaster/tools/test_lazy_mcp.py` (LazyMcpTool tests)
- `tests/matmaster/core/test_exp_runtime_v2.py` (Exp runtime tests)
- `tests/matmaster/types/test_session_protocol.py` (Session protocol compliance)
- `tests/matmaster/types/test_tool_spec.py` (ToolExecutionContext tests)
- `tests/matmaster/tools/test_spawn_tool.py` (SpawnTool tests)
- `tests/matmaster/core/test_full_tool_runner.py` (FullToolRunner tests)
- Any service-layer tests (e.g. `tests/matmaster/services/test_agent_run_stream.py`)

- [ ] **Step 1: Grep for remaining stop_event references in tests**

Run: `grep -rn 'stop_event\|_stop_event\|StopEventLike\|RedisBackedStopEvent' tests/ --include='*.py' | grep -v __pycache__`

Update all references: `stop_event` → `cancel_token`, `threading.Event()` → `CancellationController()` / `.token`, etc.

- [ ] **Step 2: Grep for remaining stop_event references in source**

Run: `grep -rn 'stop_event\|_stop_event\|StopEventLike\|RedisBackedStopEvent' matmaster/ src/ --include='*.py' | grep -v __pycache__`

Fix any remaining references.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v --timeout=120 -x`

Fix any failures.

- [ ] **Step 4: Commit any remaining fixes**

```bash
git add -u
git commit -m "refactor: final cleanup of stop_event → cancel_token migration"
```
