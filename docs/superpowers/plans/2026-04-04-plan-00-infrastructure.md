# Builtin Tools Infrastructure — Plan 00

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the BuiltinTool ABC base class, shell-safety utilities, and package skeleton that all subsequent tool plans depend on.

**Architecture:** `BuiltinTool` ABC satisfies the existing `Tool` Protocol. Sync `_execute()` runs via `asyncio.to_thread`. Shared `_path_safety.py` provides `resolve_safe_path()` and `shell_escape()`. Package `__init__.py` will be populated incrementally by later plans.

**Tech Stack:** Python 3.10+, asyncio, shlex, posixpath, pydantic (ToolResult), abc

**Spec:** `docs/superpowers/specs/2026-04-04-builtin-tools-design.md` — Section 1

**CC Reference:** `Tool.ts` (Tool type), `tools.ts` (tool assembly)

---

## Task 1: Create `_path_safety.py`

**Files:**
- Create: `matmaster/tools/builtin/_path_safety.py`
- Test: `tests/matmaster/tools/builtin/test_path_safety.py`

- [ ] **Step 1: Write failing tests for `resolve_safe_path`**

```python
"""tests/matmaster/tools/builtin/test_path_safety.py"""
import pytest
from matmaster.tools.builtin._path_safety import resolve_safe_path


class TestResolveSafePath:
    def test_empty_returns_workdir(self):
        assert resolve_safe_path("", "/workspace") == "/workspace"

    def test_dot_returns_workdir(self):
        assert resolve_safe_path(".", "/workspace") == "/workspace"

    def test_relative_path_joined(self):
        assert resolve_safe_path("src/foo", "/workspace") == "/workspace/src/foo"

    def test_absolute_within_workdir(self):
        assert resolve_safe_path("/workspace/src", "/workspace") == "/workspace/src"

    def test_absolute_outside_workdir_fallback(self):
        assert resolve_safe_path("/etc/passwd", "/workspace") == "/workspace"

    def test_traversal_blocked(self):
        assert resolve_safe_path("../../etc/passwd", "/workspace") == "/workspace"

    def test_normpath_removes_dotdot(self):
        assert resolve_safe_path("src/../src/foo", "/workspace") == "/workspace/src/foo"

    def test_workdir_trailing_slash(self):
        assert resolve_safe_path("src", "/workspace/") == "/workspace/src"

    def test_workdir_trailing_slash_absolute(self):
        assert resolve_safe_path("/workspace/src", "/workspace/") == "/workspace/src"

    def test_prefix_collision_not_subdir(self):
        # /workspacex is NOT a subdirectory of /workspace
        assert resolve_safe_path("/workspacex/foo", "/workspace") == "/workspace"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run pytest tests/matmaster/tools/builtin/test_path_safety.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write failing tests for `shell_escape`**

Append to the same test file:

```python
from matmaster.tools.builtin._path_safety import shell_escape


class TestShellEscape:
    def test_simple_string_unchanged(self):
        # shlex.quote returns safe strings unchanged (no wrapping quotes)
        assert shell_escape("hello") == "hello"

    def test_string_with_spaces(self):
        assert shell_escape("hello world") == "'hello world'"

    def test_injection_attempt_dollar(self):
        result = shell_escape("$(rm -rf /)")
        assert "$(" not in result or result.startswith("'")

    def test_injection_attempt_backtick(self):
        result = shell_escape("`rm -rf /`")
        assert "`" not in result or result.startswith("'")

    def test_injection_attempt_semicolon(self):
        result = shell_escape("foo; rm -rf /")
        assert result.startswith("'")

    def test_empty_string(self):
        assert shell_escape("") == "''"
```

- [ ] **Step 4: Implement `_path_safety.py`**

```python
"""matmaster/tools/builtin/_path_safety.py

Path safety and shell argument sanitization for builtin tools.

resolve_safe_path: ensures user paths stay within workdir boundary.
shell_escape: wraps values for safe shell interpolation (shlex.quote).
"""

from __future__ import annotations

import posixpath
import shlex


def resolve_safe_path(user_path: str, workdir: str) -> str:
    """Resolve user-provided path to a safe absolute path within workdir.

    - Empty or '.' → workdir
    - Absolute path within workdir → normalized
    - Absolute path outside workdir → fallback to workdir
    - Relative path → joined with workdir, checked for containment

    The outside-workdir fallback is defense-in-depth; StructuralValidation
    (Layer A) catches boundary violations before _execute() runs.
    """
    # Normalize workdir first to handle trailing slashes and dot segments
    workdir = posixpath.normpath(workdir)

    if not user_path or user_path == ".":
        return workdir

    if user_path.startswith("/"):
        normalized = posixpath.normpath(user_path)
        if normalized == workdir or normalized.startswith(workdir + "/"):
            return normalized
        return workdir

    joined = posixpath.join(workdir, user_path)
    normalized = posixpath.normpath(joined)
    if normalized == workdir or normalized.startswith(workdir + "/"):
        return normalized
    return workdir


