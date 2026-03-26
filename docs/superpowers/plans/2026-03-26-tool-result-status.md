# Tool Result Status Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the `status` field in tool_result SSE events by introducing a structured `ToolResult` model and propagating status through the tool -> kernel -> hook -> event -> SSE pipeline.

**Architecture:** `ToolResult` Pydantic model is the new structured return type for tool execution. `ToolRegistry.execute()` normalizes all tool returns (str, ToolResult, None) into `ToolResult`. The kernel passes `ToolResult` through hooks to `ToolResultEvent`, which carries `status` to the SSE payload.

**Tech Stack:** Python, Pydantic, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `matmaster/tools/tool_result.py` | Create | ToolResult model |
| `matmaster/tools/tool_registry.py` | Modify | Tool protocol return type + ToolRegistry normalization |
| `matmaster/tools/builtin/base.py` | Modify | BuiltinTool error path returns ToolResult |
| `matmaster/tools/evomaster_tool_adapter.py` | Modify | Return ToolResult with status + info |
| `matmaster/tools/lazy_mcp.py` | Modify | Return ToolResult with status + info |
| `matmaster/types/events.py` | Modify | ToolResultEvent gains status field |
| `matmaster/core/hooks.py` | Modify | Hook protocol + helpers + EventEmitterHook |
| `matmaster/hooks/output_processor.py` | Modify | Signature update |
| `matmaster/hooks/skill_hit.py` | Modify | Signature update |
| `matmaster/devshell/stream_hook.py` | Modify | Signature + error detection logic |
| `matmaster/core/agent.py` | Modify | Kernel tool execution block |
| `matmaster/integration/event_payloads.py` | Modify | SSE payload includes status |

---

## Chunk 1: ToolResult Model + ToolRegistry Normalization

### Task 1: Create ToolResult model

**Files:**
- Create: `matmaster/tools/tool_result.py`
- Test: `tests/matmaster/tools/test_tool_result.py`

- [ ] **Step 1: Write the test**

```python
# tests/matmaster/tools/test_tool_result.py
"""Tests for ToolResult model."""
from matmaster.tools.tool_result import ToolResult


class TestToolResult:
    def test_default_values(self) -> None:
        r = ToolResult()
        assert r.status == "success"
        assert r.content == ""
        assert r.info == {}

    def test_explicit_success(self) -> None:
        r = ToolResult(status="success", content="ok", info={"key": "val"})
        assert r.status == "success"
        assert r.content == "ok"
        assert r.info == {"key": "val"}

    def test_error_status(self) -> None:
        r = ToolResult(status="error", content="Error: boom")
        assert r.status == "error"
        assert r.content == "Error: boom"
        assert r.info == {}

    def test_model_dump(self) -> None:
        r = ToolResult(status="error", content="fail", info={"error": "x"})
        d = r.model_dump()
        assert d == {"status": "error", "content": "fail", "info": {"error": "x"}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_tool_result.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write ToolResult model**

```python
# matmaster/tools/tool_result.py
"""ToolResult -- structured return type for tool execution.

Used by ToolRegistry to normalize all tool returns into a unified
status + content + info structure. Consumed by AgentKernel and hooks.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Structured tool execution result.

    Attributes:
        status: "success" or "error".
        content: Tool output text consumed by the LLM.
        info: Metadata dict (auto_save flags, error details, etc.).
    """

    status: str = "success"
    content: str = ""
    info: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_tool_result.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/tool_result.py tests/matmaster/tools/test_tool_result.py
git commit -m "feat: add ToolResult model for structured tool execution results"
```

### Task 2: Update Tool protocol and ToolRegistry normalization

**Files:**
- Modify: `matmaster/tools/tool_registry.py`
- Test: `tests/matmaster/tools/test_tool_registry.py` (create if not exists, or add tests)

- [ ] **Step 1: Write the tests**

```python
# tests/matmaster/tools/test_tool_registry.py
"""Tests for ToolRegistry normalization to ToolResult."""
from matmaster.tools.tool_result import ToolResult
from matmaster.tools.tool_registry import ToolRegistry


