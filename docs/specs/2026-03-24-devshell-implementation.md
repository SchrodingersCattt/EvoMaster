# DevShell Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `mm-devshell`, a standalone interactive CLI for testing the matmaster agent chain without Redis/MySQL/frontend.

**Architecture:** Reuse Exp pipeline (`build_runtime` + `kernel.run`) with manually constructed PlaygroundContext. DevRunner mirrors `AgentRunService` split-call pattern. DevStreamHook for terminal output, EventLogger for JSONL persistence.

**Tech Stack:** Python 3.10+, Pydantic, PyYAML, argparse, readline, threading

**Spec:** `docs/specs/2026-03-24-devshell-design.md`

---

## Chunk 1: Kernel/Exp Core Changes

These changes are prerequisites for devshell. They modify the core agent chain to expose the interfaces devshell needs, while maintaining backward compatibility with all existing callers.

### Task 1: KernelRunResult return type

Kernel currently returns `RunResultEvent`, discarding the message transcript. Add `KernelRunResult` and update `AgentKernel.run()` to return it. All existing callers (tests, Exp.run(), AgentRunService) access `result.event` or `result.reason` etc. — we need to update them.

**Files:**
- Modify: `matmaster/types/runtime.py` — add `KernelRunResult` dataclass
- Modify: `matmaster/core/agent.py:57,86,95,103,110,169,266-285` — change return type and `_finish()`
- Modify: `matmaster/core/exp.py:203` — `Exp.run()` returns `result.event`
- Modify: `src/services/agent_run_service.py:529` — access `result.event`
- Modify: `tests/matmaster/core/test_agent.py` — all `result.xxx` → `result.event.xxx` or `result.xxx`
- Modify: `tests/matmaster/integration/test_e2e_minimal.py:71-73`
- Modify: `tests/matmaster/integration/test_e2e_mat_master.py:210,234,262`
- Modify: `tests/matmaster/integration/test_pipeline_alignment.py:120-122`
- Modify: `tests/matmaster/integration/test_upstream_scenarios.py:144,164`
- Test: `tests/matmaster/types/test_runtime.py` — add KernelRunResult test
- Test: `tests/matmaster/core/test_agent.py` — add test for messages in result

- [ ] **Step 1: Write test for KernelRunResult**

In `tests/matmaster/types/test_runtime.py`, add:

```python
from matmaster.types.runtime import KernelRunResult
from matmaster.types.events import RunResultEvent

class TestKernelRunResult:
    def test_frozen_construction(self) -> None:
        event = RunResultEvent(source="agent", status="completed", reason="natural")
        result = KernelRunResult(event=event, messages=[])
        assert result.event is event
        assert result.messages == []

    def test_messages_preserved(self) -> None:
        from matmaster.types.messages import UserMessage, AssistantMessage
        event = RunResultEvent(source="agent", status="completed", reason="natural")
        msgs = [UserMessage(content="hi"), AssistantMessage(content="hello")]
        result = KernelRunResult(event=event, messages=msgs)
        assert len(result.messages) == 2
        assert result.messages[0].content == "hi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/types/test_runtime.py::TestKernelRunResult -v`
Expected: FAIL — `ImportError: cannot import name 'KernelRunResult'`

- [ ] **Step 3: Add KernelRunResult to runtime.py**

In `matmaster/types/runtime.py`, add after the existing imports:

```python
from matmaster.types.messages import Message
```

Add after `AgentRuntime` class (end of file):

```python
@dataclass(frozen=True)
class KernelRunResult:
    """Return value of AgentKernel.run().

    Bundles the terminal event with the full message transcript,
    enabling callers to extract conversation history for multi-turn.
    """

    event: RunResultEvent
    messages: list[Message]
```

Also need to import `RunResultEvent`:

```python
from matmaster.types.events import RunResultEvent
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/types/test_runtime.py::TestKernelRunResult -v`
Expected: PASS

- [ ] **Step 5: Write test for kernel returning KernelRunResult with messages**

In `tests/matmaster/core/test_agent.py`, add a new test class:

```python
class TestKernelRunResultMessages:
    """kernel.run() returns KernelRunResult with message transcript."""

    def test_natural_finish_returns_messages(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.types.runtime import KernelRunResult

        provider = StreamingProvider([
            StreamChunk(content="Hello"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = kernel.run(spec, "test task")

        assert isinstance(result, KernelRunResult)
        assert result.event.reason == "natural"
        # Messages: [SystemMessage, UserMessage, AssistantMessage]
        assert len(result.messages) == 3
        assert isinstance(result.messages[0], SystemMessage)
        assert isinstance(result.messages[1], UserMessage)
        assert isinstance(result.messages[2], AssistantMessage)
        assert result.messages[2].content == "Hello"

    def test_tool_cycle_returns_all_messages(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.types.runtime import KernelRunResult

        tc = ToolCallData(id="tc-1", name="my_tool", arguments={"key": "val"})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="final"
        )
        tool_reg, _ = _make_tool_registry(["my_tool"], result="tool output")
        spec = _make_spec(provider=provider, tool_registry=tool_reg, max_turns=10)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert isinstance(result, KernelRunResult)
        assert result.event.reason == "natural"
        # Messages: System, User, Assistant(tool_calls), ToolMessage, Assistant(final)
        assert len(result.messages) == 5
        assert isinstance(result.messages[2], AssistantMessage)
        assert result.messages[2].tool_calls is not None
        assert isinstance(result.messages[3], ToolMessage)
        assert isinstance(result.messages[4], AssistantMessage)
        assert result.messages[4].content == "final"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/core/test_agent.py::TestKernelRunResultMessages -v`
Expected: FAIL — kernel.run() still returns RunResultEvent

- [ ] **Step 7: Update AgentKernel to return KernelRunResult**

In `matmaster/core/agent.py`:

Add import at top:
```python
from matmaster.types.runtime import KernelRunResult
```

Change `run()` return type annotation (line 57):
```python
) -> KernelRunResult:
```

Change docstring "Returns" line:
```python
Returns KernelRunResult with event and message transcript.
```

Change all `return self._finish(...)` calls in `run()` to keep using `_finish()` but it now returns `KernelRunResult`.

Update `_finish()` (line 266-285):
```python
@staticmethod
def _finish(
    spec: AgentRuntimeSpec,
    messages: list[Message],
    reason: str,
    final_content: str | None = None,
) -> KernelRunResult:
    """Unified exit path -- all termination goes through here."""
    if reason == "cancelled":
        status = "cancelled"
    elif reason == "invalid_finish":
        status = "failed"
    else:
        status = "completed"
    event = RunResultEvent(
        source="agent",
        status=status,
        reason=reason,
        final_content=final_content,
    )
    return KernelRunResult(event=event, messages=list(messages))
```

- [ ] **Step 8: Run new tests to verify they pass**

Run: `uv run pytest tests/matmaster/core/test_agent.py::TestKernelRunResultMessages -v`
Expected: PASS

- [ ] **Step 9: Update existing test assertions**

All existing tests that do `result.reason`, `result.status`, `result.final_content` now need `result.event.reason` etc. And `isinstance(result, FinishEvent)` → `isinstance(result, KernelRunResult)` or `isinstance(result.event, FinishEvent)`.

