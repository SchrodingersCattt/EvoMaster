# Testing Patterns

**Analysis Date:** 2026-04-02

## Test Framework

**Runner:**
- pytest >= 9.0.2
- pytest-asyncio >= 0.24.0
- Config: `pytest.ini` at project root + `pyproject.toml` `[tool.pytest.ini_options]`

**Configuration (`pytest.ini`):**
```ini
[pytest]
minversion = 1.0
addopts = -s                    # Show print/log output
pythonpath = .                  # Project root on sys.path
asyncio_mode = auto             # Auto-detect async test functions
testpaths = tests               # Test discovery root
```

**Assertion Library:**
- Python standard `assert` statements
- `pytest.raises` for expected exceptions

**Run Commands:**
```bash
uv run pytest                           # Run all tests
uv run pytest tests/matmaster/core/     # Run core tests only
uv run pytest -k "test_natural_finish"  # Run tests matching pattern
uv run pytest tests/matmaster/tools/test_tool_registry.py  # Single file
uv run pytest --tb=short               # Short traceback format
```

**Note:** No coverage configuration detected. No `pytest-cov` in dependencies.

## Test File Organization

**Location:**
- All tests in `tests/` directory at project root, mirroring `matmaster/` package structure
- Separate from source code (not co-located)

**Naming:**
- Test files: `test_*.py` (enforced by `name-tests-test --pytest-test-first` pre-commit hook)
- Test classes: `class Test*:` (PascalCase with `Test` prefix)
- Test methods: `async def test_*` or `def test_*`
- Helper files excluded from naming check: `agent_kernel_test_helpers.py`

**Directory Structure:**
```
tests/
├── conftest.py                          # Root-level mock factories (MockAsyncLLMProvider, MockAsyncTool, MockAsyncHook)
├── fixtures/                            # Static test fixtures
│   └── evomaster_multi_agent/prompts/
├── matmaster/
│   ├── core/                            # 17 test files
│   │   ├── conftest.py                  # MockLLMProvider, make_tool_call(), build_mock_spec()
│   │   ├── agent_kernel_test_helpers.py # StreamingProvider, ToolCallingProvider, _make_spec(), _make_tool_registry()
│   │   ├── test_agent_kernel.py         # Kernel termination, hooks, guards
│   │   ├── test_agent_kernel_extended.py
│   │   ├── test_agent_kernel_stream.py
│   │   ├── test_bus.py
│   │   ├── test_context_builder.py
│   │   ├── test_context_compactor.py
│   │   ├── test_exp.py
│   │   ├── test_exp_skills.py
│   │   ├── test_guard_pipeline.py
│   │   ├── test_guard_injection.py
│   │   ├── test_hooks.py
│   │   ├── test_playground.py
│   │   ├── test_playground_manager.py
│   │   ├── test_playground_no_evomaster.py
│   │   ├── test_tool_runner.py
│   │   └── test_local_session_stop.py
│   ├── tools/                           # 24 test files
│   │   ├── conftest.py                  # MockTool satisfying Tool Protocol
│   │   ├── test_tool_registry.py
│   │   ├── test_tool_result.py
│   │   ├── test_bash_tool.py
│   │   ├── test_edit_tool.py
│   │   ├── test_read_tool.py
│   │   ├── test_write_tool.py
│   │   ├── test_glob_tool.py
│   │   ├── test_grep_tool.py
│   │   ├── test_listdir_tool.py
│   │   ├── test_spawn_tool.py
│   │   ├── test_lazy_mcp.py
│   │   ├── test_skill_tool_callback.py
│   │   ├── test_schema_cache.py
│   │   ├── test_cache_mcp_schemas.py
│   │   ├── test_web_fetch_tool.py
│   │   ├── test_web_search_tool.py
│   │   ├── test_monitor_job.py
│   │   ├── test_task_tools.py
│   │   ├── test_tool_catalog.py
│   │   ├── test_tool_descriptions.py
│   │   ├── test_builtin_base.py
│   │   ├── test_script_env.py
│   │   ├── test_read_tracker.py
│   │   └── test_skill_meta_extras.py
│   ├── types/                           # 13 test files
│   │   ├── test_events.py
│   │   ├── test_messages.py
│   │   ├── test_context.py
│   │   ├── test_runtime.py
│   │   ├── test_guards.py
│   │   ├── test_errors.py
│   │   ├── test_llm_provider.py
│   │   ├── test_session_protocol.py
│   │   ├── test_tool_spec.py
│   │   ├── test_tool_decision.py
│   │   ├── test_topology.py
│   │   ├── test_terminal_event_names.py
│   │   └── test_worker_registry.py
│   ├── integration/                     # 19 test files
│   │   ├── test_e2e_minimal.py
│   │   ├── test_e2e_mat_master.py
│   │   ├── test_subagent_spawn.py
│   │   ├── test_subagent_event_routing.py
│   │   ├── test_event_router.py
│   │   ├── test_event_payloads.py
│   │   ├── test_events_to_messages.py
│   │   ├── test_lazy_mcp_integration.py
│   │   ├── test_llm_factory.py
│   │   ├── test_pipeline_alignment.py
│   │   ├── test_quota_pipeline.py
│   │   ├── test_sse_skill_hit.py
│   │   ├── test_stream_timeout_retry.py
│   │   ├── test_upstream_scenarios.py
│   │   ├── test_workspace_handler.py
│   │   ├── test_bohrium_execution_contract.py
│   │   ├── test_compaction_real_api.py
│   │   ├── test_direct_toml_prompt.py
│   │   └── test_agent_run_service_workspace_upload.py
│   ├── hooks/                           # 4 test files
│   │   ├── test_skill_hit.py
│   │   ├── test_assistant_state.py
│   │   ├── test_confirmation.py
│   │   └── test_output_processor.py
│   ├── providers/                       # 2 test files
│   │   ├── test_openai_provider.py
│   │   └── test_llm_factory.py
│   ├── sessions/                        # 3 test files
│   │   ├── test_local.py
│   │   ├── test_ssh_session.py
│   │   └── test_sftp_pool.py
│   ├── devshell/                        # 7 test files
│   │   ├── test_runner.py
│   │   ├── test_repl.py
│   │   ├── test_config.py
│   │   ├── test_event_logger.py
│   │   ├── test_stream_hook.py
│   │   ├── test_compaction_via_devshell.py
│   │   └── test_integration.py
│   ├── config/                          # 4 test files
│   │   ├── test_exp.py
│   │   ├── test_llm.py
│   │   ├── test_loader.py
│   │   └── test_config_consolidation.py
│   ├── adaptors/calculation/            # 3 test files
│   │   ├── test_env_config.py
│   │   ├── test_job_service.py
│   │   └── test_path_adaptor.py
│   └── mcp/                             # 2 test files
│       ├── test_connection.py
│       └── test_manager.py
└── utils/                               # Utility tests
```