class _StrTool:
    """Tool that returns plain str."""
    name = "str_tool"
    description = "returns str"
    json_schema = {"type": "object", "properties": {}}

    def execute(self, arguments):
        return "hello"


class _ToolResultTool:
    """Tool that returns ToolResult."""
    name = "result_tool"
    description = "returns ToolResult"
    json_schema = {"type": "object", "properties": {}}

    def execute(self, arguments):
        return ToolResult(status="error", content="fail", info={"error": "bad"})


class _NoneTool:
    """Tool that returns None."""
    name = "none_tool"
    description = "returns None"
    json_schema = {"type": "object", "properties": {}}

    def execute(self, arguments):
        return None


class _ExceptionTool:
    """Tool that raises."""
    name = "boom_tool"
    description = "raises"
    json_schema = {"type": "object", "properties": {}}

    def execute(self, arguments):
        raise ValueError("kaboom")


class TestToolRegistryNormalization:
    def test_str_return_normalized_to_success(self) -> None:
        reg = ToolRegistry()
        reg.register(_StrTool(), source="test")
        result = reg.execute("str_tool", {})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.content == "hello"

    def test_tool_result_return_passed_through(self) -> None:
        reg = ToolRegistry()
        reg.register(_ToolResultTool(), source="test")
        result = reg.execute("result_tool", {})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert result.content == "fail"
        assert result.info == {"error": "bad"}

    def test_none_return_normalized_to_empty_success(self) -> None:
        reg = ToolRegistry()
        reg.register(_NoneTool(), source="test")
        result = reg.execute("none_tool", {})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.content == ""

    def test_tool_not_found_returns_error(self) -> None:
        reg = ToolRegistry()
        result = reg.execute("missing", {})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "not found" in result.content

    def test_exception_propagates(self) -> None:
        """ToolRegistry does NOT catch exceptions -- they propagate to kernel."""
        import pytest

        reg = ToolRegistry()
        reg.register(_ExceptionTool(), source="test")
        with pytest.raises(ValueError, match="kaboom"):
            reg.execute("boom_tool", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/tools/test_tool_registry.py::TestToolRegistryNormalization -v`
Expected: FAIL (execute returns str, not ToolResult)

- [ ] **Step 3: Update tool_registry.py**

In `matmaster/tools/tool_registry.py`:

1. Add import: `from matmaster.tools.tool_result import ToolResult`
2. Change `Tool.execute` return type annotation to `str | ToolResult`
3. Change `ToolRegistry.execute` to return `ToolResult` with normalization:

```python
def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
    """Dispatch execution to the named tool. Normalizes return to ToolResult."""
    tool = self._tools.get(name)
    if tool is None:
        available = ", ".join(sorted(self._tools))
        return ToolResult(
            status="error",
            content=f"Error: Tool '{name}' not found. Available: {available}",
        )
    raw = tool.execute(arguments)
    if isinstance(raw, ToolResult):
        return raw
    if raw is None:
        return ToolResult(status="success", content="")
    return ToolResult(status="success", content=str(raw))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/tools/test_tool_registry.py::TestToolRegistryNormalization -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Update existing tests in test_tool_registry.py**

Existing tests compare `registry.execute()` return to plain `str`. Update them to work with `ToolResult`:

- Line 37: `result = registry.execute("greet", {})` then `assert result == "hello!"` -> `assert result.content == "hello!"`
- Line 45-46: `result = registry.execute("missing_tool", {})` then `assert "not found" in result.lower()` -> `assert "not found" in result.content.lower()`
- Line 69: `result = registry.execute("anything", {})` -> update assertions to use `result.content`
- Line 87: `assert registry.execute("shared", {}) == "second"` -> `assert registry.execute("shared", {}).content == "second"`
- Line 110: `assert registry.execute("overlap", {}) == "skill"` -> `assert registry.execute("overlap", {}).content == "skill"`

Add import `from matmaster.tools.tool_result import ToolResult` if not already present.

- [ ] **Step 6: Run all registry tests**

Run: `uv run pytest tests/matmaster/tools/test_tool_registry.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add matmaster/tools/tool_registry.py tests/matmaster/tools/test_tool_registry.py
git commit -m "feat: ToolRegistry.execute() returns ToolResult with normalization"
```

### Task 3: Update BuiltinTool error path

**Files:**
- Modify: `matmaster/tools/builtin/base.py:45-51`
- Test: `tests/matmaster/tools/test_builtin_base.py` (create)

- [ ] **Step 1: Write the test**

```python
# tests/matmaster/tools/test_builtin_base.py
"""Tests for BuiltinTool.execute error path returning ToolResult."""
from matmaster.tools.tool_result import ToolResult


class TestBuiltinToolErrorPath:
    def test_exception_returns_tool_result_error(self) -> None:
        from matmaster.tools.builtin.base import BuiltinTool

        class FailTool(BuiltinTool):
            name = "fail"
            description = "always fails"
            json_schema = {"type": "object", "properties": {}}

            def _execute(self, arguments):
                raise RuntimeError("kaboom")

        tool = FailTool()
        result = tool.execute({})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "kaboom" in result.content

    def test_success_returns_str(self) -> None:
        """Success path still returns str (normalized by ToolRegistry)."""
        from matmaster.tools.builtin.base import BuiltinTool

        class OkTool(BuiltinTool):
            name = "ok"
            description = "always ok"
            json_schema = {"type": "object", "properties": {}}

            def _execute(self, arguments):
                return "done"

        tool = OkTool()
        result = tool.execute({})
        assert isinstance(result, str)
        assert result == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_builtin_base.py -v`
Expected: FAIL (execute returns str "Error: kaboom", not ToolResult)

- [ ] **Step 3: Update BuiltinTool.execute()**

In `matmaster/tools/builtin/base.py`, change `execute()`:

```python
def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
    """Tool Protocol entry point. Delegates to _execute."""
    try:
        return self._execute(arguments)
    except Exception as e:
        self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
        return ToolResult(status="error", content=f"Error: {e}")
```

Add import at top: `from matmaster.tools.tool_result import ToolResult`

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_builtin_base.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Update existing tests in test_builtin_base.py**

Existing tests treat `execute()` error return as plain `str`:

- Line 70: `assert result == "executed with {'arg1': 'hello'}"` -> keep as-is (success path still returns str)
- Line 74-75: `result = tool.execute({})` then `assert result.startswith("Error:")` -> `assert isinstance(result, ToolResult)` then `assert result.status == "error"` then `assert "Error:" in result.content`

Add import `from matmaster.tools.tool_result import ToolResult`.

- [ ] **Step 6: Run all builtin tests**

Run: `uv run pytest tests/matmaster/tools/test_builtin_base.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add matmaster/tools/builtin/base.py tests/matmaster/tools/test_builtin_base.py
git commit -m "feat: BuiltinTool returns ToolResult(status=error) on exception"
```

### Task 4: Update EvoToolAdapter and LazyMCPTool

**Files:**
- Modify: `matmaster/tools/evomaster_tool_adapter.py:49-54`
- Modify: `matmaster/tools/lazy_mcp.py:57-68`
- Test: `tests/matmaster/tools/test_evomaster_tool_adapter.py` (create)

- [ ] **Step 1: Write the tests**

```python
# tests/matmaster/tools/test_evomaster_tool_adapter.py
"""Tests for EvoToolAdapter returning ToolResult with status and info."""
from unittest.mock import MagicMock
from matmaster.tools.tool_result import ToolResult
from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter


def _make_adapter(observation, info) -> EvoToolAdapter:
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_tool.params_class.__doc__ = "test"
    mock_tool.params_class.model_json_schema.return_value = {}
    mock_tool.execute.return_value = (observation, info)
    return EvoToolAdapter(mock_tool, session=MagicMock())


class TestEvoToolAdapterResult:
    def test_success_with_str_observation(self) -> None:
        adapter = _make_adapter("result text", {})
        result = adapter.execute({"key": "val"})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.content == "result text"
        assert result.info == {}

    def test_error_from_info(self) -> None:
        adapter = _make_adapter("err msg", {"error": "bad input"})
        result = adapter.execute({})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert result.content == "err msg"
        assert result.info == {"error": "bad input"}

    def test_dict_observation_serialized(self) -> None:
        adapter = _make_adapter({"data": [1, 2]}, {})
        result = adapter.execute({})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert '"data"' in result.content

    def test_info_passed_through(self) -> None:
        adapter = _make_adapter("ok", {"auto_saved_path": "/tmp/x.txt"})
        result = adapter.execute({})
        assert result.info == {"auto_saved_path": "/tmp/x.txt"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/tools/test_evomaster_tool_adapter.py -v`
Expected: FAIL (execute returns str, not ToolResult)

- [ ] **Step 3: Update EvoToolAdapter.execute()**

In `matmaster/tools/evomaster_tool_adapter.py`:

Add import: `from matmaster.tools.tool_result import ToolResult`

Replace `execute()`:

```python
def execute(self, arguments: dict[str, Any]) -> ToolResult:
    args_json = json.dumps(arguments, ensure_ascii=False)
    observation, info = self._tool.execute(self._session, args_json)
    content = (
        observation
        if isinstance(observation, str)
        else json.dumps(observation, ensure_ascii=False, default=str)
    )
    info_dict = info if isinstance(info, dict) else {}
    status = "error" if "error" in info_dict else "success"
    return ToolResult(status=status, content=content, info=info_dict)
```

- [ ] **Step 4: Update LazyMCPTool.execute()**

In `matmaster/tools/lazy_mcp.py`:

Add import: `from matmaster.tools.tool_result import ToolResult`

Replace `execute()`:

```python
def execute(self, arguments: dict[str, Any]) -> ToolResult:
    if self._real_tool is None:
        self._real_tool = self._connector.connect_and_get_tool(
            self._server_name, self._remote_tool_name
        )
    args_json = json.dumps(arguments)
    observation, info = self._real_tool.execute(
        self._connector.session, args_json
    )
    content = (
        observation
        if isinstance(observation, str)
        else json.dumps(observation, default=str)
    )
    info_dict = info if isinstance(info, dict) else {}
    status = "error" if "error" in info_dict else "success"
    return ToolResult(status=status, content=content, info=info_dict)
```

- [ ] **Step 5: Run new tests to verify they pass**

Run: `uv run pytest tests/matmaster/tools/test_evomaster_tool_adapter.py::TestEvoToolAdapterResult -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Update existing tests in test_evomaster_tool_adapter.py**

Existing tests compare `adapter.execute()` return to plain `str`:

- Line 84: `assert result == "hello world"` -> `assert result.content == "hello world"`
- Other assertions that treat `execute()` return as `str` -> access `.content`

Add import `from matmaster.tools.tool_result import ToolResult` if not already present.

Also check and update `tests/matmaster/tools/test_lazy_mcp.py` if it exists and tests `execute()` return type.

- [ ] **Step 7: Run all adapter tests**

Run: `uv run pytest tests/matmaster/tools/test_evomaster_tool_adapter.py tests/matmaster/tools/test_lazy_mcp.py -v`
Expected: PASS (all tests)

- [ ] **Step 8: Commit**

```bash
git add matmaster/tools/evomaster_tool_adapter.py matmaster/tools/lazy_mcp.py tests/matmaster/tools/test_evomaster_tool_adapter.py tests/matmaster/tools/test_lazy_mcp.py
git commit -m "feat: EvoToolAdapter and LazyMCPTool return ToolResult with status and info"
```

---

## Chunk 2: Event Type + Hook Protocol + SSE Payload

### Task 5: Add status field to ToolResultEvent

**Files:**
- Modify: `matmaster/types/events.py:62-71`

- [ ] **Step 1: Update ToolResultEvent**

In `matmaster/types/events.py`, add `status` field to `ToolResultEvent`:

```python
class ToolResultEvent(BaseModel):
    """Tool execution result event."""

    type: Literal["tool_result"] = "tool_result"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    call_id: str
    tool_name: str
    result: Any  # str | dict
    status: str = "success"  # "success" | "error"
    info: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 2: Run existing event tests to verify no regressions**

Run: `uv run pytest tests/matmaster/types/test_events.py -v`
Expected: PASS (default "success" is backward compatible with existing ToolResultEvent constructors)

- [ ] **Step 3: Commit**

```bash
git add matmaster/types/events.py
git commit -m "feat: add status field to ToolResultEvent"
```

### Task 6: Update Hook protocol and all implementations

**Files:**
- Modify: `matmaster/core/hooks.py:55,76,131-136,187-196`
- Modify: `matmaster/hooks/output_processor.py:40`
- Modify: `matmaster/hooks/skill_hit.py:32`
- Modify: `matmaster/devshell/stream_hook.py:48-53`

- [ ] **Step 1: Update hooks.py**

In `matmaster/core/hooks.py`:

Add import: `from matmaster.tools.tool_result import ToolResult`

Update `Hook` protocol (line 55):
```python
def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None: ...
```

Update `BaseHook` (line 76):
```python
def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
    """Default: no-op observation."""
```

Update `run_post_tool_call` (lines 131-136):
```python
def run_post_tool_call(
    hooks: list[Hook], tool_call: ToolCallData, result: ToolResult
) -> None:
    """Run post_tool_call on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        hook.post_tool_call(tool_call, result)
```

Update `EventEmitterHook.post_tool_call` (lines 187-196):
```python
def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
    """Emit ToolResultEvent after tool execution."""
    self._bus.emit(
        ToolResultEvent(
            source=self._source,
            call_id=tool_call.id,
            tool_name=tool_call.name,
            result=result.content,
            status=result.status,
            info=result.info,
        )
    )
```

- [ ] **Step 2: Update output_processor.py**

In `matmaster/hooks/output_processor.py`:

Add import: `from matmaster.tools.tool_result import ToolResult`

Change line 40 signature:
```python
def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
```

Update ToolResultEvent constructions inside (lines 46-53, 57-64) to use `result.content`:
```python
result=result.content,
```

- [ ] **Step 3: Update skill_hit.py**

In `matmaster/hooks/skill_hit.py`:

Add import: `from matmaster.tools.tool_result import ToolResult`

Change line 32 signature:
```python
def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
```

No body changes needed (`result` is unused in function body).

- [ ] **Step 4: Update stream_hook.py**

In `matmaster/devshell/stream_hook.py`:

Add import: `from matmaster.tools.tool_result import ToolResult`

Replace lines 48-53:
```python
def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
    is_error = result.status == "error"
    prefix = "\u274c tool_error:" if is_error else "\u2705 tool_result:"
    display = result.content if len(result.content) <= _MAX_RESULT_LEN else result.content[:_MAX_RESULT_LEN] + "..."
    self._out.write(f"\n{prefix} {display}\n\n")
    self._out.flush()
```

- [ ] **Step 5: Run hook tests to check for compilation errors**

Run: `uv run pytest tests/matmaster/core/test_hooks.py tests/matmaster/hooks/ tests/matmaster/devshell/test_stream_hook.py -v --no-header 2>&1 | tail -30`
Expected: FAIL (tests still pass str to post_tool_call -- will fix in Task 8)

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/hooks.py matmaster/hooks/output_processor.py matmaster/hooks/skill_hit.py matmaster/devshell/stream_hook.py
git commit -m "feat: update Hook protocol post_tool_call to accept ToolResult"
```

### Task 7: Update AgentKernel + SSE payload

**Files:**
- Modify: `matmaster/core/agent.py:173-188`
- Modify: `matmaster/integration/event_payloads.py:68-76`

- [ ] **Step 1: Update agent.py tool execution block**

In `matmaster/core/agent.py`:

Add import at top: `from matmaster.tools.tool_result import ToolResult`

Replace lines 173-188:
```python
                # Tool execution
                try:
                    tool_result = spec.tool_registry.execute(tc.name, tc.arguments)
                except Exception as e:
                    tool_result = ToolResult(
                        status="error",
                        content=f"Error executing tool '{tc.name}': {type(e).__name__}: {e}",
                    )
                    logger.exception("Tool execution failed: %s", tc.name)
                messages.append(
                    ToolMessage(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content=tool_result.content,
                    )
                )

                # post_tool_call hook (observation, all hooks called)
                run_post_tool_call(spec.hooks, tc, tool_result)
```

- [ ] **Step 2: Update event_payloads.py**

In `matmaster/integration/event_payloads.py`, replace lines 68-76:

```python
    if event_type == "tool_result":
        call_id = payload.get("call_id")
        return {
            "id": call_id,
            "call_id": call_id,
            "name": payload.get("tool_name"),
            "result": payload.get("result"),
            "status": payload.get("status", "success"),
            "info": payload.get("info") or {},
        }
```

- [ ] **Step 3: Run the core agent test to check basic flow**

Run: `uv run pytest tests/matmaster/core/test_agent.py -v --no-header 2>&1 | tail -30`
Expected: FAIL (RecordingHook still uses old signature -- will fix in Task 8)

- [ ] **Step 4: Commit**

```bash
git add matmaster/core/agent.py matmaster/integration/event_payloads.py
git commit -m "feat: AgentKernel passes ToolResult through hooks; SSE payload includes status"
```

---

## Chunk 3: Test Updates

### Task 8: Update all test files

**Files:**
- Modify: `tests/matmaster/core/test_hooks.py`
- Modify: `tests/matmaster/core/test_agent.py`
- Modify: `tests/matmaster/hooks/test_output_processor.py`
- Modify: `tests/matmaster/hooks/test_skill_hit.py`
- Modify: `tests/matmaster/devshell/test_stream_hook.py`
- Modify: `tests/matmaster/integration/test_events_to_messages.py`
- Modify: `tests/matmaster/integration/test_event_router.py`
- Modify: `tests/matmaster/types/test_events.py`
- Modify: `tests/matmaster/devshell/test_event_logger.py`
- Modify: `tests/matmaster/integration/test_workspace_handler.py`
- Modify: `tests/test_chat_stream_direct.py`

- [ ] **Step 1: Update test_hooks.py**

Add import: `from matmaster.tools.tool_result import ToolResult`

Replace every `post_tool_call(tc, "string")` call with `post_tool_call(tc, ToolResult(content="string"))`.

Key locations:
- Line 85: `hook.post_tool_call(sample_tool_call, "result")` -> `hook.post_tool_call(sample_tool_call, ToolResult(content="result"))`
- Line 150: `TrackingHook.post_tool_call(self, tool_call, result: str)` -> `TrackingHook.post_tool_call(self, tool_call, result: ToolResult)`
- Line 217: `run_post_tool_call([h1, h2], sample_tool_call, "result")` -> `run_post_tool_call([h1, h2], sample_tool_call, ToolResult(content="result"))`
- Line 262: `hook.post_tool_call(sample_tool_call, "result_data")` -> `hook.post_tool_call(sample_tool_call, ToolResult(content="result_data"))`
- Line 360: `OldHook.post_tool_call` -> update signature to `result: ToolResult`

Add new test for EventEmitterHook status propagation:
```python
def test_post_tool_call_emits_status(
    self, sample_tool_call: ToolCallData
) -> None:
    bus = MessageBus()
    hook = EventEmitterHook(bus, "agent-1")
    hook.post_tool_call(
        sample_tool_call,
        ToolResult(status="error", content="fail", info={"error": "x"}),
    )
    event = bus.get_nowait()
    assert isinstance(event, ToolResultEvent)
    assert event.status == "error"
    assert event.result == "fail"
    assert event.info == {"error": "x"}
```

- [ ] **Step 2: Update test_agent.py**

Add import: `from matmaster.tools.tool_result import ToolResult`

Line 211: `RecordingHook.post_tool_call(self, tool_call, result: str)` -> `RecordingHook.post_tool_call(self, tool_call, result: ToolResult)`

- [ ] **Step 3: Update test_output_processor.py**

Add import: `from matmaster.tools.tool_result import ToolResult`

Replace all `hook.post_tool_call(tc, "string")` with `hook.post_tool_call(tc, ToolResult(content="string"))`:
- Line 24: `hook.post_tool_call(tc, "file written")` -> `hook.post_tool_call(tc, ToolResult(content="file written"))`
- Line 42: `hook.post_tool_call(tc, "very long text...")` -> `hook.post_tool_call(tc, ToolResult(content="very long text..."))`
- Line 60: `hook.post_tool_call(tc, "result")` -> `hook.post_tool_call(tc, ToolResult(content="result"))`
- Line 71: `hook.post_tool_call(tc, "result")` -> `hook.post_tool_call(tc, ToolResult(content="result"))`

- [ ] **Step 4: Update test_skill_hit.py**

Add import: `from matmaster.tools.tool_result import ToolResult`

Replace all `hook.post_tool_call(tc, "result")` with `hook.post_tool_call(tc, ToolResult(content="result"))`:
- Lines 25, 40, 51, 62

- [ ] **Step 5: Update test_stream_hook.py**

Add import: `from matmaster.tools.tool_result import ToolResult`

Replace line 47: `hook.post_tool_call(tc, "file1.py\nfile2.py")` -> `hook.post_tool_call(tc, ToolResult(content="file1.py\nfile2.py"))`
Replace line 57: `hook.post_tool_call(tc, long_result)` -> `hook.post_tool_call(tc, ToolResult(content=long_result))`

Add new test for error status detection:
```python
def test_post_tool_call_error_status(self) -> None:
    hook, buf = self._make_hook()
    tc = ToolCallData(id="tc-1", name="bash", arguments={})
    hook.post_tool_call(tc, ToolResult(status="error", content="Error: boom"))

    output = buf.getvalue()
    assert "tool_error:" in output
    assert "Error: boom" in output
```

- [ ] **Step 6: Update integration test files**

**`tests/matmaster/integration/test_event_router.py`** (MUST update -- full equality assertions):
- Lines 367-372: `assert args[3] == {...}` for tool_result persistence shape. Add `"status": "success"` to expected dict:
  ```python
  assert args[3] == {
      "id": "c1",
      "call_id": "c1",
      "name": "bash",
      "result": "file.txt",
      "status": "success",
      "info": {"auto_save": True},
  }
  ```
- Lines 528-534: `test_tool_result_payload_matches_frontend_contract` -- same fix, add `"status": "success"` to expected dict.
- Any other full-equality assertions on tool_result content dicts.

**`tests/test_chat_stream_direct.py`** (MUST update -- full equality assertion):
- Lines 313-319: `assert frames[1]['content'] == {...}` for tool_result SSE frame. Add `"status": "success"`:
  ```python
  assert frames[1]['content'] == {
      'id': 'call-1',
      'call_id': 'call-1',
      'name': 'bash',
      'result': {'status': 'success', 'stdout': 'ok'},
      'status': 'success',
      'info': {'auto_save': True},
  }
  ```

**`tests/matmaster/types/test_events.py`** (should update):
- Line 95: existing `TestToolResultEvent` -- add `assert evt.status == "success"` to verify default
- Lines 229, 283: serialization dicts for ToolResultEvent -- these use minimal fields and will still work with defaults, but consider adding explicit status assertions

**No changes needed** (default "success" backward compatible):
- `tests/matmaster/integration/test_events_to_messages.py`: round-trip test reads `result` and `name` only
- `tests/matmaster/devshell/test_event_logger.py`: no full-equality assertion on payload
- `tests/matmaster/integration/test_workspace_handler.py`: no payload shape assertion

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/matmaster/ tests/test_chat_stream_direct.py -v --no-header 2>&1 | tail -40`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add tests/
git commit -m "test: update all tests for ToolResult-based post_tool_call signature"
```

### Task 9: Final verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -x -v --no-header 2>&1 | tail -40`
Expected: ALL PASS

- [ ] **Step 2: Verify import chain**

Run: `uv run python -c "from matmaster.tools.tool_result import ToolResult; from matmaster.core.agent import AgentKernel; from matmaster.integration.event_payloads import _public_content_for_event; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit (if any fixups needed)**

```bash
git add -A && git commit -m "fix: address any test fixups from full suite run"
```