In `tests/matmaster/core/test_agent.py`, update every test that accesses result fields:

- `result.reason` → `result.event.reason`
- `result.final_content` → `result.event.final_content`
- `result.status` → `result.event.status`
- `isinstance(result, FinishEvent)` → `isinstance(result.event, FinishEvent)`

Similarly in:
- `tests/matmaster/integration/test_e2e_minimal.py` — `finish.reason` → `finish.event.reason`, `isinstance(finish, FinishEvent)` → `isinstance(finish.event, FinishEvent)`
- `tests/matmaster/integration/test_e2e_mat_master.py` — same pattern
- `tests/matmaster/integration/test_pipeline_alignment.py` — same pattern
- `tests/matmaster/integration/test_upstream_scenarios.py` — same pattern

- [ ] **Step 10: Update Exp.run() to return RunResultEvent (backward compat)**

In `matmaster/core/exp.py:203`, change:
```python
return runtime.kernel.run(
    runtime.spec, task, history=history, stop_event=stop_event
)
```
to:
```python
result = runtime.kernel.run(
    runtime.spec, task, history=history, stop_event=stop_event
)
return result.event
```

`Exp.run()` keeps returning `RunResultEvent` — it's a convenience wrapper that doesn't need messages.

- [ ] **Step 11: Update AgentRunService to use result.event**

In `src/services/agent_run_service.py:529`, change:
```python
run_result_event = runtime.kernel.run(
```
to extract event:
```python
kernel_result = runtime.kernel.run(
    spec=spec,
    task=user_prompt,
    history=history,
    stop_event=stop_event,
)
run_result_event = kernel_result.event
```

- [ ] **Step 12: Run full test suite to verify backward compatibility**

Run: `uv run pytest tests/matmaster/ -v`
Expected: ALL PASS

- [ ] **Step 13: Commit**

```bash
git add matmaster/types/runtime.py matmaster/core/agent.py matmaster/core/exp.py \
    src/services/agent_run_service.py tests/
git commit -m "refactor(core): return KernelRunResult from AgentKernel.run()

AgentKernel.run() now returns KernelRunResult(event, messages) instead
of bare RunResultEvent. This exposes the message transcript for
multi-turn history accumulation. Exp.run() still returns RunResultEvent
for backward compatibility."
```

### Task 2: Guard blocked hook point

Add `on_guard_blocked` to the Hook protocol so guard denials are observable.

**Files:**
- Modify: `matmaster/core/hooks.py` — add `on_guard_blocked` to Protocol, BaseHook, and `run_guard_blocked` helper
- Modify: `matmaster/core/agent.py:129-141` — call `run_guard_blocked` in guard deny branch
- Test: `tests/matmaster/core/test_hooks.py` — test `run_guard_blocked`
- Test: `tests/matmaster/core/test_agent.py` — test guard block triggers hook

- [ ] **Step 1: Write test for run_guard_blocked helper**

In `tests/matmaster/core/test_hooks.py`, add:

```python
from matmaster.types.guards import GuardResult

class TestRunGuardBlocked:
    def test_calls_all_hooks(self) -> None:
        from matmaster.core.hooks import run_guard_blocked, BaseHook
        from matmaster.types.messages import ToolCallData

        class RecordingGuardHook(BaseHook):
            def __init__(self):
                self.calls = []
            def on_guard_blocked(self, tool_call, result):
                self.calls.append((tool_call.name, result.reason))

        h1 = RecordingGuardHook()
        h2 = RecordingGuardHook()
        tc = ToolCallData(id="tc-1", name="dangerous", arguments={})
        gr = GuardResult(allowed=False, reason="forbidden")

        run_guard_blocked([h1, h2], tc, gr)

        assert len(h1.calls) == 1
        assert h1.calls[0] == ("dangerous", "forbidden")
        assert len(h2.calls) == 1

    def test_no_hooks_no_error(self) -> None:
        from matmaster.core.hooks import run_guard_blocked
        from matmaster.types.messages import ToolCallData

        tc = ToolCallData(id="tc-1", name="tool", arguments={})
        gr = GuardResult(allowed=False, reason="blocked")
        run_guard_blocked([], tc, gr)  # Should not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/core/test_hooks.py::TestRunGuardBlocked -v`
Expected: FAIL — `ImportError: cannot import name 'run_guard_blocked'`

- [ ] **Step 3: Add on_guard_blocked to Hook protocol and helpers**

In `matmaster/core/hooks.py`:

Add import:
```python
from matmaster.types.guards import GuardResult
```

Add to `Hook` Protocol class (after `on_stream_chunk`):
```python
def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None: ...
```

Add to `BaseHook` class:
```python
def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None:
    """Default: no-op observation."""
```

Add helper function (after `run_on_stream_chunk`):
```python
def run_guard_blocked(
    hooks: list[Hook], tool_call: ToolCallData, result: GuardResult
) -> None:
    """Run on_guard_blocked on all hooks (observation, no short-circuit).

    Uses getattr for backward compatibility with Hook implementations
    that predate the on_guard_blocked addition.
    """
    for hook in hooks:
        fn = getattr(hook, "on_guard_blocked", None)
        if fn is not None:
            fn(tool_call, result)
```

Update module docstring to mention the new hook point.

Also add `on_guard_blocked` no-op to `EventEmitterHook` to satisfy the updated Protocol:
```python
def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None:
    """Guard blocks are not emitted to the bus by default."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_hooks.py::TestRunGuardBlocked -v`
Expected: PASS

- [ ] **Step 5: Write test for kernel calling on_guard_blocked**

In `tests/matmaster/core/test_agent.py`, add to `TestGuardBlocks`:

```python
def test_guard_block_triggers_hook(self) -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.core.hooks import BaseHook
    from matmaster.types.guards import GuardResult

    class GuardBlockRecorder(BaseHook):
        def __init__(self):
            self.blocked = []
        def on_guard_blocked(self, tool_call, result):
            self.blocked.append((tool_call.name, result.reason))

    tc = ToolCallData(id="tc-1", name="bad_tool", arguments={})
    provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=1, final_content="ok")
    recorder = GuardBlockRecorder()
    tool_reg, _ = _make_tool_registry(["bad_tool"])
    spec = _make_spec(
        provider=provider,
        tool_registry=tool_reg,
        guards=[DenyGuard("bad_tool", reason="no access")],
        hooks=[recorder],
        max_turns=5,
    )
    kernel = AgentKernel()
    kernel.run(spec, "test")

    assert len(recorder.blocked) == 1
    assert recorder.blocked[0] == ("bad_tool", "no access")
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/core/test_agent.py::TestGuardBlocks::test_guard_block_triggers_hook -v`
Expected: FAIL — hook.on_guard_blocked never called

- [ ] **Step 7: Add run_guard_blocked call in kernel guard deny branch**

In `matmaster/core/agent.py`, add import:
```python
from matmaster.core.hooks import (
    ...
    run_guard_blocked,
)
```

