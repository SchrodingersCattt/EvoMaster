# Hook System Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the disconnected 7-point Hook Protocol with a unified HookExecutor supporting 8 event types across observe/intercept/rewrite capabilities.

**Architecture:** HookExecutor is a single dispatch object created in `Exp.build_runtime()`, injected via `AgentRuntimeSpec.hook_executor`. Three dispatch methods (`emit`, `emit_intercept`, `emit_rewrite`) handle parallel observation, parallel interception with result aggregation, and serial rewrite chains respectively. All hook call sites live inside `Exp.run_stream` / kernel / FullToolRunner boundary.

**Tech Stack:** Python 3.10+, asyncio, Pydantic v2, pytest + pytest-asyncio

**Spec:** `docs/specs/2026-04-03-hook-system-redesign.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `matmaster/core/hooks.py` | Rewrite | HookEvent enum, 6 context dataclasses, HookOutcome, HookResult, handler type aliases, HookExecutor |
| `matmaster/types/runtime.py` | Modify (line 19, 68) | Replace `hooks: list[Hook]` with `hook_executor: HookExecutor \| None` |
| `matmaster/core/exp.py` | Modify | Create HookExecutor in build_runtime (before _make_spawn_fn); SUBAGENT hooks in _make_spawn_fn; inject run_meta into spec.meta |
| `matmaster/core/tool_runner.py` | Modify | PRE_TOOL_CALL + POST_TOOL_CALL in FullToolRunner; remove D-01 constraint; clean up imports |
| `matmaster/core/agent.py` | Modify | RUN_START/END in AgentKernel.run_stream; USER_PROMPT_SUBMIT + CONTEXT_COMPACTION in _run_items; remove old run_pre_llm_call/run_should_continue |
| `matmaster/devshell/stream_hook.py` | Modify (all) | Remove BaseHook inheritance and old hook methods; keep on_event() |
| `matmaster/devshell/event_observer.py` | Modify (all) | Remove BaseHook inheritance and old hook methods from DevEventHook |
| `matmaster/core/__init__.py` | Modify (lines 7-24) | Update re-exports |
| `matmaster/hooks/__init__.py` | Keep (update docstring) | Retain empty package for backward compat (`import matmaster.hooks` tested in test_upstream_scenarios) |
| `tests/matmaster/core/test_hooks.py` | Rewrite | Tests for HookExecutor |
| `tests/matmaster/core/test_hook_wiring.py` | Create | Integration tests for hook call sites |
| `tests/conftest.py` | Modify | Remove HookAction import and old hook mocks |
| `tests/matmaster/types/test_runtime.py` | Modify | Update spec.hooks -> spec.hook_executor references |
| `tests/matmaster/core/test_full_tool_runner.py` | Modify | Remove BaseHook/HookAction imports |
| `tests/matmaster/core/agent_kernel_test_helpers.py` | Modify | Remove old hook helper references |
| `tests/matmaster/services/test_agent_run_stream.py` | Modify | Update spec.hooks = [] to hook_executor |
| `tests/matmaster/test_validation.py` | Modify | Remove Hook/HookAction imports |

---

## Chunk 1: Core Types & HookExecutor

### Task 1: HookEvent, Context Dataclasses, HookOutcome, HookResult

**Files:**
- Rewrite: `matmaster/core/hooks.py`
- Test: `tests/matmaster/core/test_hooks.py`

- [ ] **Step 1: Write failing tests for types**

```python
# tests/matmaster/core/test_hooks.py
"""Tests for the redesigned hook system (HookExecutor + typed events)."""

import pytest

from matmaster.core.hooks import (
    CompactionContext,
    HookEvent,
    HookOutcome,
    HookResult,
    PostToolCallContext,
    PreToolCallContext,
    RunContext,
    SubagentContext,
    UserPromptContext,
)


class TestHookEvent:
    def test_all_events_defined(self):
        assert len(HookEvent) == 8

    def test_values_are_strings(self):
        assert HookEvent.RUN_START == "run_start"
        assert HookEvent.RUN_END == "run_end"
        assert HookEvent.PRE_TOOL_CALL == "pre_tool_call"
        assert HookEvent.POST_TOOL_CALL == "post_tool_call"
        assert HookEvent.SUBAGENT_START == "subagent_start"
        assert HookEvent.SUBAGENT_STOP == "subagent_stop"
        assert HookEvent.CONTEXT_COMPACTION == "context_compaction"
        assert HookEvent.USER_PROMPT_SUBMIT == "user_prompt_submit"


class TestHookOutcome:
    def test_outcomes(self):
        assert HookOutcome.SUCCESS == "success"
        assert HookOutcome.BLOCK == "block"
        assert HookOutcome.ERROR == "error"


class TestHookResult:
    def test_defaults(self):
        r = HookResult()
        assert r.outcome == HookOutcome.SUCCESS
        assert r.message == ""
        assert r.data is None

    def test_block_with_message(self):
        r = HookResult(outcome=HookOutcome.BLOCK, message="blocked")
        assert r.outcome == HookOutcome.BLOCK
        assert r.message == "blocked"