def shell_escape(value: str) -> str:
    """Escape a string for safe interpolation into shell commands.

    Uses shlex.quote() to prevent shell injection ($(...), backticks,
    semicolons, etc.) when building commands for session.exec_bash().
    """
    return shlex.quote(value)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/claude-code-tool && uv run pytest tests/matmaster/tools/builtin/test_path_safety.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/tools/builtin/_path_safety.py tests/matmaster/tools/builtin/test_path_safety.py
git commit -m "feat(tools): add _path_safety module with resolve_safe_path and shell_escape"
```

---

## Task 2: Create `base.py` — BuiltinTool ABC

**Files:**
- Create: `matmaster/tools/builtin/base.py`
- Test: `tests/matmaster/tools/builtin/test_base.py`

**CC Reference:** `Tool.ts` lines 362-695 — Tool type definition. Mapped to Python:
- `call()` → `execute()` / `execute_with_context()`
- `description()` → `describe(ctx)`
- `prompt()` → `prompt(ctx)`
- `inputSchema` → `json_schema` (ClassVar dict)
- `validateInput()` → `validate_input()` (async)
- `isConcurrencySafe`, `isReadOnly`, etc. → ClassVar metadata

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_base.py"""
import asyncio
import pytest
from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.topology import ToolPlane


class ConcreteTool(BuiltinTool):
    name = "TestTool"
    description = "A test tool"
    json_schema = {"type": "object", "properties": {}, "required": []}

    def _execute(self, arguments):
        return "ok"


class ErrorTool(BuiltinTool):
    name = "ErrorTool"
    description = "Raises"
    json_schema = {"type": "object", "properties": {}}

    def _execute(self, arguments):
        raise RuntimeError("boom")


class TestBuiltinToolProtocol:
    def test_name(self):
        tool = ConcreteTool()
        assert tool.name == "TestTool"

    def test_description(self):
        tool = ConcreteTool()
        assert tool.description == "A test tool"

    def test_describe_returns_description(self):
        tool = ConcreteTool()
        assert tool.describe() == "A test tool"

    def test_prompt_returns_none(self):
        tool = ConcreteTool()
        assert tool.prompt() is None

    def test_default_plane(self):
        tool = ConcreteTool()
        assert tool.plane == ToolPlane.CONTROL_PLANE

    def test_default_effect_level(self):
        tool = ConcreteTool()
        assert tool.effect_level == "local_mutation"

    def test_default_capabilities(self):
        tool = ConcreteTool()
        assert tool.capabilities == frozenset()

    def test_default_exposed_to_model(self):
        tool = ConcreteTool()
        assert tool.exposed_to_model is True


class TestBuiltinToolExecution:
    def test_execute_returns_result(self):
        tool = ConcreteTool()
        result = asyncio.run(tool.execute({}))
        assert result == "ok"

    def test_execute_catches_exception(self):
        tool = ErrorTool()
        result = asyncio.run(tool.execute({}))
        assert isinstance(result, str)
        assert "Error:" in result

    def test_execute_with_context_default(self):
        tool = ConcreteTool()
        result = asyncio.run(tool.execute_with_context({}, None))
        assert result == "ok"


class TestRequireSession:
    def test_no_session_raises(self):
        tool = ConcreteTool()
        with pytest.raises(RuntimeError, match="requires a session"):
            tool._require_session()

    def test_with_session(self):
        tool = ConcreteTool(session="fake")
        assert tool._require_session() == "fake"


class TestToolResultReturn:
    def test_execute_propagates_tool_result(self):
        class ToolResultTool(BuiltinTool):
            name = "ToolResultTool"
            description = "Returns ToolResult"
            json_schema = {"type": "object", "properties": {}}

            def _execute(self, arguments):
                return ToolResult(content="structured output")

        tool = ToolResultTool()
        result = asyncio.run(tool.execute({}))
        assert isinstance(result, ToolResult)
        assert result.content == "structured output"


class TestValidateInput:
    def test_default_returns_none(self):
        tool = ConcreteTool()
        result = asyncio.run(tool.validate_input({}, None))
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/tools/builtin/test_base.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `base.py`**

```python
"""matmaster/tools/builtin/base.py

BuiltinTool ABC — base class for all matmaster builtin tools.

Satisfies the Tool Protocol (name/description/json_schema/execute).
Construction injection: session/workdir passed at Exp assemble time.
Kernel sees only Tool Protocol interface.

execute() is async, delegates to sync _execute() via asyncio.to_thread.
Subclasses implement sync _execute() only.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from matmaster.tools.tool_result import ToolResult
from matmaster.types.cancellation import CancellationToken
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane


class BuiltinTool(ABC):
    """BuiltinTool base — satisfies matmaster Tool Protocol.

    Subclasses:
    - Define name, description, json_schema as ClassVar
    - Implement _execute(arguments) -> str | ToolResult (sync)
    """

    name: ClassVar[str]
    description: ClassVar[str] = ""
    json_schema: ClassVar[dict[str, Any]]
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = ()
    capabilities: ClassVar[frozenset[str]] = frozenset()
    effect_level: ClassVar[str] = "local_mutation"
    fast_path_eligible: ClassVar[bool] = False
    max_result_chars: ClassVar[int] = 0
    plane: ClassVar[ToolPlane] = ToolPlane.CONTROL_PLANE
    state_mode: ClassVar[str] = "stateless"
    stop_mode: ClassVar[str] = "cancellable"
    exposed_to_model: ClassVar[bool] = True

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: str | Path | None = None,
    ) -> None:
        self._session = session
        self._workdir = Path(workdir) if workdir is not None else None
        self.logger = logging.getLogger(self.__class__.__name__)

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Tool Protocol entry point. Delegates to _execute via to_thread."""
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        """Context-aware execution. Subclasses override for cancel_token/runner_state."""
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    def describe(self, ctx: ToolDescriptionContext | None = None) -> str:
        """Dynamic description. Default returns self.description."""
        return self.description

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        """LLM prompt injection. Default returns None."""
        return None

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        """Semantic input validation. Return None to allow, ToolDecision(deny) to reject."""
        return None

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Subclass implementation. Sync. Raise on error, return str or ToolResult."""
        ...

    def _require_session(self) -> Any:
        """Guard: raise if session not injected."""
        if self._session is None:
            raise RuntimeError(
                f"{self.name} requires a session but none was injected"
            )
        return self._session

    def _cancel_token_for_exec(self) -> CancellationToken | None:
        """Cancel signal for session.exec_bash (injected by ToolCatalog.inject_cancel_token)."""
        ct = getattr(self, "_cancel_token", None)
        if ct is not None:
            return ct
        if self._session is not None:
            return getattr(self._session, "_cancel_token", None)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/tools/builtin/test_base.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/base.py tests/matmaster/tools/builtin/test_base.py
git commit -m "feat(tools): add BuiltinTool ABC base class"
```

---

## Task 3: Create package `__init__.py` skeleton

**Files:**
- Create: `matmaster/tools/builtin/__init__.py`
- Create: `tests/matmaster/tools/builtin/__init__.py`

**Note:** This skeleton is for the worktree fresh-start scenario. The current codebase `__init__.py` already exports all existing tools (BashTool, ReadTool, etc.). When executing in the worktree, start with this minimal skeleton; each subsequent plan (01-04) appends its tool class to `__all__` and adds the corresponding import.

- [ ] **Step 1: Create skeleton `__init__.py`**

```python
"""matmaster/tools/builtin/__init__.py