At line 129, before the `blocked_content` line, add:
```python
if not guard_result.allowed:
    run_guard_blocked(spec.hooks, tc, guard_result)
    # Blocked: ToolMessage error response
    ...
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_agent.py::TestGuardBlocks -v`
Expected: ALL PASS

- [ ] **Step 9: Run full hook and agent test suites**

Run: `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/core/test_agent.py -v`
Expected: ALL PASS

- [ ] **Step 10: Commit**

```bash
git add matmaster/core/hooks.py matmaster/core/agent.py tests/matmaster/core/
git commit -m "feat(core): add on_guard_blocked hook point

Guard denials now trigger on_guard_blocked on all hooks before
appending the BLOCKED ToolMessage. Observation-only, no short-circuit."
```

### Task 3: Tool execution exception safety

Wrap tool execution in try/except so exceptions become error strings instead of crashing the kernel.

**Files:**
- Modify: `matmaster/core/agent.py:155-163` — wrap in try/except
- Test: `tests/matmaster/core/test_agent.py` — test tool exception handling

- [ ] **Step 1: Write test for tool execution exception**

In `tests/matmaster/core/test_agent.py`, add:

```python
class TestToolExecutionException:
    """Tool that raises exception -> error ToolMessage, run continues."""

    def test_tool_exception_becomes_error_message(self) -> None:
        from matmaster.core.agent import AgentKernel

        class ExplodingTool:
            @property
            def name(self) -> str:
                return "boom"

            @property
            def description(self) -> str:
                return "explodes"

            @property
            def json_schema(self) -> dict[str, Any]:
                return {"type": "object", "properties": {}}

            def execute(self, arguments: dict[str, Any]) -> str:
                raise RuntimeError("kaboom!")

        registry = ToolRegistry()
        registry.register(ExplodingTool(), source="test")

        tc = ToolCallData(id="tc-1", name="boom", arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="recovered"
        )
        spec = _make_spec(provider=provider, tool_registry=registry, max_turns=5)
        kernel = AgentKernel()
        result = kernel.run(spec, "test")

        assert result.event.reason == "natural"
        assert result.event.final_content == "recovered"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/core/test_agent.py::TestToolExecutionException -v`
Expected: FAIL — `RuntimeError: kaboom!` propagates

- [ ] **Step 3: Add try/except around tool execution**

In `matmaster/core/agent.py`, replace lines 155-163:

```python
# Tool execution
try:
    result = spec.tool_registry.execute(tc.name, tc.arguments)
except Exception as e:
    result = f"Error executing tool '{tc.name}': {type(e).__name__}: {e}"
    logger.exception("Tool execution failed: %s", tc.name)
messages.append(
    ToolMessage(
        tool_call_id=tc.id,
        tool_name=tc.name,
        content=str(result),
    )
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_agent.py::TestToolExecutionException -v`
Expected: PASS

- [ ] **Step 5: Run full agent test suite**

Run: `uv run pytest tests/matmaster/core/test_agent.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent.py
git commit -m "fix(core): catch tool execution exceptions in kernel

Tool exceptions are now caught and converted to error strings in
ToolMessage instead of crashing the kernel. Benefits all callers."
```

### Task 4: Identity override in Exp.build_runtime()

Forward `identity` from config to `ContextBuilder.build()`.

**Files:**
- Modify: `matmaster/core/exp.py:148-149` — read identity from config, pass to builder
- Test: `tests/matmaster/core/test_exp.py` — test identity forwarding

- [ ] **Step 1: Write test for identity override**

In `tests/matmaster/core/test_exp.py`, add:

```python
class TestIdentityOverride:
    def test_identity_from_config(self) -> None:
        config = {"name": "test", "identity": "I am a materials scientist.", "tools": {"builtin": []}}
        exp = Exp(config)
        ctx = _make_ctx(with_llm=True)
        runtime = exp.build_runtime(ctx)

        assert "I am a materials scientist." in runtime.spec.system_prompt

    def test_default_identity_when_not_set(self) -> None:
        config = {"name": "test", "tools": {"builtin": []}}
        exp = Exp(config)
        ctx = _make_ctx(with_llm=True)
        runtime = exp.build_runtime(ctx)

        assert "helpful AI assistant" in runtime.spec.system_prompt
```

- [ ] **Step 2: Run test to verify current behavior**

Run: `uv run pytest tests/matmaster/core/test_exp.py::TestIdentityOverride -v`
Expected: `test_identity_from_config` FAILS (identity not forwarded), `test_default_identity_when_not_set` PASSES

- [ ] **Step 3: Forward identity in Exp.build_runtime()**

In `matmaster/core/exp.py`, change line 148-149:

```python
# 5. Build system_prompt via ContextBuilder
builder = ContextBuilder()
identity = self._config.get("identity")
system_prompt = builder.build(ctx, registry, mode=spec.mode, identity=identity)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_exp.py::TestIdentityOverride -v`
Expected: PASS

- [ ] **Step 5: Run full exp test suite**

Run: `uv run pytest tests/matmaster/core/test_exp.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/exp.py tests/matmaster/core/test_exp.py
git commit -m "feat(core): forward identity config to ContextBuilder

Exp.build_runtime() now reads 'identity' from config and passes it
to ContextBuilder.build(), enabling custom identity section override."
```

### Task 5: Packaging fix

Add `matmaster` to wheel packages.

**Files:**
- Modify: `pyproject.toml:57`

- [ ] **Step 1: Update pyproject.toml**

In `pyproject.toml`, line 57, change:
```toml
packages = ["evomaster", "playground"]
```
to:
```toml
packages = ["evomaster", "playground", "matmaster"]
```

Note: `mm-devshell` script entry is added in Task 11 (Chunk 3) after the module exists.

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "build: add matmaster to wheel packages and mm-devshell entry point"
```

---

## Chunk 2: DevShell Config and Runner

Core devshell modules that don't involve terminal I/O.

### Task 6: DevConfig model

**Files:**
- Create: `matmaster/devshell/__init__.py`
- Create: `matmaster/devshell/config.py`
- Create: `tests/matmaster/devshell/__init__.py`
- Test: `tests/matmaster/devshell/test_config.py`

- [ ] **Step 1: Write tests for DevConfig**

Create `tests/matmaster/devshell/__init__.py` (empty) and `tests/matmaster/devshell/test_config.py`:

```python
"""Tests for DevConfig model and loading."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


class TestDevConfig:
    def test_defaults(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        assert cfg.llm.model == "gpt-4o"
        assert cfg.agent.mode == "direct"
        assert cfg.agent.max_turns == 20
        assert cfg.session.type == "local"
        assert cfg.tools.builtin == ["*"]

    def test_from_dict(self) -> None:
        from matmaster.devshell.config import DevConfig

        data = {
            "llm": {"model": "gpt-3.5-turbo", "api_key": "sk-test"},
            "agent": {"max_turns": 5},
        }
        cfg = DevConfig.model_validate(data)
        assert cfg.llm.model == "gpt-3.5-turbo"
        assert cfg.llm.api_key == "sk-test"
        assert cfg.agent.max_turns == 5

    def test_identity_optional(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        assert cfg.agent.identity is None

        cfg2 = DevConfig.model_validate({"agent": {"identity": "I am a scientist."}})
        assert cfg2.agent.identity == "I am a scientist."


class TestLoadDevConfig:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        from matmaster.devshell.config import load_dev_config

        yaml_content = """