class TestContextDataclasses:
    def test_run_context_frozen(self):
        ctx = RunContext(task_id="t1", session_id="s1", reason="startup")
        with pytest.raises(AttributeError):
            ctx.reason = "other"  # type: ignore[misc]

    def test_pre_tool_call_context(self):
        ctx = PreToolCallContext(
            tool_name="bash", tool_call_id="tc1", arguments={"cmd": "ls"}, turn=1
        )
        assert ctx.tool_name == "bash"
        assert ctx.turn == 1

    def test_post_tool_call_context(self):
        from matmaster.tools.tool_result import ToolResult

        tr = ToolResult(status="success", content="ok")
        ctx = PostToolCallContext(
            tool_name="bash", tool_call_id="tc1", arguments={}, result=tr, turn=2
        )
        assert ctx.result.status == "success"

    def test_subagent_context_default_task_preview(self):
        ctx = SubagentContext(
            agent_id="a1", agent_type="direct", parent_session_id="s1"
        )
        assert ctx.task_preview == ""

    def test_compaction_context(self):
        ctx = CompactionContext(
            messages_before=100, messages_after=20, trigger_tokens=8000, strategy="summary"
        )
        assert ctx.trigger_tokens == 8000

    def test_user_prompt_context(self):
        ctx = UserPromptContext(prompt="hello", session_id="s1")
        assert ctx.prompt == "hello"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_hooks.py -v --no-header -x 2>&1 | head -30`
Expected: ImportError (new types don't exist yet)

- [ ] **Step 3: Implement types in hooks.py**

Rewrite `matmaster/core/hooks.py` with:

```python
"""HookExecutor: unified event dispatch for the matmaster agent kernel.

Eight hook events across three capabilities:
- Observe (parallel, fire-and-forget): RUN_START, RUN_END, SUBAGENT_START, SUBAGENT_STOP, CONTEXT_COMPACTION
- Intercept (parallel, aggregated): PRE_TOOL_CALL
- Rewrite (serial chain): POST_TOOL_CALL, USER_PROMPT_SUBMIT

Spec: docs/specs/2026-04-03-hook-system-redesign.md
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

from matmaster.tools.tool_result import ToolResult

logger = logging.getLogger(__name__)

# ── Handler type aliases ────────────────────────────────
# Using Any for parameters — type safety is enforced by runtime convention,
# not compile-time generics. See spec "类型安全说明" section.

ObserveHandler = Callable[[Any], Awaitable[None]]
InterceptHandler = Callable[[Any], Awaitable["HookResult"]]
RewriteHandler = Callable[[Any, Any], Awaitable[Any]]


# ── Enums ───────────────────────────────────────────────


class HookEvent(str, enum.Enum):
    RUN_START = "run_start"
    RUN_END = "run_end"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    CONTEXT_COMPACTION = "context_compaction"
    USER_PROMPT_SUBMIT = "user_prompt_submit"


class HookOutcome(str, enum.Enum):
    SUCCESS = "success"
    BLOCK = "block"
    ERROR = "error"


# ── Result ──────────────────────────────────────────────


@dataclass
class HookResult:
    outcome: HookOutcome = HookOutcome.SUCCESS
    message: str = ""
    data: Any = None


# ── Context dataclasses ─────────────────────────────────


@dataclass(frozen=True)
class RunContext:
    task_id: str
    session_id: str
    reason: str


@dataclass(frozen=True)
class PreToolCallContext:
    tool_name: str
    tool_call_id: str
    arguments: dict[str, Any]
    turn: int


@dataclass(frozen=True)
class PostToolCallContext:
    tool_name: str
    tool_call_id: str
    arguments: dict[str, Any]
    result: ToolResult
    turn: int


@dataclass(frozen=True)
class SubagentContext:
    agent_id: str
    agent_type: str
    parent_session_id: str
    task_preview: str = ""


@dataclass(frozen=True)
class CompactionContext:
    messages_before: int
    messages_after: int
    trigger_tokens: int
    strategy: str


@dataclass(frozen=True)
class UserPromptContext:
    prompt: str
    session_id: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_hooks.py -v --no-header -x 2>&1 | head -40`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/hooks.py tests/matmaster/core/test_hooks.py
git commit -m "feat(hooks): add HookEvent, context dataclasses, HookOutcome, HookResult"
```

---

### Task 2: HookExecutor Core

**Files:**
- Modify: `matmaster/core/hooks.py` (append HookExecutor class)
- Test: `tests/matmaster/core/test_hooks.py` (append executor tests)

- [ ] **Step 1: Write failing tests for HookExecutor.emit (observe)**

```python
# Append to tests/matmaster/core/test_hooks.py

from matmaster.core.hooks import HookExecutor


class TestHookExecutorEmit:
    """Tests for observe dispatch (parallel, error-isolated)."""

    @pytest.mark.asyncio
    async def test_emit_no_handlers(self):
        ex = HookExecutor()
        # Should not raise
        await ex.emit(HookEvent.RUN_START, RunContext("t1", "s1", "startup"))

    @pytest.mark.asyncio
    async def test_emit_calls_all_observers(self):
        ex = HookExecutor()
        calls = []

        async def obs1(ctx):
            calls.append(("obs1", ctx.reason))

        async def obs2(ctx):
            calls.append(("obs2", ctx.reason))

        ex.on(HookEvent.RUN_START, obs1)
        ex.on(HookEvent.RUN_START, obs2)
        await ex.emit(HookEvent.RUN_START, RunContext("t1", "s1", "startup"))
        assert ("obs1", "startup") in calls
        assert ("obs2", "startup") in calls

    @pytest.mark.asyncio
    async def test_emit_swallows_exceptions(self, caplog):
        ex = HookExecutor()

        async def bad_hook(ctx):
            raise ValueError("boom")

        async def good_hook(ctx):
            pass  # should still run

        ex.on(HookEvent.RUN_START, bad_hook)
        ex.on(HookEvent.RUN_START, good_hook)
        await ex.emit(HookEvent.RUN_START, RunContext("t1", "s1", "startup"))
        assert "boom" in caplog.text

    @pytest.mark.asyncio
    async def test_emit_ignores_other_events(self):
        ex = HookExecutor()
        called = False

        async def obs(ctx):
            nonlocal called
            called = True

        ex.on(HookEvent.RUN_START, obs)
        await ex.emit(HookEvent.RUN_END, RunContext("t1", "s1", "completed"))
        assert not called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_hooks.py::TestHookExecutorEmit -v --no-header -x 2>&1 | head -20`
Expected: ImportError (HookExecutor not defined)

- [ ] **Step 3: Write failing tests for HookExecutor.emit_intercept**

```python
# Append to tests/matmaster/core/test_hooks.py

class TestHookExecutorIntercept:
    """Tests for intercept dispatch (parallel, aggregated)."""

    @pytest.mark.asyncio
    async def test_intercept_no_handlers_returns_success(self):
        ex = HookExecutor()
        r = await ex.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )
        assert r.outcome == HookOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_intercept_single_block(self):
        ex = HookExecutor()

        async def blocker(ctx):
            return HookResult(outcome=HookOutcome.BLOCK, message="denied")

        ex.intercept(HookEvent.PRE_TOOL_CALL, blocker)
        r = await ex.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )
        assert r.outcome == HookOutcome.BLOCK
        assert r.message == "denied"

    @pytest.mark.asyncio
    async def test_intercept_multiple_blocks_aggregate_messages(self):
        ex = HookExecutor()

        async def blocker1(ctx):
            return HookResult(outcome=HookOutcome.BLOCK, message="reason1")

        async def blocker2(ctx):
            return HookResult(outcome=HookOutcome.BLOCK, message="reason2")

        ex.intercept(HookEvent.PRE_TOOL_CALL, blocker1)
        ex.intercept(HookEvent.PRE_TOOL_CALL, blocker2)
        r = await ex.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )
        assert r.outcome == HookOutcome.BLOCK
        assert "reason1" in r.message
        assert "reason2" in r.message

    @pytest.mark.asyncio
    async def test_intercept_all_success(self):
        ex = HookExecutor()

        async def allow(ctx):
            return HookResult()

        ex.intercept(HookEvent.PRE_TOOL_CALL, allow)
        r = await ex.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )
        assert r.outcome == HookOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_intercept_exception_becomes_error(self, caplog):
        ex = HookExecutor()

        async def bad(ctx):
            raise RuntimeError("oops")

        ex.intercept(HookEvent.PRE_TOOL_CALL, bad)
        r = await ex.emit_intercept(
            HookEvent.PRE_TOOL_CALL,
            PreToolCallContext("bash", "tc1", {}, 1),
        )
        # Exception -> ERROR outcome, not BLOCK
        assert r.outcome == HookOutcome.SUCCESS  # no BLOCK, so aggregated result is SUCCESS
        assert "oops" in caplog.text
```

- [ ] **Step 4: Write failing tests for HookExecutor.emit_rewrite**

```python
# Append to tests/matmaster/core/test_hooks.py

class TestHookExecutorRewrite:
    """Tests for rewrite dispatch (serial chain)."""

    @pytest.mark.asyncio
    async def test_rewrite_no_handlers_returns_original(self):
        ex = HookExecutor()
        result = await ex.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("hello", "s1"),
            "hello",
        )
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_rewrite_single_modifier(self):
        ex = HookExecutor()

        async def add_prefix(ctx, prompt):
            return f"[modified] {prompt}"

        ex.rewrite(HookEvent.USER_PROMPT_SUBMIT, add_prefix)
        result = await ex.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("hello", "s1"),
            "hello",
        )
        assert result == "[modified] hello"

    @pytest.mark.asyncio
    async def test_rewrite_chain_passes_previous_output(self):
        ex = HookExecutor()

        async def step1(ctx, data):
            return f"({data})"

        async def step2(ctx, data):
            return f"[{data}]"

        ex.rewrite(HookEvent.USER_PROMPT_SUBMIT, step1)
        ex.rewrite(HookEvent.USER_PROMPT_SUBMIT, step2)
        result = await ex.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("x", "s1"),
            "x",
        )
        assert result == "[(x)]"

    @pytest.mark.asyncio
    async def test_rewrite_none_means_no_change(self):
        ex = HookExecutor()

        async def noop(ctx, data):
            return None

        async def modify(ctx, data):
            return f"[{data}]"

        ex.rewrite(HookEvent.USER_PROMPT_SUBMIT, noop)
        ex.rewrite(HookEvent.USER_PROMPT_SUBMIT, modify)
        result = await ex.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("x", "s1"),
            "x",
        )
        assert result == "[x]"

    @pytest.mark.asyncio
    async def test_rewrite_exception_swallowed(self, caplog):
        ex = HookExecutor()

        async def bad(ctx, data):
            raise ValueError("fail")

        async def good(ctx, data):
            return f"[{data}]"

        ex.rewrite(HookEvent.USER_PROMPT_SUBMIT, bad)
        ex.rewrite(HookEvent.USER_PROMPT_SUBMIT, good)
        result = await ex.emit_rewrite(
            HookEvent.USER_PROMPT_SUBMIT,
            UserPromptContext("x", "s1"),
            "x",
        )
        assert result == "[x]"
        assert "fail" in caplog.text
```

- [ ] **Step 5: Implement HookExecutor**

Append to `matmaster/core/hooks.py`:

```python
# ── HookExecutor ────────────────────────────────────────


class HookExecutor:
    """Unified hook dispatch: observe (parallel), intercept (parallel+aggregate), rewrite (serial chain)."""

    def __init__(self) -> None:
        self._observers: dict[HookEvent, list[ObserveHandler]] = defaultdict(list)
        self._interceptors: dict[HookEvent, list[InterceptHandler]] = defaultdict(list)
        self._rewriters: dict[HookEvent, list[RewriteHandler]] = defaultdict(list)

    # ── Registration ────────────────────────────────────

    def on(self, event: HookEvent, handler: ObserveHandler) -> None:
        self._observers[event].append(handler)

    def intercept(self, event: HookEvent, handler: InterceptHandler) -> None:
        self._interceptors[event].append(handler)

    def rewrite(self, event: HookEvent, handler: RewriteHandler) -> None:
        self._rewriters[event].append(handler)

    # ── Dispatch ────────────────────────────────────────

    async def emit(self, event: HookEvent, ctx: Any) -> None:
        handlers = self._observers.get(event, [])
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(ctx) for h in handlers),
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.warning("Hook %s raised: %s", handlers[i], r)

    async def emit_intercept(self, event: HookEvent, ctx: Any) -> HookResult:
        handlers = self._interceptors.get(event, [])
        if not handlers:
            return HookResult()
        results = await asyncio.gather(
            *(self._safe_intercept(h, ctx) for h in handlers),
        )
        blocks = [r for r in results if r.outcome == HookOutcome.BLOCK]
        if blocks:
            combined_msg = "; ".join(b.message for b in blocks if b.message)
            return HookResult(outcome=HookOutcome.BLOCK, message=combined_msg)
        return HookResult()

    async def emit_rewrite(self, event: HookEvent, ctx: Any, data: T) -> T:
        for handler in self._rewriters.get(event, []):
            try:
                modified = await handler(ctx, data)
                if modified is not None:
                    data = modified
            except Exception as e:
                logger.warning("Rewrite hook %s raised: %s", handler, e)
        return data

    async def _safe_intercept(self, handler: InterceptHandler, ctx: Any) -> HookResult:
        try:
            return await handler(ctx)
        except Exception as e:
            logger.warning("Intercept hook %s raised: %s", handler, e)
            return HookResult(outcome=HookOutcome.ERROR, message=str(e))
```

- [ ] **Step 6: Run all tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_hooks.py -v --no-header 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add matmaster/core/hooks.py tests/matmaster/core/test_hooks.py
git commit -m "feat(hooks): implement HookExecutor with emit/intercept/rewrite dispatch"
```

---

## Chunk 2: Spec Migration & Wiring

### Task 3: AgentRuntimeSpec Field Migration

**Files:**
- Modify: `matmaster/types/runtime.py:19,68`
- Modify: `matmaster/core/__init__.py:7-24`

- [ ] **Step 1: Update AgentRuntimeSpec**

In `matmaster/types/runtime.py`:
- Line 19: Change `from matmaster.core.hooks import Hook` to `from matmaster.core.hooks import HookExecutor`
- Line 68: Change `hooks: list[Hook] = Field(default_factory=list)` to `hook_executor: HookExecutor | None = None`

- [ ] **Step 2: Update core/__init__.py re-exports**

In `matmaster/core/__init__.py`:
- Remove: `from .hooks import BaseHook, Hook, HookAction`
- Add: `from .hooks import HookEvent, HookExecutor, HookOutcome, HookResult`
- Update `__all__` accordingly

- [ ] **Step 3: Fix all import errors in source files**

Search for and update all files importing the old symbols:
- `matmaster/core/agent.py:39-42` — remove `run_pre_llm_call, run_should_continue` imports
- `matmaster/core/tool_runner.py:26-31` — remove `HookAction, run_guard_blocked, run_post_tool_call, run_pre_tool_call` imports
- `matmaster/devshell/stream_hook.py:9` — remove `from matmaster.core.hooks import BaseHook, HookAction`
- `matmaster/devshell/event_observer.py:26` — remove `from matmaster.core.hooks import BaseHook, HookAction`

- [ ] **Step 3b: Fix all import errors in test files (prevent breakage window)**

Run: `grep -rn "from matmaster.core.hooks import\|from matmaster.core import.*BaseHook\|from matmaster.core import.*HookAction\|from matmaster.core import.*Hook\b\|spec\.hooks" --include="*.py" tests/ 2>&1 | head -30`

Update every match:
- Replace `from matmaster.core.hooks import Hook, HookAction, BaseHook, ...` with `from matmaster.core.hooks import HookExecutor, HookEvent, HookOutcome, HookResult`
- Replace `spec.hooks = [...]` or `spec.hooks` references with `spec.hook_executor = None` or equivalent
- Replace `from matmaster.core import BaseHook, Hook, HookAction` with new symbols
- Remove or update mock classes that implement the old Hook Protocol

Key files to update:
- `tests/conftest.py` — remove HookAction import, update mock hooks
- `tests/matmaster/types/test_runtime.py` — update all `spec.hooks` references
- `tests/matmaster/core/test_tool_runner.py` — remove BaseHook/HookAction imports
- `tests/matmaster/core/agent_kernel_test_helpers.py` — remove old hook helper references
- `tests/matmaster/services/test_agent_run_stream.py` — update `spec.hooks = []`
- `tests/matmaster/test_validation.py` — remove Hook/HookAction imports
- `tests/matmaster/devshell/test_stream_hook.py` — remove HookAction import

- [ ] **Step 4: Run import check**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -c "from matmaster.types.runtime import AgentRuntimeSpec; print('OK')"`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add matmaster/types/runtime.py matmaster/core/__init__.py matmaster/core/agent.py matmaster/core/tool_runner.py matmaster/devshell/stream_hook.py
git commit -m "refactor(hooks): migrate AgentRuntimeSpec.hooks to hook_executor"
```

---

### Task 4: Exp Assembly, Kernel RUN_START/END, Subagent Hooks

**Files:**
- Modify: `matmaster/core/exp.py`
- Modify: `matmaster/core/agent.py` (AgentKernel.run_stream for RUN_START/END)
- Test: `tests/matmaster/core/test_hook_wiring.py` (create)

**Critical context:**
- `PlaygroundContext` does NOT have `task_id` or `session_id`. Use `ctx.run_meta.get("task_id", "")` / `ctx.run_meta.get("session_id", "")`.
- `devshell/runner.py` bypasses `Exp.run_stream()`, calling `kernel.run_stream()` directly. RUN_START/END must go in `AgentKernel.run_stream()`.
- `_make_spawn_fn()` is called BEFORE the hooks section in `build_runtime()`. Create `hook_executor` early.

- [ ] **Step 1: Wire HookExecutor creation at TOP of build_runtime**

In `matmaster/core/exp.py`, `build_runtime()`:

1. Create `hook_executor = HookExecutor()` at the TOP of `build_runtime`, BEFORE `_make_spawn_fn` call
2. Delete the old hooks section: `hooks = list(spec.hooks)`
3. In `spec.model_copy(update={...})`:
   - Replace `'hooks': hooks,` with `'hook_executor': hook_executor,`
   - Inject IDs into meta: `'meta': {**spec.meta, 'task_id': ctx.run_meta.get('task_id', ''), 'session_id': ctx.run_meta.get('session_id', '')},`

- [ ] **Step 4: Wire RUN_START/END in run_stream**

In `matmaster/core/exp.py`, in `run_stream()` (line 356+):

Replace:
```python
        try:
            runtime = await self.build_runtime(
                ctx,
                skills=skills,
                source_override=source_override,
                spawn_id=spawn_id,
            )
            if ctx.session is not None:
                ctx.session._stop_event = stop_event

            # Inject stop_event into tools for cancel propagation
            catalog = getattr(runtime.spec, "tool_catalog", None)
            if stop_event is not None and catalog is not None:
                catalog.inject_stop_event(stop_event)

            async for event in runtime.kernel.run_stream(
                runtime.spec, task, history=history, stop_event=stop_event
            ):
                yield event
        finally:
            await self._run_cleanup_callbacks()
```
with:
```python
        try:
            runtime = await self.build_runtime(
                ctx,
                skills=skills,
                source_override=source_override,
                spawn_id=spawn_id,
            )
            if ctx.session is not None:
                ctx.session._stop_event = stop_event

            # Inject stop_event into tools for cancel propagation
            catalog = getattr(runtime.spec, "tool_catalog", None)
            if stop_event is not None and catalog is not None:
                catalog.inject_stop_event(stop_event)

            # Hook: RUN_START
            if runtime.spec.hook_executor is not None:
                await runtime.spec.hook_executor.emit(
                    HookEvent.RUN_START,
                    RunContext(
                        task_id=ctx.task_id,
                        session_id=ctx.session_id if ctx.session_id else "",
                        reason="startup",
                    ),
                )

            async for event in runtime.kernel.run_stream(
                runtime.spec, task, history=history, stop_event=stop_event
            ):
                yield event
        finally:
            # Hook: RUN_END
            if hasattr(self, '_last_runtime_spec') and self._last_runtime_spec and self._last_runtime_spec.hook_executor is not None:
                pass  # See step 5 for the actual pattern
            await self._run_cleanup_callbacks()
```

- [ ] **Step 2: Wire RUN_START/END in AgentKernel.run_stream (NOT Exp.run_stream)**

In `matmaster/core/agent.py`, `AgentKernel.run_stream()` — this is the true common entry point called by Exp.run_stream, devshell/runner.py, and spawn sub-agents:

Add RUN_START at the beginning (after `async with spec.llm_provider`), and RUN_END in a finally block. Track terminal reason from `_run_items` terminal items:

```python
    async def run_stream(self, spec, task, history=None, stop_event=None):
        from matmaster.types.events import RunResultEvent

        async with spec.llm_provider:
            ...existing _summary_provider setup...

            last_reason = "error"

            # Hook: RUN_START
            if spec.hook_executor is not None:
                await spec.hook_executor.emit(
                    HookEvent.RUN_START,
                    RunContext(
                        task_id=spec.meta.get("task_id", ""),
                        session_id=spec.meta.get("session_id", ""),
                        reason="startup",
                    ),
                )

            try:
                # ...existing _consume_and_yield logic...
                # When processing terminal items, capture reason:
                #   last_reason = item.terminal.reason
                ...
            finally:
                if spec.hook_executor is not None:
                    await spec.hook_executor.emit(
                        HookEvent.RUN_END,
                        RunContext(
                            task_id=spec.meta.get("task_id", ""),
                            session_id=spec.meta.get("session_id", ""),
                            reason=last_reason,
                        ),
                    )
```

Note: Integrate with the EXISTING `_consume_and_yield` pattern — add `last_reason` tracking where terminal items are processed, and wrap the existing generator consumption in try/finally.

- [ ] **Step 3: Wire SUBAGENT_START/STOP in _make_spawn_fn**

In `matmaster/core/exp.py`, `_make_spawn_fn` method (line 95+), the `spawn_fn` closure needs the HookExecutor. Change `_make_spawn_fn` signature to accept it:

```python
@staticmethod
def _make_spawn_fn(
    ctx: PlaygroundContext,
    source_prefix: str,
    hook_executor: HookExecutor | None = None,
) -> Any:
```

Inside the `spawn_fn` closure, before `drain_run_stream`:
```python
            child_spawn_id = uuid.uuid4().hex[:16]

            # Hook: SUBAGENT_START
            if hook_executor is not None:
                await hook_executor.emit(
                    HookEvent.SUBAGENT_START,
                    SubagentContext(
                        agent_id=child_spawn_id,
                        agent_type=exp_name,
                        parent_session_id=ctx.session_id or "",
                        task_preview=task[:200],
                    ),
                )
```

After `drain_run_stream` completes (before return):
```python
            # Hook: SUBAGENT_STOP
            if hook_executor is not None:
                await hook_executor.emit(
                    HookEvent.SUBAGENT_STOP,
                    SubagentContext(
                        agent_id=child_spawn_id,
                        agent_type=exp_name,
                        parent_session_id=ctx.session_id or "",
                        task_preview=task[:200],
                    ),
                )
```

Update the call site in `build_runtime` where `_make_spawn_fn` is called to pass `hook_executor`.

- [ ] **Step 4: Add imports**

In `exp.py`:
```python
from matmaster.core.hooks import HookEvent, HookExecutor, SubagentContext
```

In `agent.py`:
```python
from matmaster.core.hooks import HookEvent, RunContext
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/ -v --no-header -x 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/exp.py matmaster/core/agent.py
git commit -m "feat(hooks): wire HookExecutor creation, RUN_START/END, SUBAGENT_START/STOP"
```

---

### Task 5: FullToolRunner — PRE_TOOL_CALL & POST_TOOL_CALL

**Files:**
- Modify: `matmaster/core/tool_runner.py:200-206,255-374`

- [ ] **Step 1: Update FullToolRunner to accept HookExecutor**

Add `hook_executor` parameter to `FullToolRunner.__init__` (preserve all existing parameters):

```python
def __init__(
    self,
    catalog: ToolCatalog,
    structural_validation: StructuralValidation,
    guard_pipeline: GuardPipeline,
    capability_policy: CapabilityPolicy,
    scheduler: ToolScheduler,
    topology: RuntimeTopology,
    hook_executor: HookExecutor | None = None,
) -> None:
    # Keep all existing field assignments, just add:
    self._hook_executor = hook_executor
```

Note: The actual codebase `__init__` may have additional parameters (e.g. `state`). Preserve them all -- only ADD `hook_executor`.

Update the docstring to remove D-01 reference:

```python
class FullToolRunner:
    """Complete ToolRunner: Catalog -> Hook -> Validation -> Guard -> Policy -> Scheduler -> Execute -> Hook -> Release."""
```

- [ ] **Step 2: Wire PRE_TOOL_CALL after catalog lookup, before validation**

In `execute_batch`, Phase 1 loop, after catalog lookup succeeds (after line 286 `continue`), before structural validation (line 306):

```python
            # Hook: PRE_TOOL_CALL (observe + intercept)
            if self._hook_executor is not None:
                pre_ctx = PreToolCallContext(
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    arguments=tc.arguments,
                    turn=ctx.turn,
                )
                await self._hook_executor.emit(HookEvent.PRE_TOOL_CALL, pre_ctx)
                hook_result = await self._hook_executor.emit_intercept(
                    HookEvent.PRE_TOOL_CALL, pre_ctx
                )
                if hook_result.outcome == HookOutcome.BLOCK:
                    tr = ToolResult(
                        status="blocked",
                        content=hook_result.message or "Blocked by hook",
                        meta={"layer": "hook"},
                    )
                    results[idx] = (tc, tr)
                    if on_result:
                        await on_result(tc, tr)
                    continue
```

- [ ] **Step 3: Wire POST_TOOL_CALL after Phase 2 execution**

After each tool result is produced in Phase 2 (the `_execute_one` or gather results section), apply rewrite then observe:

```python
            # Hook: POST_TOOL_CALL (rewrite then observe)
            # Note: When multiple tools execute in parallel (asyncio.gather in Phase 2),
            # each tool's POST_TOOL_CALL rewrite chain runs independently. The serial
            # guarantee is per-tool (rewriters within one chain execute sequentially),
            # not cross-tool. This is correct — each tool result is independent.
            if self._hook_executor is not None:
                post_ctx = PostToolCallContext(
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    arguments=tc.arguments,
                    result=tr,
                    turn=ctx.turn,
                )
                tr = await self._hook_executor.emit_rewrite(
                    HookEvent.POST_TOOL_CALL, post_ctx, tr
                )
                # Rebuild context with final result for observers.
                # Required because PostToolCallContext is frozen — cannot mutate result field.
                post_ctx_final = PostToolCallContext(
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    arguments=tc.arguments,
                    result=tr,
                    turn=ctx.turn,
                )
                await self._hook_executor.emit(HookEvent.POST_TOOL_CALL, post_ctx_final)
```

- [ ] **Step 4: Add imports**

```python
from matmaster.core.hooks import (
    HookEvent,
    HookExecutor,
    HookOutcome,
    PostToolCallContext,
    PreToolCallContext,
)
```

- [ ] **Step 5: Update FullToolRunner construction in exp.py**

In `matmaster/core/exp.py` `build_runtime()`, where FullToolRunner is created (line 299):

```python
        # Add hook_executor= to the EXISTING FullToolRunner(...) construction.
        # Do NOT change any other arguments. The actual call already has
        # catalog, structural_validation, guard_pipeline, capability_policy,
        # scheduler, topology (and possibly state). Just append:
        #     hook_executor=hook_executor,
```

- [ ] **Step 6: Remove old hook imports from tool_runner.py**

Delete lines 26-31:
```python
from matmaster.core.hooks import (
    HookAction,
    run_guard_blocked,
    run_post_tool_call,
    run_pre_tool_call,
)
```

Remove any remaining references to old hook functions in InlineToolRunner (if it still exists, leave the class but remove hook calls).

- [ ] **Step 7: Run tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/ -v --no-header -x 2>&1 | tail -30`
Expected: PASS (fix any test failures from spec.hooks migration)

- [ ] **Step 8: Commit**

```bash
git add matmaster/core/tool_runner.py matmaster/core/exp.py
git commit -m "feat(hooks): wire PRE_TOOL_CALL and POST_TOOL_CALL in FullToolRunner"
```

---

### Task 6: AgentKernel — USER_PROMPT_SUBMIT & CONTEXT_COMPACTION

**Files:**
- Modify: `matmaster/core/agent.py:39-42,188-224`

- [ ] **Step 1: Wire USER_PROMPT_SUBMIT before UserMessage construction**

In `_run_items()`, before line 191 (`UserMessage(content=task)`):

```python
        # Hook: USER_PROMPT_SUBMIT (rewrite then observe)
        # session_id comes from spec.meta, populated by Exp.build_runtime
        # from ctx.run_meta.get("session_id", "")
        if spec.hook_executor is not None:
            _sid = spec.meta.get("session_id", "")
            prompt_ctx = UserPromptContext(prompt=task, session_id=_sid)
            task = await spec.hook_executor.emit_rewrite(
                HookEvent.USER_PROMPT_SUBMIT, prompt_ctx, task
            )
            prompt_ctx_final = UserPromptContext(prompt=task, session_id=_sid)
            await spec.hook_executor.emit(HookEvent.USER_PROMPT_SUBMIT, prompt_ctx_final)

        state = _KernelState(
            messages=[
                SystemMessage(content=spec.system_prompt),
                *(history or []),
                UserMessage(content=task),
            ]
        )
```

- [ ] **Step 2: Wire CONTEXT_COMPACTION after compact_if_needed**

In `_run_items()`, after the compactor block (lines 219-224):

```python
            if spec.compactor:
                messages_before = len(state.messages)
                await spec.compactor.compact_if_needed(
                    state.messages, turn_usage, state.turn
                )
                while compactor_events:
                    ce = compactor_events.popleft()
                    yield _KernelItem(event=ce)
                    # Hook: CONTEXT_COMPACTION
                    if spec.hook_executor is not None and hasattr(ce, 'payload'):
                        payload = getattr(ce, 'payload', {})
                        await spec.hook_executor.emit(
                            HookEvent.CONTEXT_COMPACTION,
                            CompactionContext(
                                messages_before=messages_before,
                                messages_after=len(state.messages),
                                trigger_tokens=payload.get("trigger_tokens", 0),
                                strategy=payload.get("strategy", "unknown"),
                            ),
                        )
```

- [ ] **Step 3: Remove old hook calls**

Delete lines 213-217:
```python
            await run_pre_llm_call(spec.hooks, state.messages, state.turn)

            if not await run_should_continue(spec.hooks, state.messages, state.turn):
                yield self._terminal(state, 'hook_stopped', turn_offset=-1)
                return
```

- [ ] **Step 4: Update imports**

Replace lines 39-42:
```python
from matmaster.core.hooks import (
    run_pre_llm_call,
    run_should_continue,
)
```
with:
```python
from matmaster.core.hooks import (
    CompactionContext,
    HookEvent,
    UserPromptContext,
)
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/ -v --no-header -x 2>&1 | tail -30`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/agent.py
git commit -m "feat(hooks): wire USER_PROMPT_SUBMIT and CONTEXT_COMPACTION in AgentKernel"
```

---

## Chunk 3: Cleanup & Final Verification

### Task 7: DevStreamHook & Dead Code Cleanup

**Files:**
- Modify: `matmaster/devshell/stream_hook.py`
- Delete: `matmaster/hooks/__init__.py` (and directory)
- Modify: `matmaster/devshell/cli.py` (if needed)
- Modify: `matmaster/devshell/runner.py` (if needed)

- [ ] **Step 1: Strip BaseHook from DevStreamHook**

In `matmaster/devshell/stream_hook.py`:
- Remove `from matmaster.core.hooks import BaseHook, HookAction`
- Change class declaration from `class DevStreamHook(BaseHook):` to `class DevStreamHook:`
- Delete all old hook methods: `on_stream_chunk`, `pre_tool_call`, `post_tool_call`, `on_guard_blocked`, `on_segment_complete`
- Keep `on_event()` and `__init__` intact

- [ ] **Step 1b: Strip BaseHook from DevEventHook**

In `matmaster/devshell/event_observer.py`:
- Remove `from matmaster.core.hooks import BaseHook, HookAction`
- Change class declaration from `class DevEventHook(BaseHook):` to `class DevEventHook:`
- Delete all old hook methods: `pre_tool_call`, `post_tool_call`, `on_segment_complete`
- Keep `on_event()` and `__init__` intact

- [ ] **Step 2: Remove InlineToolRunner hook calls**

In `matmaster/core/tool_runner.py`, if InlineToolRunner still exists:
- Remove hook-related calls (lines 115, 131, 141, 193)
- Remove `self._spec.hooks` reference
- Or if InlineToolRunner is only used in tests, leave it but remove hook calls

- [ ] **Step 3: Update matmaster/hooks/__init__.py (do NOT delete)**

`tests/matmaster/integration/test_upstream_scenarios.py` explicitly tests `import matmaster.hooks`. Keep the empty package for backward compatibility. Update the docstring to reference the new system:

```python
"""Hook infrastructure has moved to matmaster.core.hooks (HookExecutor).

