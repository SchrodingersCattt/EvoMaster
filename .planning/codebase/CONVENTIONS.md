# Coding Conventions

**Analysis Date:** 2026-04-02

## Naming Patterns

**Files:**
- Snake_case for all Python modules: `tool_registry.py`, `context_compactor.py`, `guard_pipeline.py`
- Test files prefixed with `test_`: `test_agent_kernel.py`, `test_bus.py`, `test_tool_registry.py`
- Test helper files use descriptive names without `test_` prefix: `agent_kernel_test_helpers.py` (excluded from `name-tests-test` hook)
- Config/TOML files use snake_case: `_base.toml`, `mcp_config.test.json`

**Classes:**
- PascalCase for all classes: `AgentKernel`, `ToolRegistry`, `PlaygroundContext`, `MessageBus`
- Protocol classes named after the concept directly (no suffix): `Tool`, `Hook`, `Guard`, `LLMProvider`, `Session`
- Mock classes prefixed with `Mock`: `MockAsyncLLMProvider`, `MockAsyncTool`, `MockLLMProvider`
- Private/internal classes prefixed with underscore: `_CatchAllTool`, `_KernelStopRequested`, `_TerminalItem`
- Event classes suffixed with `Event`: `ThoughtEvent`, `ToolCallEvent`, `RunResultEvent`, `SkillHitEvent`
- Config classes suffixed with `Config`: `ExpConfig`, `CompactionConfig`, `SessionConfig`, `ExpToolsConfig`
- Result/data classes suffixed with `Result`/`Data`: `ToolResult`, `KernelResult`, `GuardResult`, `ToolCallData`

**Functions/Methods:**
- Snake_case for all functions: `parse_tool_arguments()`, `normalize_tool_result()`
- Private methods prefixed with underscore: `_run_items()`, `_execute()`, `_make_spawn_fn()`
- Factory functions prefixed with `make_` or `build_`: `make_tool_call()`, `build_mock_spec()`
- Test helper factories prefixed with `_make_`: `_make_spec()`, `_make_tool_registry()`, `_make_thought()`, `_make_minimal_ctx()`
- Async hook dispatch helpers prefixed with `run_`: `run_pre_tool_call()`, `run_post_tool_call()`, `run_should_continue()`

**Variables/Constants:**
- Snake_case for all variables: `tool_registry`, `stop_event`, `stream_chunks`
- Module-level constants in UPPER_SNAKE_CASE with underscore prefix for private: `_STOP_CHECK_EVERY_N_STREAM_CHUNKS`, `_COMPILED_PATTERNS`, `_BLOCKED_FIRST_TOKENS`
- Logger always via `logging.getLogger(__name__)` assigned to `logger` at module level
- Class-level logger via `self.logger = logging.getLogger(self.__class__.__name__)` in `__init__`

**Types:**
- PEP 604 union syntax: `str | None`, `str | ToolResult | None` (never `Optional[str]`)
- `Literal` types for discriminated union fields: `type: Literal["thought"] = "thought"`
- `ClassVar` for class-level tool attributes: `name: ClassVar[str]`

## Code Style

**Formatting:**
- Black 25.9.0 with `--skip-string-normalization` (preserves single quotes in source)
- Line length: 88 characters (Black default)
- Configured via `.pre-commit-config.yaml`, not `pyproject.toml`

**Import Sorting:**
- isort 6.0.1 with `--profile black` for Black-compatible formatting
- Automatically enforced via pre-commit

**Linting:**
- flake8 7.3.0 with flake8-bugbear plugin
- Ignored rules: `B008` (function call in default arg), `E501` (line length deferred to Black), `B036`, `E203`
- autoflake: removes all unused imports and variables in-place
- pyupgrade 3.20.0: modernizes Python syntax automatically

**Pre-commit hooks (`.pre-commit-config.yaml`):**
- Custom file line count check: **single file must not exceed 1000 lines** (`.pre-commit/check_file_lines.py`)
- Standard hooks: `check-ast`, `check-json`, `check-toml`, `check-yaml`, `check-merge-conflict`, `trailing-whitespace`, `end-of-file-fixer`
- Security: `detect-aws-credentials`, `detect-private-key`
- `no-commit-to-branch`: prevents direct commits to protected branches
- `name-tests-test --pytest-test-first`: enforces `test_*.py` naming for test files

**Type Checking:**
- No mypy or pyright configuration detected -- type annotations are used extensively but not enforced via static analysis tool in CI
- `from __future__ import annotations` used consistently as first import in every module for PEP 604 syntax support