llm:
  model: gpt-4o-mini
  api_key: sk-yaml
agent:
  max_turns: 10
  identity: "Test bot"
"""
        config_file = tmp_path / "dev.yaml"
        config_file.write_text(yaml_content)
        cfg = load_dev_config(config_file)
        assert cfg.llm.model == "gpt-4o-mini"
        assert cfg.llm.api_key == "sk-yaml"
        assert cfg.agent.max_turns == 10
        assert cfg.agent.identity == "Test bot"

    def test_env_var_expansion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from matmaster.devshell.config import load_dev_config

        monkeypatch.setenv("TEST_API_KEY", "sk-from-env")
        yaml_content = """
llm:
  api_key: ${TEST_API_KEY}
"""
        config_file = tmp_path / "dev.yaml"
        config_file.write_text(yaml_content)
        cfg = load_dev_config(config_file)
        assert cfg.llm.api_key == "sk-from-env"

    def test_file_not_found(self) -> None:
        from matmaster.devshell.config import load_dev_config

        with pytest.raises(FileNotFoundError):
            load_dev_config(Path("/nonexistent/dev.yaml"))

    def test_defaults_when_no_file(self) -> None:
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        assert cfg.llm.api_key == ""
        assert cfg.agent.name == "general"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/devshell/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'matmaster.devshell'`

- [ ] **Step 3: Create devshell package and config module**

Create `matmaster/devshell/__init__.py` (empty).

Create `matmaster/devshell/config.py`:

```python
"""DevConfig model and YAML loading for mm-devshell."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from matmaster.config.loader import _expand_env_vars


class LLMConfig(BaseModel):
    """LLM connection settings."""

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int | None = None


class AgentConfig(BaseModel):
    """Agent behavior settings."""

    name: str = "general"
    mode: str = "direct"
    max_turns: int = 20
    identity: str | None = None


class SessionConfig(BaseModel):
    """Session type selection."""

    type: str = "local"


class ToolsConfig(BaseModel):
    """Tool registration settings."""

    builtin: list[str] = Field(default_factory=lambda: ["*"])


class DevConfig(BaseModel):
    """Top-level devshell configuration."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)


def load_dev_config(path: Path) -> DevConfig:
    """Load DevConfig from a YAML file with env var expansion."""
    import yaml

    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Config file not found: {resolved}")

    with open(resolved) as f:
        raw = yaml.safe_load(f) or {}

    expanded = _expand_env_vars(raw)
    return DevConfig.model_validate(expanded)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/devshell/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/devshell/ tests/matmaster/devshell/
git commit -m "feat(devshell): add DevConfig model and YAML loading"
```

### Task 7: DevStreamHook

**Files:**
- Create: `matmaster/devshell/stream_hook.py`
- Test: `tests/matmaster/devshell/test_stream_hook.py`

- [ ] **Step 1: Write tests for DevStreamHook**

Create `tests/matmaster/devshell/test_stream_hook.py`:

```python
"""Tests for DevStreamHook terminal output formatting."""
from __future__ import annotations

import io
from typing import Any

from matmaster.types.messages import StreamChunk, ToolCallData
from matmaster.types.guards import GuardResult


class TestDevStreamHook:
    def _make_hook(self, verbose: bool = False) -> tuple:
        from matmaster.devshell.stream_hook import DevStreamHook

        buf = io.StringIO()
        hook = DevStreamHook(output=buf, verbose=verbose)
        return hook, buf

    def test_stream_chunk_content(self) -> None:
        hook, buf = self._make_hook()
        chunk = StreamChunk(content="Hello", stream_state="streaming", stream_id="s1")
        hook.on_stream_chunk(chunk)
        assert buf.getvalue() == "Hello"

    def test_stream_chunk_start_end_no_content(self) -> None:
        hook, buf = self._make_hook()
        hook.on_stream_chunk(StreamChunk(stream_state="start", stream_id="s1"))
        hook.on_stream_chunk(StreamChunk(stream_state="end", stream_id="s1"))
        # end should add newline
        assert buf.getvalue() == "\n"

    def test_pre_tool_call_display(self) -> None:
        from matmaster.core.hooks import HookAction

        hook, buf = self._make_hook()
        tc = ToolCallData(id="tc-1", name="bash", arguments={"command": "ls"})
        action = hook.pre_tool_call(tc)

        assert action == HookAction.CONTINUE
        output = buf.getvalue()
        assert "tool_call: bash" in output
        assert "command" in output

    def test_post_tool_call_success(self) -> None:
        hook, buf = self._make_hook()
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        hook.post_tool_call(tc, "file1.py\nfile2.py")

        output = buf.getvalue()
        assert "tool_result:" in output
        assert "file1.py" in output

    def test_post_tool_call_truncation(self) -> None:
        hook, buf = self._make_hook()
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        long_result = "x" * 2000
        hook.post_tool_call(tc, long_result)

        output = buf.getvalue()
        assert "..." in output or len(output) < 2000

    def test_guard_blocked(self) -> None:
        hook, buf = self._make_hook()
        tc = ToolCallData(id="tc-1", name="rm_rf", arguments={})
        gr = GuardResult(allowed=False, reason="dangerous operation")
        hook.on_guard_blocked(tc, gr)

        output = buf.getvalue()
        assert "guard_blocked:" in output
        assert "dangerous operation" in output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/devshell/test_stream_hook.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement DevStreamHook**

Create `matmaster/devshell/stream_hook.py`:

```python
"""DevStreamHook -- real-time terminal output for devshell REPL."""
from __future__ import annotations

import io
import json
import sys
from typing import TextIO

from matmaster.core.hooks import BaseHook, HookAction
from matmaster.types.guards import GuardResult
from matmaster.types.messages import StreamChunk, ToolCallData

_MAX_RESULT_LEN = 1000


class DevStreamHook(BaseHook):
    """Hook that formats kernel events for terminal display.

    Writes directly to the provided output stream (default: sys.stdout).
    """

    def __init__(
        self,
        output: TextIO | None = None,
        verbose: bool = False,
    ) -> None:
        self._out = output or sys.stdout
        self._verbose = verbose

    def on_stream_chunk(self, chunk: StreamChunk) -> None:
        if chunk.stream_state == "start":
            return
        if chunk.stream_state == "end":
            self._out.write("\n")
            self._out.flush()
            return
        if chunk.content:
            self._out.write(chunk.content)
            self._out.flush()

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        args_str = json.dumps(tool_call.arguments, ensure_ascii=False, indent=2)
        self._out.write(f"\n\U0001f4ce tool_call: {tool_call.name}\n")
        for line in args_str.split("\n"):
            self._out.write(f"   {line}\n")
        self._out.flush()
        return HookAction.CONTINUE

    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        is_error = result.startswith("Error executing tool")
        prefix = "\u274c tool_error:" if is_error else "\u2705 tool_result:"
        display = result if len(result) <= _MAX_RESULT_LEN else result[:_MAX_RESULT_LEN] + "..."
        self._out.write(f"\n{prefix} {display}\n\n")
        self._out.flush()

    def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None:
        self._out.write(f"\n\U0001f6e1\ufe0f guard_blocked: {result.reason}\n\n")
        self._out.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/devshell/test_stream_hook.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/devshell/stream_hook.py tests/matmaster/devshell/test_stream_hook.py
git commit -m "feat(devshell): add DevStreamHook for terminal output"
```

### Task 8: EventLogger

**Files:**
- Create: `matmaster/devshell/event_logger.py`
- Test: `tests/matmaster/devshell/test_event_logger.py`

- [ ] **Step 1: Write tests for EventLogger**

Create `tests/matmaster/devshell/test_event_logger.py`:

```python
"""Tests for EventLogger JSONL persistence."""
from __future__ import annotations

import json
from pathlib import Path

from matmaster.core.bus import MessageBus
from matmaster.types.events import (
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    RunResultEvent,
)


class TestEventLogger:
    def test_writes_jsonl(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-001")

        logger.log_event(ToolCallEvent(
            source="test", call_id="tc-1", tool_name="bash",
            arguments={"command": "ls"},
        ))
        logger.log_event(ToolResultEvent(
            source="test", call_id="tc-1", tool_name="bash",
            result="file1.py",
        ))
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

        rec1 = json.loads(lines[0])
        assert rec1["type"] == "tool_call"
        assert rec1["tool"] == "bash"
        assert rec1["run_id"] == "run-001"

        rec2 = json.loads(lines[1])
        assert rec2["type"] == "tool_result"
        assert rec2["tool"] == "bash"

    def test_thought_streaming_merged(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-001")

        logger.log_event(ThoughtEvent(source="test", content="", stream_state="start", stream_id="s1"))
        logger.log_event(ThoughtEvent(source="test", content="Hel", stream_state="streaming", stream_id="s1"))
        logger.log_event(ThoughtEvent(source="test", content="lo", stream_state="streaming", stream_id="s1"))
        logger.log_event(ThoughtEvent(source="test", content="", stream_state="end", stream_id="s1"))
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1  # Merged into single record
        rec = json.loads(lines[0])
        assert rec["type"] == "thought"
        assert rec["content"] == "Hello"

    def test_skips_assistant_state(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger
        from matmaster.types.events import AssistantStateEvent

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-001")

        logger.log_event(AssistantStateEvent(source="test", state={}))
        logger.close()

        assert not log_file.exists() or log_file.read_text().strip() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/devshell/test_event_logger.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement EventLogger**

Create `matmaster/devshell/event_logger.py`:

```python
"""EventLogger -- JSONL event persistence for devshell."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from matmaster.types.events import (
    AssistantStateEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    RunResultEvent,
)

