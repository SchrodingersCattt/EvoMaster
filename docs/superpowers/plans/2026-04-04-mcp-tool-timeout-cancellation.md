# MCP Tool Timeout & Cancellation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give MCP tools timeout and cancellation support so a hung MCP server cannot stall the agent loop.

**Architecture:** Add timeout + stop_event race logic inside `LazyMCPTool.execute_with_context()`. Extract raw call logic into `_do_call()`. Thread per-server timeout from `mcp.yaml` through `Exp` into the tool constructor. No ToolRunner changes.

**Tech Stack:** Python asyncio (`wait_for`, `wait`, `create_task`), threading.Event bridge

**Spec:** `docs/superpowers/specs/2026-04-04-mcp-tool-timeout-cancellation-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `matmaster/tools/lazy_mcp.py` | Modify | Add `_do_call()`, rewrite `execute_with_context()` with race logic, add `timeout` constructor param |
| `matmaster/core/exp.py` | Modify | Read `tool_timeouts` from `mcp_config`, pass `timeout=` to `LazyMCPTool()` |
| `tests/matmaster/tools/test_lazy_mcp.py` | Modify | Add timeout, stop_event, and cancellation tests |
| `tests/matmaster/integration/test_lazy_mcp_integration.py` | Modify | Add integration test for timeout threading through Exp |

---

## Chunk 1: LazyMCPTool timeout and cancellation

### Task 1: Extract `_do_call()` from `execute()`

**Files:**
- Modify: `matmaster/tools/lazy_mcp.py:132-163`
- Test: `tests/matmaster/tools/test_lazy_mcp.py`

- [ ] **Step 1: Write failing test for `_do_call` existence**

```python
# In tests/matmaster/tools/test_lazy_mcp.py, add to TestLazyMCPToolExecution:

async def test_do_call_returns_tool_result(self):
    connector = FakeConnector()
    tool = LazyMCPTool(
        server_name="s", tool_name="s_t", remote_tool_name="t",
        description="", input_schema={}, connector=connector,
    )
    result = await tool._do_call({"key": "val"})
    assert isinstance(result, ToolResult)
    assert result.status == "success"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_lazy_mcp.py::TestLazyMCPToolExecution::test_do_call_returns_tool_result -v`
Expected: FAIL with `AttributeError: 'LazyMCPTool' object has no attribute '_do_call'`

- [ ] **Step 3: Extract `_do_call()` from `execute()`**

In `matmaster/tools/lazy_mcp.py`, extract the connection setup + path_adaptor + call_tool + format logic from `execute()` into `_do_call()`:

```python
async def _do_call(self, arguments: dict[str, Any]) -> ToolResult:
    """Raw MCP call: connect + resolve args + call_tool + format."""
    if self._connection is None:
        conn_info = await self._connector.ensure_connection(self._server_name)
        self._connection = conn_info["connection"]
        self._path_adaptor = conn_info.get("path_adaptor")

    resolved_args = arguments
    if self._path_adaptor:
        try:
            resolved_args = self._path_adaptor.resolve_args(
                workspace_path=self._connector.workspace_path,
                args=arguments,
                tool_name=self._name,
                server_name=self._server_name,
                tool_description=self._static_description,
                input_schema=self._input_schema,
                session=getattr(self._connector, "session", None),
            )
        except Exception as e:
            logger.warning("path_adaptor resolve_args failed: %s", e)

    try:
        result_content = await self._connection.call_tool(
            self._remote_tool_name, resolved_args
        )
        content = self._format_result(result_content)
        return ToolResult(status="success", content=content)
    except RuntimeError as e:
        return ToolResult(status="error", content=str(e))

async def execute(self, arguments: dict[str, Any]) -> ToolResult:
    return await self._do_call(arguments)
```

- [ ] **Step 4: Run full existing test suite to verify no regression**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_lazy_mcp.py -v`
Expected: ALL PASS (including the new test)

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/lazy_mcp.py tests/matmaster/tools/test_lazy_mcp.py
git commit -m "refactor: extract LazyMCPTool._do_call() from execute()"
```

---

### Task 2: Add `timeout` constructor parameter

**Files:**
- Modify: `matmaster/tools/lazy_mcp.py:34-76`
- Test: `tests/matmaster/tools/test_lazy_mcp.py`

- [ ] **Step 1: Write failing test for timeout param**

```python
# In tests/matmaster/tools/test_lazy_mcp.py, add new test class:

class TestLazyMCPToolTimeout:
    def test_default_timeout(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
        )
        assert tool._timeout == 120.0

    def test_custom_timeout_via_constructor(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
            timeout=300.0,
        )
        assert tool._timeout == 300.0

    def test_runtime_meta_timeout_overrides_default(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
            runtime_meta={"timeout": 60},
        )
        assert tool._timeout == 60.0

    def test_runtime_meta_timeout_beats_constructor(self):
        """runtime_meta['timeout'] (per-tool) takes precedence over constructor timeout (per-server)."""
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
            timeout=300.0,
            runtime_meta={"timeout": 45},
        )
        assert tool._timeout == 45.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_lazy_mcp.py::TestLazyMCPToolTimeout -v`
Expected: FAIL (no `timeout` parameter, no `_timeout` attribute)

- [ ] **Step 3: Add timeout parameter to `__init__`**

In `matmaster/tools/lazy_mcp.py`, add module-level constant and modify `__init__`:

```python
_DEFAULT_MCP_TOOL_TIMEOUT: float = 120.0  # seconds

class LazyMCPTool:
    def __init__(
        self,
        server_name: str,
        tool_name: str,
        remote_tool_name: str,
        description: str,
        input_schema: dict,
        connector: Any,
        runtime_meta: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        # ... existing code ...
        meta = runtime_meta or {}
        # Timeout resolution: runtime_meta (per-tool) > constructor param (per-server) > default
        if meta.get("timeout") is not None:
            self._timeout = float(meta["timeout"])
        elif timeout is not None:
            self._timeout = float(timeout)
        else:
            self._timeout = _DEFAULT_MCP_TOOL_TIMEOUT
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_lazy_mcp.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/lazy_mcp.py tests/matmaster/tools/test_lazy_mcp.py
git commit -m "feat: add per-tool timeout parameter to LazyMCPTool"
```

---

### Task 3: Implement `execute_with_context()` race logic

**Files:**
- Modify: `matmaster/tools/lazy_mcp.py:164-169`
- Test: `tests/matmaster/tools/test_lazy_mcp.py`

- [ ] **Step 1: Write failing tests for timeout and stop_event**

```python
# In tests/matmaster/tools/test_lazy_mcp.py, add:

import asyncio
import threading
from matmaster.types.tool_spec import ToolExecutionContext


class SlowConnector(FakeConnector):
    """Connector whose call_tool sleeps forever."""
    def __init__(self):
        super().__init__()
        self._mock_conn.call_tool = AsyncMock(
            side_effect=lambda *a, **kw: asyncio.sleep(9999)
        )


class TestLazyMCPToolExecuteWithContext:
    async def test_timeout_fires_on_hung_server(self):
        connector = SlowConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
            timeout=0.5,
        )
        exec_ctx = ToolExecutionContext()
        result = await tool.execute_with_context({}, exec_ctx)
        assert result.status == "error"
        assert "timed out" in result.content

    async def test_stop_event_before_call(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
        )
        stop = threading.Event()
        stop.set()
        exec_ctx = ToolExecutionContext(stop_event=stop)
        result = await tool.execute_with_context({}, exec_ctx)
        assert result.status == "cancelled"

    async def test_stop_event_during_call(self):
        connector = SlowConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
            timeout=30.0,  # long timeout so stop_event wins
        )
        stop = threading.Event()
        exec_ctx = ToolExecutionContext(stop_event=stop)

        async def set_stop_later():
            await asyncio.sleep(0.2)
            stop.set()

        asyncio.create_task(set_stop_later())
        result = await tool.execute_with_context({}, exec_ctx)
        assert result.status == "cancelled"

    async def test_best_effort_cancel_message(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
            runtime_meta={"stop_mode": "best_effort"},
        )
        stop = threading.Event()
        stop.set()
        exec_ctx = ToolExecutionContext(stop_event=stop)
        result = await tool.execute_with_context({}, exec_ctx)
        assert "best-effort" in result.content

    async def test_cancellable_cancel_message(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
            runtime_meta={"stop_mode": "cancellable"},
        )
        stop = threading.Event()
        stop.set()
        exec_ctx = ToolExecutionContext(stop_event=stop)
        result = await tool.execute_with_context({}, exec_ctx)
        assert result.content == "Run cancelled."

    async def test_normal_execution_still_works(self):
        connector = FakeConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
        )
        exec_ctx = ToolExecutionContext()
        result = await tool.execute_with_context({"key": "val"}, exec_ctx)
        assert result.status == "success"

    async def test_no_context_uses_timeout_only(self):
        connector = SlowConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
            timeout=0.5,
        )
        result = await tool.execute_with_context({}, None)
        assert result.status == "error"
        assert "timed out" in result.content

    async def test_race_timeout_and_stop_simultaneous(self):
        """Both timeout and stop_event fire near-simultaneously. No crash."""
        connector = SlowConnector()
        tool = LazyMCPTool(
            server_name="s", tool_name="s_t", remote_tool_name="t",
            description="", input_schema={}, connector=connector,
            timeout=0.3,
        )
        stop = threading.Event()
        exec_ctx = ToolExecutionContext(stop_event=stop)

        async def set_stop_later():
            await asyncio.sleep(0.3)
            stop.set()

        asyncio.create_task(set_stop_later())
        result = await tool.execute_with_context({}, exec_ctx)
        assert result.status in ("error", "cancelled")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_lazy_mcp.py::TestLazyMCPToolExecuteWithContext -v`
Expected: FAIL (current `execute_with_context` ignores exec_ctx)

- [ ] **Step 3: Implement the race logic**

In `matmaster/tools/lazy_mcp.py`, add the stop_event polling helper and rewrite `execute_with_context`:

```python
_STOP_POLL_INTERVAL: float = 0.5  # seconds


