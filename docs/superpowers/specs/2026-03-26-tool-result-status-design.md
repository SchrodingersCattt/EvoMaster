# Tool Result Status Design

## Problem

Refactored `matmaster/` architecture lost the `status` field in `tool_result` SSE events. The old architecture (`origin/main`) wrapped every tool output via `format_tool_observation()` into `{"status": "success|error", "observation": ...}`, which the frontend displays and may use for future logic. The refactored code returns raw strings with no structured status signal.

Root cause: `Tool.execute()` was simplified from `(observation, info)` tuple to plain `str`, and the `format_tool_observation()` wrapper was removed during refactoring.

## Design

### ToolResult data class

New file `matmaster/types/tool_result.py`:

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ToolResult:
    status: str = "success"          # "success" | "error"
    content: str = ""                # tool output (consumed by LLM)
    info: dict[str, Any] = field(default_factory=dict)  # metadata (auto_save, error detail, etc.)
```

Uses dataclass (not Pydantic) -- internal data structure, no serialization/validation overhead needed.

### Tool protocol + ToolRegistry normalization

`Tool.execute()` return type broadened to `str | ToolResult`:

```python
class Tool(Protocol):
    def execute(self, arguments: dict[str, Any]) -> str | ToolResult: ...
```

Existing `str`-returning tools satisfy `str | ToolResult` without modification.

`ToolRegistry.execute()` returns `ToolResult` (normalization point):

- `tool.execute()` returns `str` -> `ToolResult(status="success", content=str_result)`
- `tool.execute()` returns `ToolResult` -> pass through
- Tool not found -> `ToolResult(status="error", content="Error: Tool '...' not found. Available: ...")`

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
- `OutputProcessorHook` (if it implements `post_tool_call`) must also update signature

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

Old: status nested inside `result.status` (because result was JSON-wrapped by `format_tool_observation`).
New: status is a top-level payload field, cleaner separation of concerns.

## Affected files

| File | Change |
|------|--------|
| `matmaster/types/tool_result.py` | New file: ToolResult dataclass |
| `matmaster/tools/tool_registry.py` | Tool.execute return type -> `str \| ToolResult`; ToolRegistry.execute returns ToolResult with normalization |
| `matmaster/core/agent.py` | Tool execution uses ToolResult; passes to run_post_tool_call |
| `matmaster/core/hooks.py` | Hook.post_tool_call, BaseHook, run_post_tool_call signature: result `str` -> `ToolResult`; EventEmitterHook unpacks into ToolResultEvent |
| `matmaster/types/events.py` | ToolResultEvent gains `status: str = "success"` |
| `matmaster/integration/event_payloads.py` | tool_result payload includes `status` |
| `matmaster/hooks/output_processor.py` | Update post_tool_call signature if implemented |

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