logger = logging.getLogger(__name__)

# Event types to skip
_SKIP_TYPES = {"assistant_state"}


class EventLogger:
    """Writes bus events to a JSONL file.

    Merges streaming ThoughtEvents (start/streaming/end) into a single record.
    Skips assistant_state events.
    """

    def __init__(self, log_file: Path, *, run_id: str) -> None:
        self._log_file = log_file
        self._run_id = run_id
        self._fh: TextIO | None = None
        self._thought_buffer: dict[str, list[str]] = {}  # stream_id -> content parts

    def _ensure_open(self) -> TextIO:
        if self._fh is None:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._log_file, "a", encoding="utf-8")
        return self._fh

    def log_event(self, event: Any) -> None:
        """Process a single bus event."""
        try:
            self._log_event_inner(event)
        except Exception:
            logger.warning("EventLogger failed to write event", exc_info=True)

    def _log_event_inner(self, event: Any) -> None:
        event_type = getattr(event, "type", None)
        if event_type in _SKIP_TYPES:
            return

        if isinstance(event, ThoughtEvent):
            self._handle_thought(event)
            return

        record = self._event_to_record(event)
        if record:
            self._write_record(record)

    def _handle_thought(self, event: ThoughtEvent) -> None:
        sid = event.stream_id or "default"
        if event.stream_state == "start":
            self._thought_buffer[sid] = []
        elif event.stream_state == "streaming":
            self._thought_buffer.setdefault(sid, []).append(event.content)
        elif event.stream_state == "end":
            parts = self._thought_buffer.pop(sid, [])
            content = "".join(parts)
            if content:
                self._write_record({
                    "type": "thought",
                    "content": content,
                })
        else:
            # Non-streaming thought
            if event.content:
                self._write_record({
                    "type": "thought",
                    "content": event.content,
                })

    def _event_to_record(self, event: Any) -> dict[str, Any] | None:
        if isinstance(event, ToolCallEvent):
            return {
                "type": "tool_call",
                "tool": event.tool_name,
                "call_id": event.call_id,
                "args": event.arguments,
            }
        if isinstance(event, ToolResultEvent):
            return {
                "type": "tool_result",
                "tool": event.tool_name,
                "call_id": event.call_id,
                "content": event.result,
                "success": not event.result.startswith("Error"),
            }
        if isinstance(event, RunResultEvent):
            return {
                "type": "run_result",
                "status": event.status,
                "reason": event.reason,
            }
        return None

    def _write_record(self, record: dict[str, Any]) -> None:
        record["ts"] = datetime.now(timezone.utc).isoformat()
        record["run_id"] = self._run_id
        fh = self._ensure_open()
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()

    def set_run_id(self, run_id: str) -> None:
        """Update the run_id for subsequent records."""
        self._run_id = run_id

    def close(self) -> None:
        """Flush and close the log file."""
        if self._fh is not None:
            self._fh.close()
            self._fh = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/devshell/test_event_logger.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/devshell/event_logger.py tests/matmaster/devshell/test_event_logger.py
git commit -m "feat(devshell): add EventLogger for JSONL persistence"
```

### Task 9: DevRunner

**Files:**
- Create: `matmaster/devshell/runner.py`
- Test: `tests/matmaster/devshell/test_runner.py`

- [ ] **Step 1: Write tests for DevRunner**

Create `tests/matmaster/devshell/test_runner.py`:

```python
"""Tests for DevRunner -- per-run assembly and history accumulation."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch, MagicMock

from matmaster.types.messages import StreamChunk, ToolCallData


class MockProvider:
    def chat(self, messages, tools=None):
        from matmaster.types.messages import LLMResponse
        return LLMResponse(content="mock", finish_reason="stop")

    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None) -> Iterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")


class TestDevRunner:
    def _make_runner(self, tmp_path: Path) -> Any:
        from matmaster.devshell.config import DevConfig
        from matmaster.devshell.runner import DevRunner

        workdir = tmp_path / "workspace"
        workdir.mkdir()
        config = DevConfig()

        return DevRunner(
            config=config,
            workdir=workdir,
            llm_provider=MockProvider(),
        )

    def test_single_run(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)
        result = runner.run("hello")

        assert result.event.reason == "natural"
        assert result.event.final_content == "hello"

    def test_history_accumulates(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)

        result1 = runner.run("first question")
        assert len(runner.history) > 0

        history_before = len(runner.history)
        result2 = runner.run("second question")
        assert len(runner.history) > history_before

    def test_cleanup_called(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)
        # Run should not leak resources
        runner.run("test")
        # If we get here without error, cleanup worked

    def test_stop_event(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)
        stop = threading.Event()
        stop.set()
        result = runner.run("test", stop_event=stop)
        assert result.event.reason == "cancelled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/devshell/test_runner.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement DevRunner**

Create `matmaster/devshell/runner.py`:

```python
"""DevRunner -- per-run assembly mirroring AgentRunService pattern."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from matmaster.core.bus import MessageBus
from matmaster.core.exp import Exp
from matmaster.devshell.config import DevConfig
from matmaster.devshell.stream_hook import DevStreamHook
from matmaster.types.context import PlaygroundContext
from matmaster.types.messages import Message, SystemMessage, UserMessage
from matmaster.types.runtime import KernelRunResult