**Total: ~115 test files (excluding conftest.py and __init__.py)**

## Test Structure

**Class-based organization (primary pattern):**
Tests are grouped into classes by behavior/scenario. Each class groups related test methods:

```python
class TestNaturalFinish:
    """LLM returns no tool_calls -> FinishEvent(reason='natural')."""

    @pytest.mark.asyncio
    async def test_natural_finish(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content='Hello'),
            StreamChunk(finish_reason='stop'),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, 'test task')

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == 'natural'
        assert result.result.final_content == 'Hello'
```

**Key patterns:**
- Test classes are not test fixtures -- no `setUp`/`tearDown`, pure method grouping
- `asyncio_mode = auto` means `@pytest.mark.asyncio` is optional but sometimes present
- `self` is unused in test methods (class is purely organizational)
- Type return annotation `-> None` on test methods

## Mocking

**Three-tier mock hierarchy:**

**1. Protocol-conforming mock classes (conftest.py, root level):**
```python
# tests/conftest.py
class MockAsyncLLMProvider:
    """Async mock satisfying LLMProvider Protocol for testing."""
    def __init__(self, *, chat_response=None, stream_chunks=None):
        self._chat_response = chat_response or LLMResponse(content="mock response", finish_reason="stop")
        self._stream_chunks = stream_chunks or [StreamChunk(content="hello", finish_reason="stop")]

    async def __aenter__(self): return self
    async def __aexit__(self, *exc): pass
    async def chat(self, messages, tools=None): return self._chat_response
    async def chat_stream(self, messages, tools=None, *, timeout=None):
        for chunk in self._stream_chunks:
            yield chunk

class MockAsyncTool:
    """Async mock satisfying Tool Protocol for testing."""
    # ... name, description, json_schema properties + execute()

class MockAsyncHook:
    """Async mock satisfying Hook Protocol for testing."""
    # ... all 7 hook methods with default returns
```

