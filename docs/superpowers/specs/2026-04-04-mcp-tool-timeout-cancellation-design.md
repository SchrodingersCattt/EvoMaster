# MCP Tool Timeout & Cancellation Design

## Problem

`LazyMCPTool` is a second-class citizen in the tool runtime:

1. `execute()` calls `MCPConnection.call_tool()` with no timeout — blocks indefinitely if the MCP server hangs.
2. `execute_with_context()` ignores `exec_ctx.stop_event` entirely — cancellation signals never reach MCP tools.
3. `FullToolRunner.execute_batch()` uses `asyncio.gather(*)` — one stuck MCP tool blocks the entire batch because MCP tools lack their own timeout.

Combined effect: a single unresponsive MCP server can stall the agent loop forever.

Built-in tools (e.g. BashTool) handle both timeout and stop_event correctly. MCP tools should have the same runtime guarantees.

## Design

### Scope

| Change | File | Description |
|--------|------|-------------|
| MCP tool timeout + stop_event | `matmaster/tools/lazy_mcp.py` | `execute_with_context()` gains race logic |
| Timeout configuration | `matmaster/tools/lazy_mcp.py` | Per-server via `mcp.yaml` tool_timeouts, per-tool via runtime_meta, default 120s |
| Pass timeout at construction | `matmaster/core/exp.py` | `_setup_lazy_mcp_tools()` reads mcp_config timeout and passes to `LazyMCPTool()` |

| Not changed | Reason |
|-------------|--------|
| `matmaster/mcp/connection.py` | `call_tool()` stays pure RPC; timeout is an execution-layer concern |
| `matmaster/types/tool_spec.py` | No new fields added to ToolSpec/ToolBinding |
| `matmaster/core/tool_runner.py` | No generic fallback timeout; MCP tools self-guard via their own timeout. Built-in tools have their own mechanisms (BashTool subprocess timeout, MonitorJobTool long-running by design). A generic `asyncio.wait_for` wrapper would not stop `to_thread` worker threads and would cause premature scheduler ticket release. |
| `matmaster/tools/cache_mcp_schemas.py` | Schema cache format unchanged; timeout config comes from `mcp.yaml`, not the cache |
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
  │     ├─ asyncio.TimeoutError → ToolResult(status="error", "MCP tool {name} timed out after {N}s", meta={"layer": "tool"})
  │     └─ stop_event triggered → ToolResult(status="cancelled", message per stop_mode)
  │
  └─ 3. execute() remains unchanged (backward-compatible, no timeout, no cancellation)
```

Cancellation message respects `self._stop_mode` (default `"best_effort"` for MCP tools):
- `stop_mode="cancellable"`: `"Run cancelled."`
- `stop_mode="best_effort"`: `"Cancellation requested (best-effort). Tool may have partially completed."`

This aligns with the existing Phase 1 semantics in `FullToolRunner.execute_batch()` (tool_runner.py:193-205).

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
    def __init__(self, ..., timeout: float | None = None):
        ...
        meta = runtime_meta or {}
        # Resolution: runtime_meta (per-tool) > constructor param (per-server) > default
        if meta.get("timeout") is not None:
            self._timeout = float(meta["timeout"])
        elif timeout is not None:
            self._timeout = float(timeout)
        else:
            self._timeout = _DEFAULT_MCP_TOOL_TIMEOUT
```

Timeout resolution order (first non-None wins):

1. `runtime_meta["timeout"]` — per-tool override (already supported by `__init__`)
2. `mcp.yaml` → `tool_timeouts.<server_name>` — per-server override
3. `_DEFAULT_MCP_TOOL_TIMEOUT` (120s) — global default

The schema cache (`cache_mcp_schemas.py` / `schema_cache.py`) only stores `name`/`description`/`input_schema` and is not changed. Timeout config comes from `mcp.yaml` at `Exp` assembly time.

Construction call site change in `Exp._setup_lazy_mcp_tools()` (exp.py:619-626):

```python
# Read per-server timeout from mcp_config
server_timeout = mcp_config.get("tool_timeouts", {}).get(mcp_server)

lazy_tool = LazyMCPTool(
    ...,
    timeout=server_timeout,  # None falls back to default inside LazyMCPTool
)
```

Example `mcp.yaml` configuration:

```yaml
tool_timeouts:
  dpcloud_server: 300   # DPCloud tools may be slow
  filesystem: 30        # local FS tools should be fast
  # servers not listed here use the 120s default
```

#### Internal refactor

Extract the raw `call_tool` + path_adaptor + format logic from `execute()` into a private `_do_call(arguments) -> ToolResult` method. Both `execute()` and `execute_with_context()` call `_do_call`:

- `execute()`: `await self._do_call(arguments)` — no timeout, no cancellation (backward compat)
- `execute_with_context()`: wraps `_do_call` with race logic

### 2. Timeout layering

Each tool type is responsible for its own timeout. No generic ToolRunner fallback.

```
FullToolRunner._execute_one()          no generic timeout
  ├─ LazyMCPTool.execute_with_ctx      120s default / per-server (MCP tools)
  │    └─ MCPConnection.call_tool      no timeout (pure RPC)
  ├─ BashTool.execute_with_context     subprocess timeout (user-specified)
  └─ MonitorJobTool.execute            long-running by design, no timeout
```

Why no ToolRunner fallback: built-in tools execute via `asyncio.to_thread()`. `asyncio.wait_for` would cancel the await but not the underlying worker thread, causing premature scheduler ticket release while the thread continues running. Each tool type manages its own timeout at the appropriate level.

## Error surfaces

| Scenario | Result |
|----------|--------|
| MCP server hangs | `status="error"`, "MCP tool {name} timed out after {N}s", `meta={"layer": "tool"}` |
| stop_event set during MCP call (best_effort) | `status="cancelled"`, "Cancellation requested (best-effort). Tool may have partially completed." |
| stop_event set during MCP call (cancellable) | `status="cancelled"`, "Run cancelled." |
| stop_event set before MCP call | `status="cancelled"`, message per stop_mode (pre-check) |
| MCP server returns isError | `status="error"` (existing behavior, unchanged) |
| MCP connection failure | `status="error"` (existing behavior, unchanged) |

## Testing strategy

- Unit test: `LazyMCPTool.execute_with_context()` with a mock connection that sleeps forever — verify timeout fires and returns error.
- Unit test: `LazyMCPTool.execute_with_context()` with stop_event set before call — verify immediate cancelled return with stop_mode-appropriate message.
- Unit test: `LazyMCPTool.execute_with_context()` with stop_event set during call — verify cancelled return within ~0.5s.
- Unit test: custom timeout via constructor — verify per-server timeout overrides default.
- Unit test: race between timeout and stop_event triggering near-simultaneously — verify deterministic behavior (no double-result, no unhandled exception).
- Integration test: `Exp._setup_lazy_mcp_tools()` reads `tool_timeouts` from mcp_config and passes to `LazyMCPTool` constructor — verify non-None timeout reaches the tool.
- Integration test: existing MCP tool tests continue to pass (`execute()` path unchanged).

### Known boundaries

- `LazyMCPConnector.ensure_connection()` blocks on `fut.result(timeout=60)` during first-use connection setup. This 60s timeout is outside the scope of the async race in `execute_with_context()` and is not changed. The lazy-connect phase has its own timeout and does not benefit from stop_event cancellation. If end-to-end cancellation coverage for the connect phase is needed, it should be a separate change.