logger = logging.getLogger(__name__)


class DevRunner:
    """Per-run assembly: build_runtime -> inject hooks -> kernel.run -> history.

    Mirrors the split-call pattern of AgentRunService. REPL-agnostic:
    accepts task string, returns KernelRunResult.
    """

    def __init__(
        self,
        *,
        config: DevConfig,
        workdir: Path,
        llm_provider: Any,
        stream_hook: DevStreamHook | None = None,
    ) -> None:
        self._config = config
        self._workdir = workdir
        self._llm_provider = llm_provider
        self._stream_hook = stream_hook or DevStreamHook()

        # Build PlaygroundContext
        session = self._create_session(config, workdir)
        cache_area = workdir / ".cache"
        cache_area.mkdir(parents=True, exist_ok=True)

        self._pg_ctx = PlaygroundContext(
            workdir=workdir,
            session_type=config.session.type,
            cache_area=cache_area,
            session=session,
            llm_provider=llm_provider,
        )

        # Exp config dict
        self._exp_config = self._build_exp_config(config)

        # Multi-turn history
        self.history: list[Message] = []

    @staticmethod
    def _create_session(config: DevConfig, workdir: Path) -> Any:
        """Create and open a session based on config."""
        from evomaster.agent.session.local import LocalSession

        session = LocalSession()
        # Set workspace BEFORE open (mirrors Playground line 90-96)
        session.config.workspace_path = str(workdir)
        session.open()
        return session

    @staticmethod
    def _build_exp_config(config: DevConfig) -> dict[str, Any]:
        """Convert DevConfig to Exp config dict."""
        exp_cfg: dict[str, Any] = {
            "name": config.agent.name,
            "mode": config.agent.mode,
            "max_turns": config.agent.max_turns,
            "tools": {"builtin": config.tools.builtin},
        }
        if config.agent.identity is not None:
            exp_cfg["identity"] = config.agent.identity
        return exp_cfg

    def run(
        self,
        task: str,
        *,
        stop_event: threading.Event | None = None,
        bus: MessageBus | None = None,
    ) -> KernelRunResult:
        """Execute a single agent run.

        Returns KernelRunResult with event and message transcript.
        Appends run messages (excluding System/User prompt) to history.
        """
        exp = Exp(self._exp_config)
        runtime = exp.build_runtime(self._pg_ctx, bus=bus)

        # Inject DevStreamHook (same pattern as AgentRunService:512)
        spec = runtime.spec.model_copy(
            update={"hooks": [*runtime.spec.hooks, self._stream_hook]}
        )

        try:
            result = runtime.kernel.run(
                spec, task, history=self.history, stop_event=stop_event
            )
            # Extract new messages (skip SystemMessage and initial history + UserMessage)
            # Only accumulate history for non-cancelled runs to avoid dangling UserMessages
            if result.event.status != "cancelled":
                skip_count = 1 + len(self.history) + 1  # System + history + User
                new_messages = result.messages[skip_count:]
                self.history.append(UserMessage(content=task))
                self.history.extend(new_messages)
            return result
        finally:
            runtime.cleanup()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/devshell/test_runner.py -v`
Expected: ALL PASS (or adjust if LocalSession import needs mocking)

Note: If `evomaster.agent.session.local.LocalSession` is not available in test env, the `_create_session` method will need to be mocked. In that case, update the test:

```python
def _make_runner(self, tmp_path: Path) -> Any:
    from matmaster.devshell.config import DevConfig
    from matmaster.devshell.runner import DevRunner

    workdir = tmp_path / "workspace"
    workdir.mkdir()
    config = DevConfig()

    with patch("matmaster.devshell.runner.DevRunner._create_session") as mock_session:
        mock_session.return_value = MagicMock()
        return DevRunner(
            config=config,
            workdir=workdir,
            llm_provider=MockProvider(),
        )
```

- [ ] **Step 5: Run all devshell tests**

Run: `uv run pytest tests/matmaster/devshell/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/devshell/runner.py tests/matmaster/devshell/test_runner.py
git commit -m "feat(devshell): add DevRunner with history accumulation"
```

---

## Chunk 3: CLI and REPL

### Task 10: REPL

**Files:**
- Create: `matmaster/devshell/repl.py`
- Test: `tests/matmaster/devshell/test_repl.py`

- [ ] **Step 1: Write tests for REPL builtin commands**

Create `tests/matmaster/devshell/test_repl.py`:

```python
"""Tests for REPL builtin command parsing and routing."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import io


class TestBuiltinCommands:
    def test_parse_help(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/help") == ("help", "")

    def test_parse_config(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/config") == ("config", "")

    def test_parse_tools(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/tools") == ("tools", "")

    def test_parse_verbose(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/verbose") == ("verbose", "")

    def test_parse_not_command(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("hello world") is None

    def test_parse_unknown_command(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/unknown") == ("unknown", "")


class TestFormatBanner:
    def test_banner_contains_model(self) -> None:
        from matmaster.devshell.repl import format_banner
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        banner = format_banner(cfg, workdir="/tmp/ws", log_dir="/tmp/logs")
        assert "gpt-4o" in banner
        assert "local" in banner
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/devshell/test_repl.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement REPL module**

Create `matmaster/devshell/repl.py`:

```python
"""REPL loop for mm-devshell."""
from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from matmaster.devshell.config import DevConfig
from matmaster.devshell.event_logger import EventLogger
from matmaster.devshell.runner import DevRunner
from matmaster.core.bus import MessageBus


BUILTIN_COMMANDS = {"help", "config", "tools", "clear", "history", "verbose"}

HELP_TEXT = """\
Builtin commands:
  /help     Show this help
  /config   Show current configuration
  /tools    List registered tools
  /clear    Clear screen
  /history  Show conversation history summary
  /verbose  Toggle verbose mode

Ctrl+C    Cancel current run
Ctrl+D    Exit"""


def parse_command(text: str) -> tuple[str, str] | None:
    """Parse a /command from input. Returns (cmd, args) or None if not a command."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped[1:].split(None, 1)
    cmd = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return (cmd, args)


