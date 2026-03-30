# Confirmation Bridge Simplification Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bridge thread + Future + buffered-reply mechanism with a single async callable injected into ConfirmationHook, eliminating ~100 lines of threading complexity.

**Architecture:** ConfirmationHook becomes a thin gate that calls an injected `get_reply` async callable. The service layer constructs this callable by wrapping `ReplyQueue.get()` in `loop.run_in_executor`. No bridge thread, no stop_event, no manual cleanup.

**Tech Stack:** Python asyncio, threading (executor only), pytest-asyncio

---

## Chunk 1: Core Rewrite

### Task 1: Rewrite ConfirmationHook tests

**Files:**
- Modify: `tests/matmaster/hooks/test_confirmation.py` (full rewrite)

- [ ] **Step 1: Rewrite test file with new test cases**

Replace the entire file. The new ConfirmationHook takes `get_reply: Callable[[], Awaitable[str | None]]` instead of `set_loop`/`resolve`/`cancel`. Delete `TestConfirmationHookAdapter` (class will be removed in Task 3).

```python
"""Async regression tests for ConfirmationHook."""

from __future__ import annotations

import asyncio

import pytest

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import HookAction
from matmaster.types.events import ConfirmationRequestEvent
from matmaster.types.messages import ToolCallData


def _tool_call(name: str = "execute_bash") -> ToolCallData:
    return ToolCallData(id=f"{name}-1", name=name, arguments={"command": "echo ok"})


async def _immediate_reply(reply: str | None = "approved") -> str | None:
    return reply


class TestConfirmationHook:
    """ConfirmationHook async get_reply behavior."""

    @pytest.mark.asyncio
    async def test_non_gated_tool_continues_without_emitting(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            confirm_tools={"execute_bash"},
            get_reply=_immediate_reply,
        )

        result = await hook.pre_tool_call(_tool_call("read_file"))

        assert result == HookAction.CONTINUE
        assert bus.pending == 0

    @pytest.mark.asyncio
    async def test_approved_reply_continues(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            get_reply=lambda: _immediate_reply("approved"),
        )

        result = await hook.pre_tool_call(_tool_call())

        assert result == HookAction.CONTINUE

    @pytest.mark.asyncio
    async def test_none_reply_skips(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            get_reply=lambda: _immediate_reply(None),
        )

        result = await hook.pre_tool_call(_tool_call())

        assert result == HookAction.SKIP

    @pytest.mark.asyncio
    async def test_timeout_returns_skip(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        async def _hang_forever() -> str | None:
            await asyncio.sleep(999)
            return "never"

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            timeout_sec=0.05,
            get_reply=_hang_forever,
        )

        result = await hook.pre_tool_call(_tool_call())

        assert result == HookAction.SKIP

    @pytest.mark.asyncio
    async def test_confirm_tools_filter_skips_get_reply(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        called = False

        async def _should_not_be_called() -> str | None:
            nonlocal called
            called = True
            return "approved"

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            confirm_tools={"execute_bash"},
            get_reply=_should_not_be_called,
        )

        result = await hook.pre_tool_call(_tool_call("read_file"))

        assert result == HookAction.CONTINUE
        assert not called

    @pytest.mark.asyncio
    async def test_emits_confirmation_request_event(self) -> None:
        from matmaster.hooks.confirmation import ConfirmationHook

        bus = MessageBus()
        hook = ConfirmationHook(
            bus=bus,
            get_reply=lambda: _immediate_reply("approved"),
        )

        await hook.pre_tool_call(_tool_call())

        event = await bus.get(timeout=0.1)
        assert isinstance(event, ConfirmationRequestEvent)
        assert event.question == "Confirm tool call: execute_bash?"
```

- [ ] **Step 2: Run tests — expect FAIL (ConfirmationHook signature mismatch)**

Run: `uv run pytest tests/matmaster/hooks/test_confirmation.py -x -v`
Expected: FAIL — `ConfirmationHook.__init__()` does not accept `get_reply` parameter

- [ ] **Step 3: Rewrite ConfirmationHook**

Replace `matmaster/hooks/confirmation.py` entirely:

```python
"""ConfirmationHook -- async confirmation gate for tool execution.

Accepts an async callable (get_reply) that produces user replies.
The service layer is responsible for constructing this callable,
e.g. by wrapping a blocking ReplyQueue in loop.run_in_executor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import BaseHook, HookAction
from matmaster.types.events import ConfirmationRequestEvent
from matmaster.types.messages import ToolCallData

logger = logging.getLogger(__name__)


class ConfirmationHook(BaseHook):
    """Gate selected tool calls until the user explicitly confirms them."""

    def __init__(
        self,
        bus: MessageBus,
        *,
        timeout_sec: float = 20,
        confirm_tools: set[str] | None = None,
        get_reply: Callable[[], Awaitable[str | None]],
        source: str = "MatMaster",
    ) -> None:
        self._bus = bus
        self._timeout_sec = timeout_sec
        self._confirm_tools = confirm_tools
        self._get_reply = get_reply
        self._source = source

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Wait asynchronously for user confirmation before running a tool."""

        if self._confirm_tools is not None and tool_call.name not in self._confirm_tools:
            return HookAction.CONTINUE

        await self._bus.emit(
            ConfirmationRequestEvent(
                source=self._source,
                question=f"Confirm tool call: {tool_call.name}?",
                mode="timeout",
                timeout_seconds=int(self._timeout_sec),
            )
        )

        try:
            reply = await asyncio.wait_for(self._get_reply(), timeout=self._timeout_sec)
        except asyncio.TimeoutError:
            logger.info("Confirmation timed out for tool %s", tool_call.name)
            return HookAction.SKIP

        if reply is None:
            logger.info("User cancelled tool call %s", tool_call.name)
            return HookAction.SKIP

        return HookAction.CONTINUE
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/matmaster/hooks/test_confirmation.py -x -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/hooks/confirmation.py tests/matmaster/hooks/test_confirmation.py
git commit -m "refactor(confirmation): rewrite ConfirmationHook with injected async callable

Replace Future + buffered-reply + state-lock + bridge-thread mechanism
with a single get_reply async callable. Hook no longer manages threading
infrastructure — it just awaits the callable and returns HookAction."
```

### Task 2: Replace bridge with `_poll_reply_queue` in agent_run_service.py

**Files:**
- Modify: `src/services/agent_run_service.py:12,26,147-199,276-277,464-475,605-615`

- [ ] **Step 1: Add `_poll_reply_queue` and remove `_start_confirmation_reply_bridge`**

In `src/services/agent_run_service.py`:

1. At line 12, add `from queue import Empty` import:

```python
import queue
from queue import Empty
```

