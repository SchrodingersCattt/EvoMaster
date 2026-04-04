# File Tools (Read + Edit + Write) — Plan 01

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Read, Edit, and Write tools that operate through the session abstraction layer.

**Architecture:** All three tools inherit `BuiltinTool`, use `session.read_file()`/`session.write_file()`/`session.is_file()`/`session.path_exists()` for I/O. Read marks files in `runner_state["read_files"]`; Edit and Write enforce read-before-modify via `validate_input()`.

**Tech Stack:** Python 3.10+, asyncio, re, posixpath, PurePosixPath

**Spec:** `docs/superpowers/specs/2026-04-04-builtin-tools-design.md` — Section 2

**Depends on:** Plan 00 (infrastructure)

---

## CC Source Reference

### Read (`FileReadTool`)
- **Name:** `Read` (`tools/FileReadTool/prompt.ts:5`)
- **Description:** `"Read a file from the local filesystem."` (`prompt.ts:12`)
- **Prompt:** `renderPromptTemplate()` (`prompt.ts:27-48`) — explains usage: absolute path, 2000 line default, offset/limit, cat -n format
- **Schema** (`FileReadTool.ts`): `file_path: string`, `offset?: integer`, `limit?: integer`, `pages?: string`
- **MatMaster adaptation:** Drop `pages` (no PDF support). Offset is 0-indexed.

### Edit (`FileEditTool`)
- **Name:** `Edit` (`tools/FileEditTool/constants.ts:2`)
- **Description:** `getEditToolDescription()` (`prompt.ts:8-27`) — str_replace, read-before-modify, replace_all, indentation preservation
- **Schema** (`types.ts:6-19`): `file_path: string`, `old_string: string`, `new_string: string`, `replace_all?: boolean (default false)`
- **MatMaster adaptation:** Parameter names match CC exactly. No strip fallback per GPT review.

### Write (`FileWriteTool`)
- **Name:** `Write` (`tools/FileWriteTool/prompt.ts:3`)
- **Description:** `getWriteToolDescription()` (`prompt.ts:10-17`) — overwrite, read-before-modify, prefer Edit for modifications
- **Schema** (`FileWriteTool.ts:56-65`): `file_path: string`, `content: string`
- **MatMaster adaptation:** No change.

---

## Task 1: ReadTool

**Files:**
- Create: `matmaster/tools/builtin/read_tool.py`
- Test: `tests/matmaster/tools/builtin/test_read_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_read_tool.py"""
import asyncio
import pytest
from unittest.mock import MagicMock
from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ToolExecutionContext
from matmaster.types.tool_runner_state import ToolRunnerState


def make_session(content="line1\nline2\nline3", is_file=True):
    s = MagicMock()
    s.is_file.return_value = is_file
    s.read_file.return_value = content
    return s


class TestReadToolMetadata:
    def test_name(self):
        assert ReadTool.name == "Read"

    def test_effect_level(self):
        assert ReadTool.effect_level == "none"

    def test_fast_path(self):
        assert ReadTool.fast_path_eligible is True


class TestReadToolExecution:
    def test_file_not_found(self):
        tool = ReadTool(session=make_session(is_file=False))
        result = asyncio.run(tool.execute({"file_path": "/workspace/nope"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    def test_full_read_small_file(self):
        content = "\n".join(f"line {i}" for i in range(10))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({"file_path": "/workspace/f.py"}))
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert "1\t" in result.content  # cat -n format

    def test_full_read_marks_read(self):
        content = "hello"
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({"file_path": "/workspace/f.py"}))
        assert isinstance(result, ToolResult)
        assert result.meta.get("mark_read") is True

    def test_overlimit_returns_error(self):
        content = "\n".join(f"line {i}" for i in range(2500))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({"file_path": "/workspace/big.py"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "offset" in result.content.lower()

    def test_ranged_read(self):
        content = "\n".join(f"line {i}" for i in range(100))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({
            "file_path": "/workspace/f.py",
            "offset": 10,
            "limit": 5,
        }))
        assert isinstance(result, ToolResult)
        assert result.meta.get("mark_read") is True

    def test_ranged_read_marks_read(self):
        content = "\n".join(f"line {i}" for i in range(100))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({
            "file_path": "/workspace/f.py",
            "offset": 0,
            "limit": 5,
        }))
        assert isinstance(result, ToolResult)
        assert result.meta.get("mark_read") is True


class TestReadToolRunnerState:
    def test_execute_with_context_updates_runner_state(self):
        content = "hello"
        tool = ReadTool(session=make_session(content=content))
        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)
        result = asyncio.run(tool.execute_with_context(
            {"file_path": "/workspace/f.py"}, ctx
        ))
        assert "/workspace/f.py" in state.get("read_files", set())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/tools/builtin/test_read_tool.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `read_tool.py`**

```python
"""matmaster/tools/builtin/read_tool.py

ReadTool — read file content via session with line numbers (cat -n).

CC Reference: tools/FileReadTool/FileReadTool.ts + prompt.ts
"""