## Import Organization

**Order (enforced by isort with Black profile):**
1. `from __future__ import annotations` (always first)
2. Standard library: `asyncio`, `logging`, `json`, `threading`, `re`, `enum`, `time`, `uuid`
3. Third-party: `pydantic`, `pytest`, `openai`
4. Local/project: `from matmaster.types.messages import ...`, `from matmaster.core.bus import ...`

**Key patterns:**
- `TYPE_CHECKING` guard for circular import prevention:
  ```python
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from matmaster.types.runtime import AgentRuntimeSpec, KernelRunResult
  ```
- Explicit named imports, never wildcard: `from matmaster.types.messages import LLMResponse, StreamChunk, ToolCallData`
- Relative imports within test packages: `from .conftest import MockLLMProvider`, `from .agent_kernel_test_helpers import _make_spec`
- No path aliases or import rewrites in Python

## Error Handling

**Custom exceptions:**
- `LLMError` in `matmaster/types/errors.py`: carries `retryable: bool`, `error_category: str | None`, and `attempts: list[dict]`
- No other custom exceptions detected -- the codebase uses structured result types instead of raising

**Error result pattern (preferred over exceptions):**
- `ToolResult(status="error", content="Error executing tool...")` for tool failures
- `ToolResult.from_error(tool_name, exception)` classmethod for structured error creation
- `normalize_tool_result()` in `matmaster/tools/tool_result.py` auto-detects error strings starting with `"Error:"` and sets `status="error"`
- `GuardResult(allowed=False, reason="...")` for guard denials (not exceptions)

**Layer-specific rules:**
- **Types layer** (`matmaster/types/`): pure data, no exception handling
- **Tool base class** (`matmaster/tools/builtin/base.py`): `BuiltinTool.execute()` catches all exceptions from `_execute()`, converts to `f'Error: {e}'`
- **Kernel** (`matmaster/core/agent.py`): catches `LLMError`, applies retry based on `retryable` flag
- **Cleanup callbacks** (`matmaster/core/exp.py`): each callback runs independently in try/except -- one failure does not block subsequent cleanup

**Logging on error:**
- `self.logger.error('Tool %s failed: %s', self.name, e, exc_info=True)` with traceback for tool failures
- `logger.warning(...)` for recoverable issues (tool override, parse failures)

## Type Annotation Patterns

**Protocol-based structural typing (`@runtime_checkable`):**
All major interfaces in `matmaster/types/`:
- `Tool` in `matmaster/tools/tool_registry.py`: `name`, `description`, `json_schema`, `execute()`
- `Hook` in `matmaster/core/hooks.py`: 7 async hook methods
- `Guard` in `matmaster/types/guards.py`: `evaluate()` method
- `LLMProvider` in `matmaster/types/llm_provider.py`: `__aenter__`, `__aexit__`, `chat()`, `chat_stream()`
- `Session` in `matmaster/types/session.py`: execution session contract

Pattern:
```python
@runtime_checkable
class Tool(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def json_schema(self) -> dict[str, Any]: ...
    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult | None: ...
```

**Frozen Pydantic models for inter-layer contracts:**
- `model_config = ConfigDict(frozen=True)` on all boundary types
- `PlaygroundContext` in `matmaster/types/context.py`
- `AgentRuntimeSpec` in `matmaster/types/runtime.py`
- `CompactionConfig` in `matmaster/types/runtime.py`
- `SessionConfig` / `LocalSessionConfig` / `SSHSessionConfig` in `matmaster/types/session.py`
- `WorkspaceArchivalConfig` in `matmaster/types/context.py`
- Use `arbitrary_types_allowed=True` when holding Protocol instances (e.g., `LLMProvider`)

**Pydantic discriminated unions for events:**
- `BusEvent = Annotated[Union[AgentEvent, SystemEvent], Field(discriminator="type")]` in `matmaster/types/events.py`
- Each event: `type: Literal["thought"] = "thought"` as discriminator field
- 18 event types total (8 AgentEvent + 10 SystemEvent)

**Dataclasses for lightweight internal data:**
- `@dataclass` for kernel-internal types: `GuardContext`, `GuardResult`, `RecentCall` in `matmaster/types/guards.py`
- `_TerminalItem` in `matmaster/core/agent.py`

**General annotation conventions:**
- Always `from __future__ import annotations` for forward references
- `collections.abc` for abstract types: `AsyncIterator`, `Callable`
- Lowercase generic syntax: `dict[str, Any]`, `list[str]`, `tuple[str, ...]`
- Return type annotations on all public and most private methods
- `Any` used sparingly, mainly for bridge/adapter boundaries