2. Remove the top-level `ConfirmationHook` import at line 26 (it's only used in the conditional block at line 465). Keep `import threading` — still used by `stop_event` and `_loop_thread`.

3. Replace lines 147-199 (ReplyQueueLike Protocol + `_start_confirmation_reply_bridge`) with:

```python
@runtime_checkable
class ReplyQueueLike(Protocol):
    """Confirmation reply queue abstraction: put content/cancel, blocking get.

    Used by _poll_reply_queue to bridge blocking queue reads into async context
    via loop.run_in_executor.
    """

    def put_content(self, content: str) -> None: ...

    def put_cancel(self) -> None: ...

    def get(self, timeout: float | None = None) -> str | None:
        """Blocking get. Returns None for cancel; raises queue.Empty on timeout."""
        ...


async def _poll_reply_queue(
    reply_queue: ReplyQueueLike, poll_sec: int = 1
) -> str | None:
    """Await a blocking reply queue in executor. Returns content or None for cancel.

    Uses int poll_sec (not float) because RedisReplyQueue.get() coerces via
    int(timeout) -- 0.5 becomes 0, triggering BLPOP timeout=0 (block forever).
    """
    loop = asyncio.get_running_loop()
    while True:
        try:
            return await loop.run_in_executor(None, reply_queue.get, poll_sec)
        except Empty:
            continue
```

4. Remove the variable declarations at lines 276-277:

```python
# DELETE these two lines:
confirmation_reply_stop: threading.Event | None = None
confirmation_reply_thread: threading.Thread | None = None
```

5. Replace hook construction at lines 464-476 (the `if mode == 'direct' ...` block):

```python
            if mode == 'direct' and reply_queue is not None and _CONFIRM_TOOLS:
                from matmaster.hooks.confirmation import ConfirmationHook

                confirmation_hook = ConfirmationHook(
                    bus=bus,
                    confirm_tools=set(_CONFIRM_TOOLS),
                    get_reply=lambda: _poll_reply_queue(reply_queue),
                )
                merged_hooks = [confirmation_hook, *runtime.spec.hooks, *observer_hooks]
```

6. Remove bridge cleanup in the finally block (lines 605-615):

```python
# DELETE these lines:
            if confirmation_reply_stop is not None:
                confirmation_reply_stop.set()
            if confirmation_reply_thread is not None:
                confirmation_reply_thread.join(timeout=2.0)
                if confirmation_reply_thread.is_alive():
                    logger.warning(
                        'confirmation-reply-bridge did not exit cleanly: '
                        'session_id=%s task_id=%s',
                        session_id,
                        task_id,
                    )
```

- [ ] **Step 2: Run existing integration tests**

Run: `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py::TestAgentRunServiceConfirmationRecovery::test_run_agent_sync_approval_executes_gated_tool tests/matmaster/integration/test_upstream_scenarios.py::TestAgentRunServiceConfirmationRecovery::test_run_agent_sync_cancel_skips_gated_tool -x -v`
Expected: Both PASS (they use `reply_queue.put_content`/`put_cancel` which still works)

- [ ] **Step 3: Commit**

```bash
git add src/services/agent_run_service.py
git commit -m "refactor(agent-run): replace bridge thread with _poll_reply_queue async helper

Delete _start_confirmation_reply_bridge and its thread lifecycle management.
ConfirmationHook now receives an async callable that wraps the blocking
ReplyQueue.get() via loop.run_in_executor. No stop_event, no thread.join,
no manual cleanup."
```

### Task 3: Delete ConfirmationHookAdapter and clean up set_loop

**Files:**
- Modify: `src/services/stream_service.py:203-231`
- Modify: `matmaster/core/agent.py:118-122`

- [ ] **Step 1: Delete ConfirmationHookAdapter from stream_service.py**

Remove lines 203-231 (the entire `ConfirmationHookAdapter` class). Also remove the `ConfirmationHook` import — it's only used by `ConfirmationHookAdapter`, so it becomes dead code after deletion.

- [ ] **Step 2: Delete set_loop injection from agent.py**

In `matmaster/core/agent.py`, remove lines 118-122:

```python
# DELETE these lines:
        # Inject running loop to hooks that need it (e.g. ConfirmationHook)
        loop = asyncio.get_running_loop()
        for hook in spec.hooks:
            if hasattr(hook, "set_loop"):
                hook.set_loop(loop)
```

- [ ] **Step 3: Run full test suite for affected areas**

Run: `uv run pytest tests/matmaster/hooks/test_confirmation.py tests/matmaster/integration/test_upstream_scenarios.py::TestAgentRunServiceConfirmationRecovery -x -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/services/stream_service.py matmaster/core/agent.py
git commit -m "refactor: delete ConfirmationHookAdapter and set_loop injection dead code

ConfirmationHookAdapter was never instantiated in runtime paths.
set_loop injection in AgentKernel.run() is dead code after
ConfirmationHook no longer has set_loop()."
```

### Task 4: Update bridge integration test

**Files:**
- Modify: `tests/matmaster/integration/test_upstream_scenarios.py:591-608`

- [ ] **Step 1: Replace bridge thread test with _poll_reply_queue test**

Replace `test_confirmation_reply_bridge_thread_exits_with_redis_compatible_timeout` (lines 591-608) with:

```python
    @pytest.mark.asyncio
    async def test_poll_reply_queue_uses_integer_second_timeout(self) -> None:
        from src.services.agent_run_service import _poll_reply_queue

        reply_queue = _RedisCompatibleReplyQueue()
        reply_queue.put_content("approved")

        result = await _poll_reply_queue(reply_queue)

        assert result == "approved"
        assert 1 in reply_queue.requested_timeouts

    @pytest.mark.asyncio
    async def test_poll_reply_queue_cancel_returns_none(self) -> None:
        from src.services.agent_run_service import _poll_reply_queue

        reply_queue = _RedisCompatibleReplyQueue()
        reply_queue.put_cancel()

        result = await _poll_reply_queue(reply_queue)

        assert result is None

    @pytest.mark.asyncio
    async def test_poll_reply_queue_retries_on_empty(self) -> None:
        from src.services.agent_run_service import _poll_reply_queue

        reply_queue = _RedisCompatibleReplyQueue()
        # Schedule reply after a short delay — first get() will raise Empty
        import threading
        threading.Timer(0.05, reply_queue.put_content, args=("delayed",)).start()

        result = await asyncio.wait_for(_poll_reply_queue(reply_queue), timeout=3.0)

        assert result == "delayed"
        assert len(reply_queue.requested_timeouts) >= 1

    @pytest.mark.asyncio
    async def test_poll_reply_queue_timeout_via_wait_for(self) -> None:
        from src.services.agent_run_service import _poll_reply_queue

        reply_queue = _RedisCompatibleReplyQueue()
        # Don't put anything — should timeout

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_poll_reply_queue(reply_queue), timeout=0.1)
```

- [ ] **Step 2: Run updated tests**

Run: `uv run pytest tests/matmaster/integration/test_upstream_scenarios.py::TestAgentRunServiceConfirmationRecovery -x -v`
Expected: All PASS (4 new + 2 existing E2E)

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/integration/test_upstream_scenarios.py
git commit -m "test(confirmation): replace bridge thread test with _poll_reply_queue tests

Cover integer-second timeout enforcement, cancel-returns-None,
and asyncio.wait_for timeout propagation."
```

### Task 5: Final verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/matmaster/ -x -v --timeout=30`
Expected: All PASS, no regressions

- [ ] **Step 2: Verify line count reduction**

Run: `git diff --stat HEAD~4`
Expected: Net deletion of ~100 lines across the 4 commits

- [ ] **Step 3: Verify no remaining references to deleted APIs**

Run:
```bash
rg "resolve\(\)|\.cancel\(\)|set_loop|_start_confirmation_reply_bridge|_buffered_reply|_state_lock|_pending_future|_NO_REPLY|confirmation_reply_stop|confirmation_reply_thread|ConfirmationHookAdapter" --type py src/ matmaster/ tests/
```
Expected: No matches (only comments/strings if any)