from __future__ import annotations

import asyncio
import posixpath
from typing import Any, ClassVar

from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool

MAX_READ_LINES = 2000
MAX_READ_CHARS = 200_000
PREVIEW_LINES = 50


class ReadTool(BuiltinTool):
    """Read file content with line numbers (cat -n semantics).

    CC name: Read (FileReadTool)
    """

    name: ClassVar[str] = "Read"
    description: ClassVar[str] = "Read a file from the local filesystem."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "The absolute path to the file to read"
                ),
            },
            "offset": {
                "type": "integer",
                "description": (
                    "The line number to start reading from (0-indexed). "
                    "Only provide if the file is too large to read at once."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "The number of lines to read. Only provide if the "
                    "file is too large to read at once."
                ),
            },
        },
        "required": ["file_path"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="shared_read"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.read"})
    effect_level: ClassVar[str] = "none"
    fast_path_eligible: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 12_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_FS

    def prompt(self, ctx=None) -> str:
        return (
            "Reads a file from the local filesystem. You can access any "
            "file directly by using this tool.\n"
            "Assume this tool is able to read all files on the machine. "
            "If the User provides a path to a file assume that path is valid.\n\n"
            "Usage:\n"
            "- The file_path parameter must be an absolute path, not a relative path\n"
            f"- By default, it reads up to {MAX_READ_LINES} lines starting from "
            "the beginning of the file\n"
            "- When you already know which part of the file you need, only read "
            "that part. This can be important for larger files.\n"
            "- Results are returned using cat -n format, with line numbers starting at 1\n"
            "- This tool can only read files, not directories. To read a directory, "
            "use an ls command via the Bash tool.\n"
            "- If you read a file that exists but has empty contents you will "
            "receive a system reminder warning in place of file contents."
        )

    # -- Core execution --

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self._execute_internal(arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> ToolResult:
        try:
            result = await asyncio.to_thread(self._execute_internal, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return ToolResult(status="error", content=f"Error: {e}")

        if exec_ctx is not None and exec_ctx.runner_state is not None:
            if result.meta.get("mark_read"):
                path = posixpath.normpath(arguments.get("file_path", ""))
                read_files = set(exec_ctx.runner_state.get("read_files", set()))
                read_files.add(path)
                exec_ctx.runner_state.set("read_files", read_files)

        return result

    def _execute_internal(self, arguments: dict[str, Any]) -> ToolResult:
        session = self._require_session()

        file_path: str = arguments.get("file_path", "")
        offset: int | None = arguments.get("offset")
        limit: int | None = arguments.get("limit")

        if not session.is_file(file_path):
            return ToolResult(
                status="error",
                content=f"Error: {file_path} is not a file or does not exist",
            )

        content: str = session.read_file(file_path)
        lines = content.splitlines()
        total = len(lines)

        ranged = offset is not None or limit is not None

        if ranged:
            return self._ranged_read(file_path, lines, total, offset, limit)
        else:
            return self._full_read(file_path, lines, total)

    # -- Full-read mode --

    def _full_read(self, file_path: str, lines: list[str], total: int) -> ToolResult:
        if total == 0:
            return ToolResult(
                content=(
                    f"<system-reminder>Warning: the file {file_path} exists "
                    "but the contents are empty.</system-reminder>"
                ),
                meta={"mark_read": True},
            )

        if total <= MAX_READ_LINES:
            output = self._format_lines(lines, file_path, init_line=1)
            truncated, result = self._apply_char_limit(output)
            return ToolResult(content=result, meta={"mark_read": True})

        preview = lines[:PREVIEW_LINES]
        preview_text = self._format_lines(preview, file_path, init_line=1)
        _, result = self._apply_char_limit(
            f"Error: file has {total} lines, exceeds read limit "
            f"({MAX_READ_LINES} lines).\n"
            f"Use offset and limit to read portions, e.g. "
            f"offset=0, limit={MAX_READ_LINES} for the first {MAX_READ_LINES} lines.\n\n"
            f"Preview (first {PREVIEW_LINES} lines):\n"
            f"{preview_text}"
        )
        return ToolResult(status="error", content=result)

    # -- Ranged-read mode --

    def _ranged_read(
        self,
        file_path: str,
        lines: list[str],
        total: int,
        offset: int | None,
        limit: int | None,
    ) -> ToolResult:
        start = offset if offset is not None else 0

        if start < 0 or start >= total:
            return ToolResult(
                status="error",
                content=f"Error: offset {start} is out of range [0, {total - 1}].",
            )

        remaining = total - start
        requested = limit if limit is not None else remaining
        count = min(requested, remaining, MAX_READ_LINES)
        end = start + count

        selected = lines[start:end]
        output = self._format_lines(selected, file_path, init_line=start + 1)

        if count < requested:
            output += (
                f"\n[Note: showing {count} of {remaining} remaining lines, "
                f"capped at {MAX_READ_LINES}. Use offset={end} to continue reading.]"
            )

        _, result = self._apply_char_limit(output)
        return ToolResult(content=result, meta={"mark_read": True})

    # -- Helpers --

    @staticmethod
    def _format_lines(lines: list[str], descriptor: str, init_line: int = 1) -> str:
        numbered = "\n".join(
            f"{i + init_line:6}\t{line}" for i, line in enumerate(lines)
        )
        return f"Here's the result of running `cat -n` on {descriptor}:\n{numbered}"

    @staticmethod
    def _apply_char_limit(output: str) -> tuple[bool, str]:
        if len(output) <= MAX_READ_CHARS:
            return False, output
        return True, (
            output[:MAX_READ_CHARS]
            + "\n[Output truncated at "
            + str(MAX_READ_CHARS)
            + " chars. Use offset/limit for smaller ranges.]"
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/matmaster/tools/builtin/test_read_tool.py -v`
Expected: all PASS

- [ ] **Step 5: Update `__init__.py` and commit**

Append to `matmaster/tools/builtin/__init__.py`:
```python
from matmaster.tools.builtin.read_tool import ReadTool
# add "ReadTool" to __all__
```

```bash
git add matmaster/tools/builtin/read_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_read_tool.py
git commit -m "feat(tools): add ReadTool with cat -n format and runner_state mark_read"
```

---

## Task 2: EditTool

**Files:**
- Create: `matmaster/tools/builtin/edit_tool.py`
- Test: `tests/matmaster/tools/builtin/test_edit_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_edit_tool.py"""
import asyncio
import pytest
from unittest.mock import MagicMock
from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.types.tool_runner_state import ToolRunnerState


def make_session(content="hello world"):
    s = MagicMock()
    s.read_file.return_value = content
    s.write_file.return_value = None
    return s


class TestEditToolMetadata:
    def test_name(self):
        assert EditTool.name == "Edit"

    def test_schema_has_replace_all(self):
        assert "replace_all" in EditTool.json_schema["properties"]


class TestEditValidation:
    def test_empty_old_string(self):
        tool = EditTool(session=make_session())
        result = asyncio.run(tool.validate_input(
            {"file_path": "/f", "old_string": "", "new_string": "x"}, None
        ))
        assert result is not None
        assert result.decision == "deny"

    def test_same_strings(self):
        tool = EditTool(session=make_session())
        result = asyncio.run(tool.validate_input(
            {"file_path": "/f", "old_string": "x", "new_string": "x"}, None
        ))
        assert result is not None
        assert result.decision == "deny"

    def test_read_before_modify(self):
        tool = EditTool(session=make_session())
        state = ToolRunnerState()
        result = asyncio.run(tool.validate_input(
            {"file_path": "/workspace/f.py", "old_string": "a", "new_string": "b"},
            state,
        ))
        assert result is not None
        assert result.decision == "deny"
        assert "read" in result.reason.lower()

    def test_read_before_modify_passes(self):
        tool = EditTool(session=make_session())
        state = ToolRunnerState()
        state.set("read_files", {"/workspace/f.py"})
        result = asyncio.run(tool.validate_input(
            {"file_path": "/workspace/f.py", "old_string": "a", "new_string": "b"},
            state,
        ))
        assert result is None


class TestEditExecution:
    def test_single_match_replace(self):
        tool = EditTool(session=make_session("hello world"))
        result = asyncio.run(tool.execute({
            "file_path": "/f", "old_string": "hello", "new_string": "goodbye",
        }))
        assert isinstance(result, str)
        assert "edited" in result.lower() or "goodbye" in result

    def test_no_match_error(self):
        tool = EditTool(session=make_session("hello world"))
        result = asyncio.run(tool.execute({
            "file_path": "/f", "old_string": "notfound", "new_string": "x",
        }))
        assert "not" in result.lower() or "error" in result.lower()

    def test_multiple_matches_error(self):
        tool = EditTool(session=make_session("aaa bbb aaa"))
        result = asyncio.run(tool.execute({
            "file_path": "/f", "old_string": "aaa", "new_string": "x",
        }))
        assert "multiple" in result.lower() or "unique" in result.lower()

    def test_replace_all(self):
        session = make_session("aaa bbb aaa")
        tool = EditTool(session=session)
        result = asyncio.run(tool.execute({
            "file_path": "/f", "old_string": "aaa", "new_string": "x",
            "replace_all": True,
        }))
        session.write_file.assert_called_once()
        written = session.write_file.call_args[0][1]
        assert written == "x bbb x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/tools/builtin/test_edit_tool.py -v`

- [ ] **Step 3: Implement `edit_tool.py`**

```python
"""matmaster/tools/builtin/edit_tool.py

EditTool — str_replace editing via session.

CC Reference: tools/FileEditTool/ (constants.ts, types.ts, prompt.ts)
CC name: Edit
"""

from __future__ import annotations

import posixpath
import re
from typing import Any, ClassVar

from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool

SNIPPET_LINES = 4
MAX_OUTPUT_SIZE = 16_000


class EditTool(BuiltinTool):
    """Edit a file using str_replace with unique-match enforcement.

    CC name: Edit (FileEditTool)
    """

    name: ClassVar[str] = "Edit"
    description: ClassVar[str] = "Performs exact string replacements in files."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to modify",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace",
            },
            "new_string": {
                "type": "string",
                "description": (
                    "The text to replace it with "
                    "(must be different from old_string)"
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "Replace all occurrences of old_string (default false)"
                ),
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.write"})
    effect_level: ClassVar[str] = "local_mutation"
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_FS

    def prompt(self, ctx=None) -> str:
        return (
            "Performs exact string replacements in files.\n\n"
            "Usage:\n"
            "- You must use your `Read` tool at least once in the conversation "
            "before editing. This tool will error if you attempt an edit without "
            "reading the file.\n"
            "- ALWAYS prefer editing existing files in the codebase. NEVER write "
            "new files unless explicitly required.\n"
            "- The edit will FAIL if `old_string` is not unique in the file. "
            "Either provide a larger string with more surrounding context to make "
            "it unique or use `replace_all` to change every instance of `old_string`.\n"
            "- Use `replace_all` for replacing and renaming strings across the file."
        )

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        old_str = arguments.get("old_string", "")
        new_str = arguments.get("new_string", "")
        if not old_str:
            return ToolDecision(decision="deny", reason="old_string must not be empty")
        if old_str == new_str:
            return ToolDecision(
                decision="deny",
                reason="old_string and new_string are identical, no edit needed",
            )
        if runner_state is not None:
            read_files = runner_state.get("read_files", set())
            path = posixpath.normpath(arguments.get("file_path", ""))
            if path and path not in read_files:
                return ToolDecision(
                    decision="deny",
                    reason=f"File '{path}' must be read before editing",
                    guidance="Read the file first using Read.",
                )
        return None

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        file_path: str = arguments.get("file_path", "")
        old_str: str = arguments.get("old_string", "")
        new_str: str = arguments.get("new_string", "")
        replace_all: bool = arguments.get("replace_all", False)

        if old_str == new_str:
            return "Error: No replacement was performed. `old_string` and `new_string` must be different."

        content: str = session.read_file(file_path)
        pattern = re.escape(old_str)
        matches = list(re.finditer(pattern, content))

        if not matches:
            return (
                f"No replacement was performed, old_string "
                f"did not appear verbatim in {file_path}."
            )

        if replace_all:
            new_content = content.replace(old_str, new_str)
            count = len(matches)
            session.write_file(file_path, new_content)
            return f"Replaced {count} occurrence(s) in {file_path}."

        if len(matches) > 1:
            line_numbers = sorted(
                {content.count("\n", 0, m.start()) + 1 for m in matches}
            )
            return (
                f"No replacement was performed. Multiple occurrences "
                f"of old_string in lines {line_numbers}. "
                f"Please ensure it is unique or use replace_all=true."
            )

        match = matches[0]
        replacement_line = content.count("\n", 0, match.start()) + 1
        new_content = content[: match.start()] + new_str + content[match.end() :]

        session.write_file(file_path, new_content)

        start_line = max(0, replacement_line - SNIPPET_LINES)
        end_line = replacement_line + SNIPPET_LINES + new_str.count("\n") + 1
        snippet = "\n".join(new_content.split("\n")[start_line : end_line + 1])
        if len(snippet) > MAX_OUTPUT_SIZE:
            half = MAX_OUTPUT_SIZE // 2
            snippet = snippet[:half] + "\n...\n" + snippet[-half:]

        numbered = "\n".join(
            f"{i + start_line + 1:6}\t{line}"
            for i, line in enumerate(snippet.split("\n"))
        )
        return (
            f"The file {file_path} has been edited. "
            f"Here's the result of running `cat -n` on a snippet of {file_path}:\n"
            f"{numbered}"
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/matmaster/tools/builtin/test_edit_tool.py -v`

- [ ] **Step 5: Update `__init__.py` and commit**

```bash
git add matmaster/tools/builtin/edit_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_edit_tool.py
git commit -m "feat(tools): add EditTool with str_replace and replace_all"
```

---

## Task 3: WriteTool

**Files:**
- Create: `matmaster/tools/builtin/write_tool.py`
- Test: `tests/matmaster/tools/builtin/test_write_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_write_tool.py"""
import asyncio
import pytest
from pathlib import PurePosixPath
from unittest.mock import MagicMock
from matmaster.tools.builtin.write_tool import WriteTool
from matmaster.types.tool_runner_state import ToolRunnerState


def make_session(path_exists=False):
    s = MagicMock()
    s.path_exists.return_value = path_exists
    s.write_file.return_value = None
    return s


class TestWriteToolMetadata:
    def test_name(self):
        assert WriteTool.name == "Write"


class TestWriteValidation:
    def test_empty_path(self):
        tool = WriteTool(session=make_session(), workdir=PurePosixPath("/workspace"))
        result = asyncio.run(tool.validate_input({"file_path": "", "content": "x"}, None))
        assert result is not None
        assert result.decision == "deny"

    def test_outside_workspace(self):
        tool = WriteTool(session=make_session(), workdir=PurePosixPath("/workspace"))
        result = asyncio.run(tool.validate_input(
            {"file_path": "/etc/passwd", "content": "x"}, None
        ))
        assert result is not None
        assert result.decision == "deny"

    def test_existing_file_without_read(self):
        tool = WriteTool(session=make_session(path_exists=True), workdir=PurePosixPath("/workspace"))
        state = ToolRunnerState()
        result = asyncio.run(tool.validate_input(
            {"file_path": "/workspace/f.py", "content": "x"}, state
        ))
        assert result is not None
        assert result.decision == "deny"

    def test_existing_file_with_read(self):
        tool = WriteTool(session=make_session(path_exists=True), workdir=PurePosixPath("/workspace"))
        state = ToolRunnerState()
        state.set("read_files", {"/workspace/f.py"})
        result = asyncio.run(tool.validate_input(
            {"file_path": "/workspace/f.py", "content": "x"}, state
        ))
        assert result is None

    def test_new_file_no_read_needed(self):
        tool = WriteTool(session=make_session(path_exists=False), workdir=PurePosixPath("/workspace"))
        state = ToolRunnerState()
        result = asyncio.run(tool.validate_input(
            {"file_path": "/workspace/new.py", "content": "x"}, state
        ))
        assert result is None


class TestWriteExecution:
    def test_write_succeeds(self):
        session = make_session()
        tool = WriteTool(session=session, workdir=PurePosixPath("/workspace"))
        result = asyncio.run(tool.execute({"file_path": "/workspace/f.py", "content": "hello"}))
        session.write_file.assert_called_once_with("/workspace/f.py", "hello")
        assert "successfully" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/tools/builtin/test_write_tool.py -v`

- [ ] **Step 3: Implement `write_tool.py`**

```python
"""matmaster/tools/builtin/write_tool.py

WriteTool — create/overwrite files via session.

CC Reference: tools/FileWriteTool/ (prompt.ts, FileWriteTool.ts)
CC name: Write
"""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from typing import Any, ClassVar

from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool


class WriteTool(BuiltinTool):
    """Write content to a file via session.

    CC name: Write (FileWriteTool)
    """

    name: ClassVar[str] = "Write"
    description: ClassVar[str] = "Write a file to the local filesystem."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "The absolute path to the file to write "
                    "(must be absolute, not relative)"
                ),
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["file_path", "content"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.write"})
    effect_level: ClassVar[str] = "local_mutation"
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_FS

    def prompt(self, ctx=None) -> str:
        return (
            "Writes a file to the local filesystem.\n\n"
            "Usage:\n"
            "- This tool will overwrite the existing file if there is one at "
            "the provided path.\n"
            "- If this is an existing file, you MUST use the Read tool first "
            "to read the file's contents. This tool will fail if you did not "
            "read the file first.\n"
            "- Prefer the Edit tool for modifying existing files — it only sends "
            "the diff. Only use this tool to create new files or for complete rewrites."
        )

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        file_path = arguments.get("file_path", "")
        if not file_path:
            return ToolDecision(decision="deny", reason="file_path is required")
        if self._workdir is None:
            return ToolDecision(decision="deny", reason="workdir not set")

        try:
            resolved = PurePosixPath(posixpath.normpath(file_path))
            if not resolved.is_relative_to(self._workdir):
                return ToolDecision(
                    decision="deny",
                    reason=f"file_path '{file_path}' is outside workspace boundary",
                )
        except (TypeError, ValueError):
            return ToolDecision(decision="deny", reason=f"invalid file_path: '{file_path}'")

        if runner_state is not None and self._session is not None:
            normalized = posixpath.normpath(file_path)
            if self._session.path_exists(file_path):
                read_files = runner_state.get("read_files", set())
                if normalized not in read_files:
                    return ToolDecision(
                        decision="deny",
                        reason=f"File '{file_path}' must be read before overwrite",
                        guidance="Read the file first using Read.",
                    )
        return None

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()
        file_path: str = arguments.get("file_path", "")
        content: str = arguments.get("content", "")
        session.write_file(file_path, content)
        return f"File written successfully to: {file_path}"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/matmaster/tools/builtin/test_write_tool.py -v`

- [ ] **Step 5: Update `__init__.py` and commit**

```bash
git add matmaster/tools/builtin/write_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_write_tool.py
git commit -m "feat(tools): add WriteTool with read-before-modify validation"
```

---

## Final `__init__.py` state after Plan 01

```python
from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.tools.builtin.write_tool import WriteTool

__all__ = [
    "BuiltinTool",
    "ReadTool",
    "EditTool",
    "WriteTool",
]
```
