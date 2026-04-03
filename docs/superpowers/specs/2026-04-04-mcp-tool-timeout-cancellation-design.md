# MCP Tool Timeout & Cancellation Design

## Problem

`LazyMCPTool` is a second-class citizen in the tool runtime:

1. `execute()` calls `MCPConnection.call_tool()` with no timeout — blocks indefinitely if the MCP server hangs.
2. `execute_with_context()` ignores `exec_ctx.stop_event` entirely — cancellation signals never reach MCP tools.
3. `FullToolRunner.execute_batch()` uses `asyncio.gather(*)` without per-tool timeout — one stuck tool blocks the entire batch.

Combined effect: a single unresponsive MCP server can stall the agent loop forever.

Built-in tools (e.g. BashTool) handle both timeout and stop_event correctly. MCP tools should have the same runtime guarantees.

## Design

### Scope

| Change | File | Description |
|--------|------|-------------|
| MCP tool timeout + stop_event | `matmaster/tools/lazy_mcp.py` | `execute_with_context()` gains race logic |
| Timeout configuration | `matmaster/tools/lazy_mcp.py` | Per-tool via `runtime_meta["timeout"]`, default 120s |
| ToolRunner fallback timeout | `matmaster/core/tool_runner.py` | `_execute_one()` wraps executor with 600s fallback |

| Not changed | Reason |
|-------------|--------|
| `matmaster/mcp/connection.py` | `call_tool()` stays pure RPC; timeout is an execution-layer concern |
| `matmaster/types/tool_spec.py` | No new fields added to ToolSpec/ToolBinding |
| Progress reporting | Deferred; `on_progress` plumbing exists but is not wired up in this change |

### 1. LazyMCPTool.execute_with_context()

Current implementation (no-op passthrough):

```python
async def execute_with_context(self, arguments, exec_ctx):
    return await self.execute(arguments)
```

New behavior:

```
execute_with_context(arguments, exec_ctx)
  │
  ├─ 1. Pre-check: if stop_event is already set → return ToolResult(status="cancelled")
  │
  ├─ 2. Race execution:
  │     asyncio.wait_for(self._do_call(arguments), timeout=self._timeout)
  │     concurrently poll stop_event via bridge coroutine (0.5s interval)
  │     ├─ Normal completion → ToolResult
  │     ├─ asyncio.TimeoutError → ToolResult(status="error", "MCP tool {name} timed out after {N}s")
  │     └─ stop_event triggered → ToolResult(status="cancelled", "Run cancelled")
  │
  └─ 3. execute() remains unchanged (backward-compatible, no timeout, no cancellation)
```

#### stop_event bridging

`exec_ctx.stop_event` is a `threading.Event`. It cannot be directly awaited in asyncio. The bridge pattern:

```python
async def _wait_for_stop(stop_event: threading.Event) -> None:
    """Poll threading.Event in async context. Returns when event is set."""
    while not stop_event.is_set():
        await asyncio.sleep(0.5)
```

The race uses `asyncio.wait(..., return_when=FIRST_COMPLETED)` with two tasks:
- The actual `call_tool` coroutine wrapped in `asyncio.wait_for` (for timeout)
- The `_wait_for_stop` coroutine (for cancellation)

Whichever finishes first wins; the other is cancelled.

When stop_event is None (e.g. called without context), only `asyncio.wait_for` with timeout is used — no stop polling.

#### Timeout configuration

```python
_DEFAULT_MCP_TOOL_TIMEOUT: float = 120.0  # seconds

class LazyMCPTool:
    def __init__(self, ..., runtime_meta=None):
        ...
        self._timeout: float = float(meta.get("timeout", _DEFAULT_MCP_TOOL_TIMEOUT))
```

Per-tool timeout is declared in `runtime_meta["timeout"]` (seconds). This is read from the lazy MCP cache / mcp_config and set at tool construction time.

#### Internal refactor

Extract the raw `call_tool` + path_adaptor + format logic from `execute()` into a private `_do_call(arguments) -> ToolResult` method. Both `execute()` and `execute_with_context()` call `_do_call`:

- `execute()`: `await self._do_call(arguments)` — no timeout, no cancellation (backward compat)
- `execute_with_context()`: wraps `_do_call` with race logic

### 2. FullToolRunner._execute_one() fallback timeout

Current implementation:

```python
try:
    tr = await instance.tool_executor(effective_args, exec_ctx)
except Exception as e:
    tr = ToolResult.from_error(tc.name, e)
```

New behavior:

```python
_RUNNER_FALLBACK_TIMEOUT: float = 600.0  # 10 minutes

try:
    tr = await asyncio.wait_for(
        instance.tool_executor(effective_args, exec_ctx),
        timeout=_RUNNER_FALLBACK_TIMEOUT,
    )
except asyncio.TimeoutError:
    tr = ToolResult(
        status="error",
        content=f"Tool {tc.name} execution timed out after {_RUNNER_FALLBACK_TIMEOUT:.0f}s",
        meta={"layer": "runner"},
    )
except Exception as e:
    tr = ToolResult.from_error(tc.name, e)
```

This is a last-resort safety net. For MCP tools, the per-tool 120s timeout fires first. The 600s fallback catches pathological cases in any tool type.

### 3. Timeout layering

```
┌─────────────────────────────────────┐
│ FullToolRunner._execute_one()       │  600s fallback (all tools)
│  ┌───────────────────────────────┐  │
│  │ LazyMCPTool.execute_with_ctx  │  │  120s default / per-tool (MCP only)
│  │  ┌─────────────────────────┐  │  │
│  │  │ MCPConnection.call_tool │  │  │  no timeout (pure RPC)
│  │  └─────────────────────────┘  │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

MCP tool timeout (120s) fires well before runner fallback (600s). Built-in tools have their own mechanisms (e.g. BashTool subprocess timeout) and are only covered by the 600s fallback.

## Error surfaces

| Scenario | Result |
|----------|--------|
| MCP server hangs | `status="error"`, "timed out after 120s", `meta={"layer": "tool"}` |
| stop_event set during MCP call | `status="cancelled"`, "Run cancelled" |
| stop_event set before MCP call | `status="cancelled"`, "Run cancelled" (pre-check) |
| Any tool hangs past 600s | `status="error"`, "timed out after 600s", `meta={"layer": "runner"}` |
| MCP server returns isError | `status="error"` (existing behavior, unchanged) |
| MCP connection failure | `status="error"` (existing behavior, unchanged) |

## Testing strategy

- Unit test: `LazyMCPTool.execute_with_context()` with a mock connection that sleeps forever — verify timeout fires and returns error.
- Unit test: `LazyMCPTool.execute_with_context()` with stop_event set before call — verify immediate cancelled return.
- Unit test: `LazyMCPTool.execute_with_context()` with stop_event set during call — verify cancelled return within ~0.5s.
- Unit test: `FullToolRunner._execute_one()` with a mock executor that sleeps forever — verify 600s fallback (use short timeout in test).
- Integration test: existing MCP tool tests continue to pass (execute() path unchanged).