def format_banner(
    config: DevConfig, workdir: str, log_dir: str
) -> str:
    """Format the startup banner."""
    return (
        f"MatMaster Dev Shell v0.1\n"
        f"Model: {config.llm.model} | Session: {config.session.type} | "
        f"Tools: builtin\n"
        f"Workdir: {workdir} | Logs: {log_dir}\n"
        f"Type /help for commands, Ctrl+C to cancel current run, Ctrl+D to exit."
    )


def run_repl(
    runner: DevRunner,
    config: DevConfig,
    *,
    log_dir: Path,
    verbose: bool = False,
) -> None:
    """Main REPL loop."""
    run_counter = 0

    # One EventLogger per session (spec: one file per REPL session)
    from datetime import datetime
    log_file = log_dir / f"events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    event_logger = EventLogger(log_file, run_id="run-000")

    print(format_banner(config, str(runner._workdir), str(log_dir)))
    print()

    while True:
        try:
            user_input = input(">>> ")
        except EOFError:
            print("\nBye.")
            break
        except KeyboardInterrupt:
            print("\nUse Ctrl+D to exit.")
            continue

        if not user_input.strip():
            continue

        # Check for builtin command
        cmd_result = parse_command(user_input)
        if cmd_result is not None:
            cmd, args = cmd_result
            if cmd == "help":
                print(HELP_TEXT)
            elif cmd == "config":
                _show_config(config)
            elif cmd == "tools":
                _show_tools(runner)
            elif cmd == "clear":
                os.system("clear" if os.name != "nt" else "cls")
            elif cmd == "history":
                _show_history(runner)
            elif cmd == "verbose":
                verbose = not verbose
                runner._stream_hook._verbose = verbose
                print(f"Verbose mode: {'on' if verbose else 'off'}")
            else:
                print(f"Unknown command: /{cmd}. Type /help for available commands.")
            continue

        # Agent run
        run_counter += 1
        run_id = f"run-{run_counter:03d}"

        event_logger.set_run_id(run_id)

        # Bus for event routing
        bus = MessageBus()
        stop_event = threading.Event()

        # SIGINT handler for cancellation
        original_handler = signal.getsignal(signal.SIGINT)

        def _sigint_handler(signum: int, frame: Any) -> None:
            stop_event.set()
            print("\n\nCancelling...")

        signal.signal(signal.SIGINT, _sigint_handler)

        try:
            # Run in background thread
            result_holder: list = []
            error_holder: list = []

            def _worker() -> None:
                try:
                    result = runner.run(user_input, stop_event=stop_event, bus=bus)
                    result_holder.append(result)
                except Exception as e:
                    error_holder.append(e)

            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()

            # Drain bus events to logger while worker runs
            import queue
            while worker.is_alive():
                try:
                    event = bus.get(timeout=0.1)
                    event_logger.log_event(event)
                except queue.Empty:
                    continue

            # Drain remaining events
            while True:
                try:
                    event = bus.get_nowait()
                    event_logger.log_event(event)
                except queue.Empty:
                    break

            worker.join()

            if error_holder:
                print(f"\n\U0001f4a5 error: {error_holder[0]}\n")
            elif result_holder:
                result = result_holder[0]
                # Log the run result event
                event_logger.log_event(result.event)

        finally:
            signal.signal(signal.SIGINT, original_handler)

    # Close session-level logger on REPL exit
    event_logger.close()


def _show_config(config: DevConfig) -> None:
    """Display current configuration."""
    print(f"LLM: model={config.llm.model}, base_url={config.llm.base_url}")
    print(f"Agent: name={config.agent.name}, mode={config.agent.mode}, max_turns={config.agent.max_turns}")
    print(f"Session: type={config.session.type}")
    print(f"Tools: builtin={config.tools.builtin}")
    if config.agent.identity:
        print(f"Identity: {config.agent.identity}")


def _show_tools(runner: DevRunner) -> None:
    """List registered tools (requires a build_runtime call)."""
    # Build a temporary runtime to inspect tools
    exp = Exp(runner._exp_config)
    runtime = exp.build_runtime(runner._pg_ctx)
    try:
        if runtime.spec.tool_registry:
            for tool in runtime.spec.tool_registry.all_tools:
                print(f"  - {tool.name}: {tool.description}")
        else:
            print("  No tools registered.")
    finally:
        runtime.cleanup()


def _show_history(runner: DevRunner) -> None:
    """Show conversation history summary."""
    if not runner.history:
        print("No conversation history.")
        return
    print(f"History: {len(runner.history)} messages")
    for i, msg in enumerate(runner.history):
        role = msg.role if hasattr(msg, "role") else type(msg).__name__
        content = getattr(msg, "content", "") or ""
        preview = content[:80] + "..." if len(content) > 80 else content
        print(f"  [{i}] {role}: {preview}")