**2. Specialized mock providers (test helper modules):**
```python
# tests/matmaster/core/agent_kernel_test_helpers.py
class StreamingProvider:
    """Mock provider that yields specific StreamChunk sequences."""

class ToolCallingProvider:
    """Mock provider that returns tool calls for N turns, then stops."""

class _CatchAllTool:
    """Tool that accepts any name and records calls for assertions."""
    def __init__(self, result='tool result'):
        self.calls: list[tuple[str, dict[str, Any]]] = []
```

**3. unittest.mock for patching external dependencies:**
```python
# tests/matmaster/integration/test_subagent_spawn.py
from unittest.mock import AsyncMock, create_autospec, patch

with (
    patch("matmaster.config.loader.load_exp_config") as mock_load,
    patch.object(Exp, "run", new_callable=AsyncMock, return_value=mock_kr),
):
    mock_load.return_value = ExpConfig(name="explore")
    # ... test assertions
```

**What to mock:**
- LLM providers (always -- never call real LLM APIs in tests)
- Session implementations (local, SSH)
- MessageBus (use real instance or MagicMock depending on test scope)
- Config loaders (for isolation)

**What NOT to mock:**
- Pydantic models (construct real instances -- they are data)
- ToolRegistry (use real instance with mock tools registered)
- Kernel (use real AgentKernel with mock provider/tools)

## Fixtures and Factories

**Root conftest.py (`tests/conftest.py`):**
Provides universal Protocol-conforming mocks as fixtures:
```python
@pytest.fixture
def async_llm_provider() -> MockAsyncLLMProvider:
    return MockAsyncLLMProvider()

@pytest.fixture
def async_tool() -> MockAsyncTool:
    return MockAsyncTool()

@pytest.fixture
def async_hook() -> MockAsyncHook:
    return MockAsyncHook()
```

**Core conftest.py (`tests/matmaster/core/conftest.py`):**
Provides kernel-specific helpers:
```python
@pytest.fixture
def mock_tool_call() -> ToolCallData:
    return ToolCallData(id="tc-1", name="test_tool", arguments={"key": "value"})

def make_tool_call(name="test_tool", args=None, call_id="tc-1") -> ToolCallData:
    """Factory function for ToolCallData instances."""

def build_mock_spec(*, llm_provider=None, guards=None, hooks=None, max_turns=10, system_prompt="You are a test agent") -> dict:
    """Build AgentRuntimeSpec-like fields dict for testing."""
```

**Helper builder pattern (`agent_kernel_test_helpers.py`):**
```python
def _make_spec(*, provider=None, max_turns=100, guards=None, hooks=None) -> AgentRuntimeSpec:
    """Build a real AgentRuntimeSpec with mock defaults."""

def _make_tool_registry(tool_names=None, result='tool result') -> tuple[ToolRegistry, list[_CatchAllTool]]:
    """Create ToolRegistry with named catch-all tools. Returns (registry, tools)."""
```

**Inline context factories in integration tests:**
```python
def _make_minimal_ctx(tmp_path: Path, llm_provider=None) -> PlaygroundContext:
    """Create a minimal PlaygroundContext (no archival, no env vars)."""
    return PlaygroundContext(
        workdir=tmp_path / "workspace",
        session_type="local",
        cache_area=tmp_path / "cache",
        llm_provider=llm_provider,
    )
```

**Location strategy:**
- Universal mocks: `tests/conftest.py`
- Domain-specific mocks: `tests/matmaster/{domain}/conftest.py`
- Complex test helpers: dedicated helper module (e.g., `agent_kernel_test_helpers.py`)
- Simple factories: inline in test file as `_make_*` functions

## Coverage

**Requirements:** Not enforced -- no coverage threshold configured
**No `pytest-cov` in dependencies** (not in `[project.optional-dependencies]`)
**No coverage configuration** in `pytest.ini` or `pyproject.toml`

## Test Types

