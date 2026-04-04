# Tool Error Reporting Fix Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make BashTool correctly report error status on non-zero exit codes, and wrap all error tool results with `<error>` tags so the LLM reliably detects failures.

**Architecture:** Two changes — BashTool returns `ToolResult(status="error")` for non-zero exit codes; `FullToolRunner._execute_one()` wraps error content in `<error>` tags after normalize, before truncate.

**Tech Stack:** Python, Pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-04-04-tool-error-reporting-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `matmaster/tools/builtin/bash_tool.py:82-108` | Return `ToolResult(status="error")` for exit_code != 0 |
| Modify | `matmaster/core/tool_runner.py:343-345` | Insert error content wrapping after normalize |
| Modify | `tests/matmaster/tools/builtin/test_bash_tool.py` | Add tests for error status on non-zero exit code |
| Modify | `tests/matmaster/tools/test_tool_result.py` | Add test for error wrapping behavior (optional, wrapping is in tool_runner not tool_result) |
| Create | `tests/matmaster/core/test_tool_runner_error_wrap.py` | Test error content wrapping in _execute_one pipeline |

---

## Chunk 1: BashTool Error Status + Tests

### Task 1: BashTool returns ToolResult(status="error") for non-zero exit codes

**Files:**
- Modify: `matmaster/tools/builtin/bash_tool.py:82-108`
- Modify: `tests/matmaster/tools/builtin/test_bash_tool.py`

- [ ] **Step 1: Write the failing tests and update existing test**

Add to `tests/matmaster/tools/builtin/test_bash_tool.py`:

```python
from matmaster.tools.tool_result import ToolResult


class TestBashErrorStatus:
    def test_nonzero_exit_returns_error_status(self):
        session = make_session(output="Traceback...\nModuleNotFoundError: No module named 'pymatgen'", exit_code=1)
        tool = BashTool(session=session, workdir="/workspace")
        result = asyncio.run(tool.execute({"command": "python -c 'import pymatgen'"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "exit code 1" in result.content.lower()

    def test_nonzero_exit_preserves_full_content(self):
        session = make_session(output="some output", exit_code=2, working_dir="/tmp")
        tool = BashTool(session=session, workdir="/workspace")
        result = asyncio.run(tool.execute({"command": "exit 2"}))
        assert isinstance(result, ToolResult)
        assert "some output" in result.content
        assert "/tmp" in result.content
        assert "exit code 2" in result.content

    def test_zero_exit_returns_success_string(self):
        session = make_session(output="hello", exit_code=0)
        tool = BashTool(session=session, workdir="/workspace")
        result = asyncio.run(tool.execute({"command": "echo hello"}))
        # exit_code==0 returns str, normalize_tool_result handles it
        assert isinstance(result, str)
        assert "hello" in result
```

Also update existing `test_exit_code_in_output` in `TestBashExecution` (it will break because `result` changes from `str` to `ToolResult`):

```python
    def test_exit_code_in_output(self):
        session = make_session(exit_code=1)
        tool = BashTool(session=session)
        result = asyncio.run(tool.execute({"command": "false"}))
        # After fix: non-zero exit returns ToolResult, not str
        assert isinstance(result, ToolResult)
        assert "exit code 1" in result.content.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py::TestBashErrorStatus -v`

Expected: `test_nonzero_exit_returns_error_status` FAILS (result is `str` not `ToolResult`), `test_zero_exit_returns_success_string` PASSES.

- [ ] **Step 3: Implement the fix**

In `matmaster/tools/builtin/bash_tool.py`:

1. Add import at top of file:

```python
from matmaster.tools.tool_result import ToolResult
```

2. Update `_execute()` return type annotation (line 82) from `-> str` to `-> str | ToolResult`.

3. Replace the return statement at the end of `_execute()` (line 108):

```python
    # Current:
    #     return obs

    # New:
    if exit_code != 0:
        return ToolResult(status="error", content=obs)
    return obs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py -v`

