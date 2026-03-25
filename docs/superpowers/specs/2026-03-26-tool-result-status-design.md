# Tool Result Status Design

## Problem

Refactored `matmaster/` architecture lost the `status` field in `tool_result` SSE events. The old architecture (`origin/main`) wrapped every tool output via `format_tool_observation()` into `{"status": "success|error", "observation": ...}`, which the frontend displays and may use for future logic. The refactored code returns raw strings with no structured status signal.

Root cause: `Tool.execute()` was simplified from `(observation, info)` tuple to plain `str`, and the `format_tool_observation()` wrapper was removed during refactoring.

## Design

### ToolResult model

New file `matmaster/tools/tool_result.py`:

```python
from pydantic import BaseModel, Field
from typing import Any

class ToolResult(BaseModel):
    status: str = "success"          # "success" | "error"
    content: str = ""                # tool output (consumed by LLM)
    info: dict[str, Any] = Field(default_factory=dict)  # metadata (auto_save, error detail, etc.)
```

Uses Pydantic BaseModel. Although it lives in `matmaster/tools/` (co-located with Tool protocol), Pydantic is used for consistency with `matmaster/types/` patterns and seamless unpacking into ToolResultEvent.

### Tool protocol + ToolRegistry normalization

`Tool.execute()` return type broadened to `str | ToolResult`:

```python
class Tool(Protocol):
    def execute(self, arguments: dict[str, Any]) -> str | ToolResult: ...
```

Existing `str`-returning tools satisfy `str | ToolResult` without modification.

`ToolRegistry.execute()` returns `ToolResult` (normalization point):

- `tool.execute()` returns `ToolResult` -> pass through
- `tool.execute()` returns `str` -> `ToolResult(status="success", content=str_result)`
- `tool.execute()` returns `None` -> `ToolResult(status="success", content="")`
- Tool not found -> `ToolResult(status="error", content="Error: Tool '...' not found. Available: ...")`

Note: `ToolRegistry.execute()` does NOT catch exceptions from `tool.execute()`. Exceptions propagate to AgentKernel's try/except which constructs `ToolResult(status="error")`.

### AgentKernel execution logic

`agent.py` tool execution block changes:

- `spec.tool_registry.execute()` now returns `ToolResult`
- Exception fallback constructs `ToolResult(status="error", content=error_msg)`
- `ToolMessage.content` takes `tool_result.content` (LLM still sees plain string)
- `run_post_tool_call` receives entire `ToolResult` instead of `str`

### Hook protocol + EventEmitterHook

`post_tool_call` signature changes from `(tool_call, result: str)` to `(tool_call, result: ToolResult)`:

- `Hook` protocol, `BaseHook` default, `run_post_tool_call` helper all updated
- `EventEmitterHook.post_tool_call` unpacks `ToolResult` into `ToolResultEvent` fields
- All Hook implementations must update signature (see affected files table)

This is a breaking change to the internal Hook protocol. All implementations are within this codebase and will be updated simultaneously. No external consumers exist.

### ToolResultEvent + SSE payload

`ToolResultEvent` gains `status` field:

```python
class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    call_id: str
    tool_name: str
    result: Any
    status: str = "success"  # "success" | "error"
    info: dict[str, Any] = Field(default_factory=dict)
```

SSE payload (`event_payloads.py`) includes `status`:

```python
return {
    "id": call_id,
    "call_id": call_id,
    "name": payload.get("tool_name"),
    "result": payload.get("result"),
    "status": payload.get("status", "success"),
    "info": payload.get("info") or {},
}
```

### Difference from old architecture

Old architecture: `format_tool_observation()` wraps tool output into `{"status": "success", "observation": ...}` JSON string, stored as `ToolMessage.content`. The SSE `tool_result` event's `result` field contains this JSON, so frontend reads status at `result.status` path.

New architecture: `status` is a top-level field in the SSE payload, sibling to `result`. Frontend reads status at `content.status` (which maps to the `_public_content_for_event` return dict).

Frontend compatibility: the existing frontend code (`useEvoSSEHandler.ts:555-564`) already checks both `content.status` and `content.result.status` paths, so both old persisted events and new events work without frontend changes.

## Affected files

### Source files

| File | Change |
|------|--------|
| `matmaster/tools/tool_result.py` | New file: ToolResult Pydantic model |
| `matmaster/tools/tool_registry.py` | Tool.execute return type -> `str \| ToolResult`; ToolRegistry.execute returns ToolResult with normalization |
| `matmaster/core/agent.py` | Tool execution uses ToolResult; passes to run_post_tool_call |
| `matmaster/core/hooks.py` | Hook.post_tool_call, BaseHook, run_post_tool_call signature: result `str` -> `ToolResult`; EventEmitterHook unpacks into ToolResultEvent |
| `matmaster/types/events.py` | ToolResultEvent gains `status: str = "success"` |
| `matmaster/integration/event_payloads.py` | tool_result payload includes `status` |
| `matmaster/hooks/output_processor.py` | Update post_tool_call signature: `result: str` -> `result: ToolResult` |
| `matmaster/hooks/skill_hit.py` | Update post_tool_call signature: `result: str` -> `result: ToolResult` (result unused in body, signature-only change) |
| `matmaster/devshell/stream_hook.py` | Update post_tool_call signature + replace `result.startswith("Error executing tool")` with `result.status == "error"` |

### Test files

All test files calling `post_tool_call(tc, "some_string")` or constructing ToolResultEvent must be updated to use ToolResult:

| File | Change |
|------|--------|
| `tests/matmaster/core/test_hooks.py` | post_tool_call calls: str -> ToolResult |
| `tests/matmaster/core/test_agent.py` | RecordingHook.post_tool_call signature + assertions |
| `tests/matmaster/hooks/test_output_processor.py` | post_tool_call calls: str -> ToolResult |
| `tests/matmaster/hooks/test_skill_hit.py` | post_tool_call calls: str -> ToolResult |
| `tests/matmaster/devshell/test_stream_hook.py` | post_tool_call calls: str -> ToolResult + error detection assertions |
| `tests/matmaster/integration/test_events_to_messages.py` | ToolResultEvent construction may need status field |

## Status values

| Value | Meaning |
|-------|---------|
| `success` | Tool executed normally |
| `error` | Python exception or tool-reported error |

`blocked` (guard) and `skipped` (hook) are not included -- these paths do not emit `ToolResultEvent` in the current kernel.

## Migration strategy

- Existing tools returning `str` work without changes (ToolRegistry normalizes to ToolResult)
- Tools that need to report errors without raising exceptions can gradually migrate to returning `ToolResult(status="error", ...)`
- MCP tool wrappers and skill wrappers are priority migration targets

### Known limitation: BuiltinTool error-as-string

`BuiltinTool.execute()` (`matmaster/tools/builtin/base.py:45-51`) catches all exceptions and returns `f"Error: {e}"` as a plain string. After normalization, these become `ToolResult(status="success", content="Error: ...")` -- semantically a mismatch.

This is accepted for now. The correct fix is for `BuiltinTool.execute()` to return `ToolResult(status="error")` on exception, which is a natural part of the gradual migration. Not included in this change to keep scope focused.