**Unit Tests (types/, tools/, core/):**
- Scope: single class or function behavior
- All external dependencies mocked
- Example: `test_tool_registry.py` -- register/execute/override with MockTool
- Example: `test_events.py` -- Pydantic model instantiation, discriminated union validation
- Example: `test_bus.py` -- MessageBus emit/get/timeout behavior

**Integration Tests (integration/):**
- Scope: multiple layers working together through the assembly pipeline
- Mock only at LLM provider boundary; use real Exp, Kernel, ToolRegistry
- Example: `test_e2e_minimal.py` -- full Exp.build_runtime -> Kernel.run with mock LLM
- Example: `test_subagent_spawn.py` -- spawn lifecycle with patched config loader
- Example: `test_event_router.py` -- events flowing through bus to handlers

**Protocol conformance tests (types/):**
- Verify that mock classes satisfy `@runtime_checkable` Protocol contracts
- Example: `assert isinstance(tool, Tool)` -- confirms structural typing

**E2E Tests:**
- No browser/API-level E2E framework (no Playwright, Cypress, or httpx TestClient tests detected)
- `test_e2e_minimal.py` and `test_e2e_mat_master.py` are pipeline-level integration tests with mocked LLM

## Common Patterns

**Async Testing:**
All async tests use `asyncio_mode = auto` -- no need for explicit `@pytest.mark.asyncio`:
```python
class TestMessageBusBasic:
    async def test_emit_and_get(self) -> None:
        bus = MessageBus()
        event = _make_thought("hello")
        await bus.emit(event)
        got = await bus.get()
        assert got.content == "hello"
```

**Error/Exception Testing:**
```python
async def test_get_timeout_on_empty(self) -> None:
    bus = MessageBus()
    with pytest.raises(asyncio.TimeoutError):
        await bus.get(timeout=0.05)
```

**Tool execution testing pattern:**
```python
async def test_register_and_execute(self) -> None:
    registry = ToolRegistry()
    tool = MockTool(name="greet", result="hello!")
    registry.register(tool, source="builtin")

    result = await registry.execute("greet", {})
    assert isinstance(result, ToolResult)
    assert result.content == "hello!"
    assert result.status == "success"
```

**Hook behavior testing with MagicMock:**
```python
async def test_emits_skill_hit_event_for_use_skill(self) -> None:
    bus = MagicMock(emit=AsyncMock())
    hook = SkillHitHook(bus=bus, source="MatMaster")
    tc = ToolCallData(id="tc-1", name="use_skill", arguments={"skill_name": "bohrium-job"})
    await hook.post_tool_call(tc, ToolResult(content="result"))

    bus.emit.assert_called_once()
    emitted = bus.emit.call_args[0][0]
    assert isinstance(emitted, SkillHitEvent)
```

**Multi-patch context manager pattern:**
```python
with (
    patch("matmaster.config.loader.load_exp_config") as mock_load,
    patch.object(Exp, "run", new_callable=AsyncMock, return_value=mock_kr),
):
    mock_load.return_value = ExpConfig(name="explore")
    spawn_fn = Exp._make_spawn_fn(ctx, bus=None, source_prefix="MatMaster")
    result = await spawn_fn("explore", "find test files")
assert result == "found 3 files"
```

**`tmp_path` for filesystem tests:**
```python
async def test_minimal_e2e_pipeline(self, tmp_path: Path) -> None:
    pg_ctx = _make_minimal_ctx(tmp_path, llm_provider=mock_llm)
    # ... test with real filesystem paths
```

## Test Gaps and Concerns

**No coverage enforcement:**
- No `pytest-cov` dependency, no coverage thresholds
- Cannot verify which code paths are untested

**Service layer untested:**
- `src/services/` (FastAPI service layer) has no dedicated test directory
- `tests/matmaster/` covers the `matmaster/` package only
- API endpoints, middleware, SSE handlers appear untested

**No HTTP/API-level tests:**
- No `httpx.AsyncClient` or `TestClient` usage for FastAPI endpoint testing
- SSE event delivery, WebSocket handlers, and REST API contracts not tested

**Adaptor tests are thin:**
- `tests/matmaster/adaptors/calculation/` has only 3 test files for the Bohrium HPC integration
- Remote execution paths (SSH, Bohrium job submission) likely under-tested

**No load/performance tests:**
- Streaming performance, bus throughput, concurrent agent execution not benchmarked

---

*Testing analysis: 2026-04-02*