## Documentation Patterns

**Module docstrings (required on every module):**
```python
"""AgentKernel -- pure async execution loop for the agent kernel.

Consumes an AgentRuntimeSpec and executes the LLM -> guard -> hook -> tool
-> message accumulate -> loop cycle. Three-layer interface:

  _run_items()   -- private AsyncGenerator yielding _KernelItem
  run_stream()   -- public AsyncGenerator yielding BusEvent
  run()          -- public coroutine returning KernelRunResult (backward compat)
"""
```

**Class docstrings:**
- Every class has a docstring
- References architectural layer, Protocol, or contract role
- Example: `"""Flat-namespace tool registry with source tracking."""`

**Method/function docstrings:**
- Public methods always have docstrings
- Private methods have docstrings when non-trivial
- No strict docstring format (not Google/NumPy style), generally 1-3 line descriptions

**Comment style:**
- Chinese comments in implementation for team communication: `# 流式输出中每隔 N 个 chunk 检查一次 stop_event（避免每 chunk 打 Redis EXISTS）`
- Section headers with Unicode box-drawing characters: `# ── AgentEvent: kernel-layer events ─────────────────────`
- Inline comments explain *why*, not *what*

**Test docstrings:**
- Test classes: brief purpose description
- Test methods: one-line expected behavior:
  ```python
  async def test_natural_finish(self) -> None:
      """LLM returns no tool_calls -> FinishEvent(reason='natural')."""
  ```

## Git Conventions

**Commit message format (Conventional Commits):**
- Pattern: `type(scope): description`
- Types: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`, `merge`
- Scope: phase number or component name: `(32-01)`, `(exp)`, `(28)`, `(state)`, `(phase-32)`
- Description: lowercase, imperative or descriptive noun phrase
- Examples:
  - `feat(32-03): implement _run_items() generator + run_stream() + run() delegation`
  - `test(32-01): add failing tests for Tool Runtime v2 type system`
  - `fix(32): restore cancel semantics, fix stale info= usage, enforce type contracts`
  - `refactor(28): 收拢 BohriumSetupService 到 src/services/agent_run_bohrium`

**Development workflow:**
- Test-first: failing tests committed before implementation (`test(32-01):` then `feat(32-01):`)
- Phase-based numbered development
- State/context documentation commits track planning sessions

**Branch naming:**
- Feature branches: `refactor/async-agent`
- Main branch: `test`
- Protected via `no-commit-to-branch` pre-commit hook

## Logging

**Framework:** Python standard `logging` module

**Initialization patterns:**
- Module level: `logger = logging.getLogger(__name__)`
- Class level: `self.logger = logging.getLogger(self.__class__.__name__)`

**Level usage:**
- `.info()`: lifecycle events (tool registered, config loaded)
- `.warning()`: recoverable issues (tool name override, parse failure)
- `.error()`: tool execution failures with `exc_info=True`
- `.debug()`: not heavily used in observed code

## Function Design

**Size:**
- Enforced max 1000 lines per file via pre-commit hook
- Functions favor single responsibility with descriptive names

**Parameters:**
- Keyword-only arguments via `*` separator for constructors and factories:
  ```python
  def __init__(self, *, session: Any | None = None, workdir: Path | None = None) -> None:
  ```
- Explicit types on all parameters

**Return Values:**
- Tool Protocol returns `str | ToolResult | None`, unified by `normalize_tool_result()`
- Kernel returns typed `KernelResult` dataclass with `status`, `reason`, `final_content`
- Guard returns `GuardResult` dataclass with `allowed`, `reason`

## Module Design

**Exports:**
- No barrel files -- `__init__.py` files are empty or minimal
- Direct imports from specific modules: `from matmaster.tools.tool_registry import Tool, ToolRegistry`
- No `__all__` definitions detected

**Layer isolation (strict):**
- `matmaster/types/`: pure data models + Protocols, zero business logic
- `matmaster/core/`: business logic (kernel, exp, playground, bus), imports from types
- `matmaster/tools/`: tool implementations + registry, imports from types
- `matmaster/providers/`: LLM client wrappers
- `matmaster/config/`: configuration loading (TOML, YAML)
- `matmaster/hooks/`: concrete Hook implementations
- `matmaster/sessions/`: Session implementations (local, SSH)
- Cross-layer communication via frozen Pydantic models and Protocol interfaces only

---

*Convention analysis: 2026-04-02*