# Needed for _show_tools
from matmaster.core.exp import Exp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/devshell/test_repl.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/devshell/repl.py tests/matmaster/devshell/test_repl.py
git commit -m "feat(devshell): add REPL loop with builtin commands"
```

### Task 11: CLI entry point

**Files:**
- Create: `matmaster/devshell/cli.py`
- Create: `matmaster/devshell/__main__.py`

- [ ] **Step 1: Write test for CLI arg parsing**

Add to `tests/matmaster/devshell/test_repl.py` (or create separate test file):

```python
class TestCliParsing:
    def test_parse_required_args(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(["--workdir", "/tmp/ws", "--log-dir", "/tmp/logs"])
        assert args.workdir == Path("/tmp/ws")
        assert args.log_dir == Path("/tmp/logs")

    def test_parse_optional_args(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args([
            "--workdir", "/tmp/ws",
            "--log-dir", "/tmp/logs",
            "--config", "custom.yaml",
            "--session", "docker",
            "--verbose",
        ])
        assert args.config == Path("custom.yaml")
        assert args.session == "docker"
        assert args.verbose is True

    def test_defaults(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(["--workdir", "/tmp/ws", "--log-dir", "/tmp/logs"])
        assert args.config is None
        assert args.session is None
        assert args.verbose is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/devshell/test_repl.py::TestCliParsing -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement CLI entry point**

Create `matmaster/devshell/cli.py`:

```python
"""CLI entry point for mm-devshell."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="mm-devshell",
        description="MatMaster DevShell -- interactive agent testing CLI",
    )
    parser.add_argument(
        "--workdir", type=Path, required=True,
        help="Workspace directory (persistent)",
    )
    parser.add_argument(
        "--log-dir", type=Path, required=True,
        help="Event log directory",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Config file path (default: use built-in defaults)",
    )
    parser.add_argument(
        "--session", type=str, default=None,
        help="Session type override: local/docker/ssh",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Enable verbose output",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for mm-devshell."""
    args = parse_args(argv)

    # Load config
    from matmaster.devshell.config import DevConfig, load_dev_config

    if args.config:
        try:
            config = load_dev_config(args.config)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        config = DevConfig()

    # Override session type if specified on CLI
    if args.session:
        config = config.model_copy(
            update={"session": config.session.model_copy(update={"type": args.session})}
        )

    # Validate API key
    if not config.llm.api_key:
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print(
                "Error: No API key. Set OPENAI_API_KEY env var or "
                "specify llm.api_key in config.",
                file=sys.stderr,
            )
            sys.exit(1)
        config = config.model_copy(
            update={"llm": config.llm.model_copy(update={"api_key": api_key})}
        )

    # Ensure directories exist
    args.workdir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)

    # Create LLM provider
    from matmaster.providers.openai_provider import OpenAIProvider

    llm_provider = OpenAIProvider(
        model=config.llm.model,
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
    )

    # Create stream hook and runner
    from matmaster.devshell.stream_hook import DevStreamHook
    from matmaster.devshell.runner import DevRunner
    from matmaster.devshell.repl import run_repl

    stream_hook = DevStreamHook(verbose=args.verbose)
    runner = DevRunner(
        config=config,
        workdir=args.workdir,
        llm_provider=llm_provider,
        stream_hook=stream_hook,
    )

    # Start REPL
    run_repl(runner, config, log_dir=args.log_dir, verbose=args.verbose)


if __name__ == "__main__":
    main()
```

Create `matmaster/devshell/__main__.py`:

```python
"""Allow running as python -m matmaster.devshell."""
from matmaster.devshell.cli import main

main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/devshell/ -v`
Expected: ALL PASS

- [ ] **Step 5: Add mm-devshell script entry to pyproject.toml**

In `pyproject.toml`, in `[project.scripts]` section, add:
```toml
mm-devshell = "matmaster.devshell.cli:main"
```

- [ ] **Step 6: Commit**

```bash
git add matmaster/devshell/cli.py matmaster/devshell/__main__.py pyproject.toml tests/matmaster/devshell/
git commit -m "feat(devshell): add CLI entry point and mm-devshell script entry"
```

### Task 12: Integration smoke test

**Files:**
- Test: `tests/matmaster/devshell/test_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/matmaster/devshell/test_integration.py`:

```python
"""Integration test: DevRunner -> Exp -> AgentKernel with mock LLM."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch, MagicMock

from matmaster.types.messages import StreamChunk, ToolCallData


class ToolCallingMockProvider:
    """Mock that calls a tool then finishes."""

    def __init__(self):
        self._call_count = 0

    def chat(self, messages, tools=None):
        from matmaster.types.messages import LLMResponse
        return LLMResponse(content="unused", finish_reason="stop")

    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None) -> Iterator[StreamChunk]:
        self._call_count += 1
        if self._call_count == 1:
            yield StreamChunk(
                tool_call_deltas=[
                    {"index": 0, "id": "tc-1", "name": "bash",
                     "arguments": '{"command": "echo hello"}'}
                ]
            )
            yield StreamChunk(finish_reason="stop")
        else:
            yield StreamChunk(content="Done! I executed the command.", finish_reason="stop")


class TestDevShellIntegration:
    def test_full_run_with_tool_call(self, tmp_path: Path) -> None:
        from matmaster.devshell.config import DevConfig
        from matmaster.devshell.runner import DevRunner
        from matmaster.devshell.stream_hook import DevStreamHook
        from matmaster.devshell.event_logger import EventLogger
        from matmaster.core.bus import MessageBus
        import io

        workdir = tmp_path / "workspace"
        workdir.mkdir()
        log_file = tmp_path / "events.jsonl"

        config = DevConfig()
        output = io.StringIO()
        stream_hook = DevStreamHook(output=output)

        with patch("matmaster.devshell.runner.DevRunner._create_session") as mock_session:
            mock_session.return_value = MagicMock()
            runner = DevRunner(
                config=config,
                workdir=workdir,
                llm_provider=ToolCallingMockProvider(),
                stream_hook=stream_hook,
            )

        bus = MessageBus()
        event_logger = EventLogger(log_file, run_id="run-001")

        result = runner.run("echo hello", bus=bus)

        # Drain bus
        import queue
        while True:
            try:
                event = bus.get_nowait()
                event_logger.log_event(event)
            except queue.Empty:
                break
        event_logger.close()

        # Verify result
        assert result.event.reason == "natural"
        assert result.event.final_content == "Done! I executed the command."

        # Verify terminal output contains tool call
        terminal_output = output.getvalue()
        assert "tool_call: bash" in terminal_output

        # Verify history accumulated
        assert len(runner.history) > 0

    def test_multi_turn_history(self, tmp_path: Path) -> None:
        from matmaster.devshell.config import DevConfig
        from matmaster.devshell.runner import DevRunner
        from matmaster.types.messages import StreamChunk

        class SimpleProvider:
            def chat(self, messages, tools=None):
                from matmaster.types.messages import LLMResponse
                return LLMResponse(content="unused", finish_reason="stop")
            def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
                return self.chat(messages, tools)
            def chat_stream(self, messages, tools=None) -> Iterator[StreamChunk]:
                yield StreamChunk(content=f"Reply to msg #{len(messages)}", finish_reason="stop")

        workdir = tmp_path / "workspace"
        workdir.mkdir()
        config = DevConfig()

        with patch("matmaster.devshell.runner.DevRunner._create_session") as mock_session:
            mock_session.return_value = MagicMock()
            runner = DevRunner(
                config=config,
                workdir=workdir,
                llm_provider=SimpleProvider(),
            )

        runner.run("first")
        runner.run("second")
        runner.run("third")

        # History should have: User+Assistant for each of 3 turns
        # Each turn adds UserMessage + AssistantMessage
        assert len(runner.history) == 6
```

- [ ] **Step 2: Run integration test**

Run: `uv run pytest tests/matmaster/devshell/test_integration.py -v`
Expected: ALL PASS

- [ ] **Step 3: Run full devshell test suite**

Run: `uv run pytest tests/matmaster/devshell/ -v`
Expected: ALL PASS

- [ ] **Step 4: Run full project test suite**

Run: `uv run pytest tests/matmaster/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add tests/matmaster/devshell/test_integration.py
git commit -m "test(devshell): add integration smoke test for full pipeline"
```

### Task 13: Example config file

**Files:**
- Create: `configs/devshell/dev.yaml.example`

- [ ] **Step 1: Create example config**

```yaml
# MatMaster DevShell Configuration
# Copy to dev.yaml and adjust values.

# LLM connection
llm:
  api_key: ${OPENAI_API_KEY}
  base_url: "https://api.openai.com/v1"
  model: "gpt-4o"
  temperature: 0.7

# Agent behavior
agent:
  name: "general"
  mode: "direct"
  max_turns: 20
  # identity: "You are a materials science AI assistant."

# Session type
session:
  type: "local"  # local / docker / ssh

# Tool registration ("*" = all builtin tools)
tools:
  builtin:
    - "*"
```

- [ ] **Step 2: Commit**

```bash
git add configs/devshell/dev.yaml.example
git commit -m "docs: add devshell example config file"
```

---

## Summary

| Chunk | Tasks | Purpose |
|-------|-------|---------|
| 1 | 1-5 | Core kernel/exp changes (KernelRunResult, guard hook, tool safety, identity, packaging) |
| 2 | 6-9 | DevShell modules (config, stream hook, event logger, runner) |
| 3 | 10-13 | CLI, REPL, integration test, example config |

**Total tasks:** 13
**Estimated files created:** 10
**Estimated files modified:** ~15