This package is retained for backward compatibility. All business hooks
were retired in Phase 34. The new HookExecutor replaces the old
Hook Protocol / BaseHook system.
"""

__all__: list[str] = []
```

- [ ] **Step 4: Clean up devshell/cli.py and runner.py**

Check if `cli.py` or `runner.py` reference `spec.hooks` and remove those references. Based on the analysis, DevStreamHook is NOT added to spec.hooks, so these files should only need import cleanup.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/ -v --no-header -x 2>&1 | tail -40`
Expected: PASS (fix any remaining failures)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(hooks): remove BaseHook, old hook methods, InlineToolRunner hook calls, matmaster/hooks/"
```

---

### Task 8: Update Existing Tests

**Files:**
- Modify: `tests/matmaster/core/test_hooks.py` (already rewritten in Task 1-2)
- Modify: `tests/matmaster/devshell/test_stream_hook.py`
- Modify: any other test files importing old hook symbols

- [ ] **Step 1: Update test_stream_hook.py**

Remove tests for deleted methods (pre_tool_call, post_tool_call, on_guard_blocked). Keep tests for on_event() if any exist.

- [ ] **Step 2: Search for remaining references to old hook symbols**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && grep -rn "from matmaster.core.hooks import.*Hook\b\|from matmaster.core.hooks import.*HookAction\|from matmaster.core.hooks import.*BaseHook\|from matmaster.core import.*BaseHook\|spec\.hooks" --include="*.py" 2>&1 | head -30`

Fix all remaining references.

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/ -v --no-header 2>&1 | tail -40`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test(hooks): update tests for new HookExecutor system"
```

---

### Task 9: Final Verification

- [ ] **Step 1: Run full test suite one final time**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/ -v --no-header 2>&1 | tail -50`
Expected: All PASS

- [ ] **Step 2: Verify no remaining references to old hook system**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && grep -rn "HookAction\|BaseHook\|run_pre_tool_call\|run_should_continue\|run_pre_llm_call\|run_post_tool_call\|run_guard_blocked\|run_on_stream_chunk\|run_on_segment_complete" --include="*.py" matmaster/ src/ 2>&1 | head -20`
Expected: No matches (except possibly in comments)

- [ ] **Step 3: Verify import health**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -c "from matmaster.core.hooks import HookExecutor, HookEvent, HookOutcome, HookResult; from matmaster.types.runtime import AgentRuntimeSpec; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 4: Final commit (if any remaining fixes)**

```bash
git add -A
git commit -m "chore(hooks): final cleanup for hook system redesign"
```