async def _wait_for_stop(stop_event: threading.Event) -> None:
    """Poll threading.Event in async context. Returns when event is set."""
    while not stop_event.is_set():
        await asyncio.sleep(_STOP_POLL_INTERVAL)
```

Replace `execute_with_context`:

```python
async def execute_with_context(
    self,
    arguments: dict[str, Any],
    exec_ctx: ToolExecutionContext | None,
) -> ToolResult:
    stop_event = getattr(exec_ctx, "stop_event", None) if exec_ctx else None

    # Pre-check: already cancelled
    if stop_event is not None and stop_event.is_set():
        return self._cancelled_result()

    # Wrap _do_call with timeout
    call_coro = asyncio.wait_for(self._do_call(arguments), timeout=self._timeout)

    if stop_event is None:
        # No stop_event: just timeout
        try:
            return await call_coro
        except asyncio.TimeoutError:
            return ToolResult(
                status="error",
                content=f"MCP tool {self._name} timed out after {self._timeout:.0f}s",
                meta={"layer": "tool"},
            )

    # Race: call vs stop_event
    call_task = asyncio.create_task(call_coro)
    stop_task = asyncio.create_task(_wait_for_stop(stop_event))

    done, pending = await asyncio.wait(
        {call_task, stop_task}, return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    if call_task in done:
        try:
            return call_task.result()
        except asyncio.TimeoutError:
            return ToolResult(
                status="error",
                content=f"MCP tool {self._name} timed out after {self._timeout:.0f}s",
                meta={"layer": "tool"},
            )

    # stop_event won
    return self._cancelled_result()

def _cancelled_result(self) -> ToolResult:
    if self._stop_mode == "cancellable":
        return ToolResult(status="cancelled", content="Run cancelled.")
    return ToolResult(
        status="cancelled",
        content="Cancellation requested (best-effort). Tool may have partially completed.",
    )
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_lazy_mcp.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/lazy_mcp.py tests/matmaster/tools/test_lazy_mcp.py
git commit -m "feat: add timeout + stop_event race logic to LazyMCPTool.execute_with_context"
```

---

## Chunk 2: Exp timeout threading + integration test

### Task 4: Thread timeout from mcp.yaml through Exp

**Files:**
- Modify: `matmaster/core/exp.py:619-626`
- Test: `tests/matmaster/integration/test_lazy_mcp_integration.py`

- [ ] **Step 1: Write failing integration test**

```python
# In tests/matmaster/integration/test_lazy_mcp_integration.py, add:

class TestLazyMCPTimeoutThreading:
    async def test_tool_timeouts_from_mcp_yaml(self, tmp_path):
        """tool_timeouts in mcp.yaml reaches LazyMCPTool._timeout."""
        skill_dir = tmp_path / 'skills' / 'test-skill'
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text(
            '---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nBody\n'
        )

        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        schemas = [{'name': 'build_bulk', 'description': 'Build', 'input_schema': {}}]
        (cache_dir / 'mat_sg.json').write_text(json.dumps(schemas))

        (tmp_path / 'mcp_config.json').write_text(json.dumps({'mcpServers': {}}))
        (tmp_path / 'mcp.yaml').write_text(
            _yaml.dump({
                'path_adaptor': 'calculation',
                'calculation_servers': ['mat_sg'],
                'tool_timeouts': {'mat_sg': 300},
            })
        )

        cfg = ExpConfig.model_validate({
            'name': 'test',
            'skills': {
                'enabled': True,
                'skills_root': str(tmp_path / 'skills'),
                'cache_dir': str(cache_dir),
                'config_dir': str(tmp_path),
                'mcp_config_file': 'mcp_config.json',
                'mcp_runtime_file': 'mcp.yaml',
            },
        })
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()

        exp._init_skill_tools(ctx, registry)
        await _execute_use_skill(registry, skill_name="test-skill")

        from matmaster.tools.lazy_mcp import LazyMCPTool
        lazy = registry._tools['mat_sg_build_bulk']
        assert isinstance(lazy, LazyMCPTool)
        assert lazy._timeout == 300.0

    async def test_default_timeout_when_not_in_config(self, tmp_path):
        """Server not in tool_timeouts gets default 120s."""
        skill_dir = tmp_path / 'skills' / 'test-skill'
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text(
            '---\nname: test-skill\ndescription: Test\nmcp_server: mat_sg\n---\nBody\n'
        )

        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        schemas = [{'name': 'build_bulk', 'description': 'Build', 'input_schema': {}}]
        (cache_dir / 'mat_sg.json').write_text(json.dumps(schemas))

        (tmp_path / 'mcp_config.json').write_text(json.dumps({'mcpServers': {}}))
        (tmp_path / 'mcp.yaml').write_text(
            _yaml.dump({
                'path_adaptor': 'calculation',
                'calculation_servers': ['mat_sg'],
                # no tool_timeouts key
            })
        )

        cfg = ExpConfig.model_validate({
            'name': 'test',
            'skills': {
                'enabled': True,
                'skills_root': str(tmp_path / 'skills'),
                'cache_dir': str(cache_dir),
                'config_dir': str(tmp_path),
                'mcp_config_file': 'mcp_config.json',
                'mcp_runtime_file': 'mcp.yaml',
            },
        })
        exp = Exp(cfg)
        registry = ToolRegistry()
        ctx = MagicMock(spec=PlaygroundContext)
        ctx.session = MagicMock()

        exp._init_skill_tools(ctx, registry)
        await _execute_use_skill(registry, skill_name="test-skill")

        from matmaster.tools.lazy_mcp import LazyMCPTool, _DEFAULT_MCP_TOOL_TIMEOUT
        lazy = registry._tools['mat_sg_build_bulk']
        assert lazy._timeout == _DEFAULT_MCP_TOOL_TIMEOUT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/integration/test_lazy_mcp_integration.py::TestLazyMCPTimeoutThreading -v`
Expected: FAIL (`_timeout` is always default because exp.py doesn't pass timeout)

- [ ] **Step 3: Modify Exp to pass timeout**

In `matmaster/core/exp.py`, inside the `on_skill_hit` closure (around line 606-631), read `tool_timeouts` from `mcp_config` and pass to `LazyMCPTool`:

```python
def on_skill_hit(mcp_server: str) -> None:
    schemas = schema_cache.load(mcp_server)
    if not schemas:
        self.logger.warning(
            "No cached schema for MCP server '%s', tools not injected",
            mcp_server,
        )
        return
    server_timeout = mcp_config.get("tool_timeouts", {}).get(mcp_server)
    for tool_schema in schemas:
        original_name = tool_schema['name']
        prefixed_name = f'{mcp_server}_{original_name}'
        if prefixed_name in registry:
            continue
        lazy_tool = LazyMCPTool(
            server_name=mcp_server,
            tool_name=prefixed_name,
            remote_tool_name=original_name,
            description=tool_schema.get('description', ''),
            input_schema=tool_schema.get('input_schema', {}),
            connector=connector,
            timeout=server_timeout,
        )
        if catalog is not None:
            catalog.register_overlay(lazy_tool, source='mcp')
        else:
            registry.register(lazy_tool, source='mcp')
```

- [ ] **Step 4: Run all integration tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/integration/test_lazy_mcp_integration.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full lazy_mcp test suite for regression**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/test_lazy_mcp.py tests/matmaster/integration/test_lazy_mcp_integration.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/exp.py tests/matmaster/integration/test_lazy_mcp_integration.py
git commit -m "feat: thread per-server MCP tool timeout from mcp.yaml through Exp"
```