Expected: All tests PASS including the new `TestBashErrorStatus` tests and the updated `test_exit_code_in_output`.

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/bash_tool.py tests/matmaster/tools/builtin/test_bash_tool.py
git commit -m "fix(bash_tool): return error status for non-zero exit codes"
```

---

## Chunk 2: Framework Error Content Wrapping + Tests

### Task 2: FullToolRunner wraps error content in `<error>` tags

**Files:**
- Modify: `matmaster/core/tool_runner.py:343-345`
- Create: `tests/matmaster/core/test_tool_runner_error_wrap.py`

- [ ] **Step 1: Write the failing test**

Create `tests/matmaster/core/test_tool_runner_error_wrap.py`:

```python
"""Tests for error content wrapping in FullToolRunner._execute_one pipeline."""

from matmaster.tools.tool_result import ToolResult, normalize_tool_result


class TestErrorContentWrapping:
    """Test the error wrapping logic that will be added to tool_runner.

    These tests validate the wrapping behavior in isolation,
    matching the logic inserted in FullToolRunner._execute_one().
    """

    def _apply_error_wrap(self, tr: ToolResult) -> ToolResult:
        """Replicate the error_wrap step from _execute_one."""
        if tr.status == "error" and not tr.content.lstrip().startswith("<error>\n"):
            tr = tr.model_copy(update={
                "content": f"<error>\n{tr.content}\n</error>"
            })
        return tr

    def test_error_result_gets_wrapped(self):
        tr = ToolResult(status="error", content="something failed")
        wrapped = self._apply_error_wrap(tr)
        assert wrapped.content == "<error>\nsomething failed\n</error>"
        assert wrapped.status == "error"

    def test_success_result_not_wrapped(self):
        tr = ToolResult(status="success", content="all good")
        result = self._apply_error_wrap(tr)
        assert result.content == "all good"

    def test_already_wrapped_not_double_wrapped(self):
        tr = ToolResult(status="error", content="<error>\nalready tagged\n</error>")
        result = self._apply_error_wrap(tr)
        assert result.content.count("<error>") == 1

    def test_empty_content_error_wrapped(self):
        tr = ToolResult(status="error", content="")
        wrapped = self._apply_error_wrap(tr)
        assert wrapped.content == "<error>\n\n</error>"

    def test_normalize_then_wrap_pipeline(self):
        """Simulate the full normalize -> error_wrap pipeline for a bash error."""
        raw = ToolResult(status="error", content="Traceback...\n[Command finished with exit code 1]")
        normalized = normalize_tool_result(raw)
        wrapped = self._apply_error_wrap(normalized)
        assert wrapped.status == "error"
        assert wrapped.content.startswith("<error>\n")
        assert wrapped.content.endswith("\n</error>")
        assert "Traceback" in wrapped.content

    def test_normalize_error_prefix_then_wrap(self):
        """base.py exception path: 'Error: ...' string -> normalize -> wrap."""
        normalized = normalize_tool_result("Error: something broke")
        wrapped = self._apply_error_wrap(normalized)
        assert wrapped.status == "error"
        assert "<error>" in wrapped.content
```

- [ ] **Step 2: Run tests to verify they pass (logic test only)**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_tool_runner_error_wrap.py -v`

Expected: All PASS (these test the wrapping logic in isolation; they validate the behavior we're about to insert).

- [ ] **Step 3: Insert error_wrap into tool_runner pipeline**

In `matmaster/core/tool_runner.py`, in `_execute_one()`, after line 343 (`tr = normalize_tool_result(tr)`) and before line 345 (`max_chars = ...`), insert:

```python
        # Error-wrap: tag error content for LLM visibility
        if tr.status == "error" and not tr.content.lstrip().startswith("<error>\n"):
            tr = tr.model_copy(update={
                "content": f"<error>\n{tr.content}\n</error>"
            })
```

- [ ] **Step 4: Run all related tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py tests/matmaster/core/test_tool_runner_error_wrap.py tests/matmaster/tools/test_tool_result.py -v`

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/tool_runner.py tests/matmaster/core/test_tool_runner_error_wrap.py
git commit -m "fix(tool_runner): wrap error tool results with <error> tags for LLM visibility"
```