Builtin tools — matmaster native tool implementations.
All tools inherit from BuiltinTool ABC and satisfy the Tool Protocol.

Tools are added incrementally by plan-01 through plan-04.
"""

from matmaster.tools.builtin.base import BuiltinTool

__all__ = [
    "BuiltinTool",
]
```

- [ ] **Step 2: Create test package `__init__.py`**

```python
"""tests/matmaster/tools/builtin/__init__.py"""
```

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from matmaster.tools.builtin import BuiltinTool; print(BuiltinTool.__name__)"`
Expected: `BuiltinTool`

- [ ] **Step 4: Commit**

```bash
git add matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/__init__.py
git commit -m "feat(tools): add builtin package skeleton"
```

---

## Dependency Note

Plans 01-04 all depend on this plan completing first. They will each append their tool class to `__init__.py` as they are implemented.

**Plan-02 integration requirements** (must be addressed when implementing Bash/Glob/Grep):
- **Replace inline `_resolve_safe_path`**: GlobTool and GrepTool each have an identical inline `_resolve_safe_path` method. Plan-02 must replace these with `from matmaster.tools.builtin._path_safety import resolve_safe_path`.
- **Use `shell_escape` for command construction**: Current GlobTool/GrepTool interpolate user-supplied `pattern`, `glob`, and `path` directly into f-strings (e.g., `f'find "{safe_path}" -type f -name "{pattern}"'`). This is a shell injection vector. Plan-02 must use `shell_escape()` from `_path_safety` for all user parameters before shell interpolation.
