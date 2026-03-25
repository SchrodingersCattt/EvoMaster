# ReadTool Optimization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ReadTool's silent mid-truncation with explicit overlimit error + preview, migrate parameters from `line_range` to `offset`/`limit`, add character-level fallback guard, and decouple from evomaster imports.

**Architecture:** Single-file rewrite of `ReadTool._execute` with two-mode branching (full-read vs ranged-read), conditional `mark_read`, and a `_maybe_truncate_chars` fallback. All changes are internal to ReadTool — no other components change.

**Tech Stack:** Python 3.10+, pytest, unittest.mock

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `matmaster/tools/builtin/read_tool.py` | Rewrite | Constants, schema, description, `_execute` logic, `_format_with_line_numbers` |
| `tests/matmaster/tools/test_read_tool.py` | Rewrite | 24 test cases (15 from spec + 9 protocol/regression) |

No new files created. No other files modified.

---

## Chunk 1: Tests + Implementation

### Task 1: Write all failing tests

**Files:**
- Rewrite: `tests/matmaster/tools/test_read_tool.py`

**Reference:** Spec test plan at `docs/superpowers/specs/2026-03-25-read-tool-optimization-design.md` lines 138-158.

- [ ] **Step 1: Write the complete test file**

```python
"""Tests for ReadTool -- file reading with line numbers and overlimit protection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from matmaster.tools.builtin.read_tool import (
    MAX_READ_CHARS,
    MAX_READ_LINES,
    PREVIEW_LINES,
    ReadTool,
)
from matmaster.tools.builtin.read_tracker import ReadTracker
from matmaster.tools.tool_registry import Tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_session() -> MagicMock:
    """Mock session with is_file=True and 5-line content."""
    session = MagicMock()
    session.is_file.return_value = True
    session.read_file.return_value = "line1\nline2\nline3\nline4\nline5"
    return session


def _make_content(n_lines: int) -> str:
    """Generate n lines of content: 'line1\\nline2\\n...lineN'."""
    return "\n".join(f"line{i}" for i in range(1, n_lines + 1))


# ---------------------------------------------------------------------------
# Basic protocol
# ---------------------------------------------------------------------------

class TestReadToolBasic:
    """ReadTool properties and protocol."""

    def test_name(self) -> None:
        tool = ReadTool()
        assert tool.name == "read_file"

    def test_tool_protocol(self) -> None:
        tool = ReadTool()
        assert isinstance(tool, Tool)

    def test_description_has_routing_declaration(self) -> None:
        desc = ReadTool.description
        assert "ALWAYS" in desc
        assert "NEVER" in desc

    def test_schema_has_offset_and_limit(self) -> None:
        props = ReadTool.json_schema["properties"]
        assert "offset" in props
        assert "limit" in props
        assert "line_range" not in props


# ---------------------------------------------------------------------------
# Full-read mode (no offset/limit)
# ---------------------------------------------------------------------------

class TestFullRead:
    """Full-read mode: no offset/limit provided."""

    def test_read_full_within_limit(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/workspace/a.py"})
        assert "cat -n" in result
        assert "     1\tline1" in result
        assert "     5\tline5" in result

    def test_read_exceeds_limit_returns_error_and_preview(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = _make_content(MAX_READ_LINES + 500)
        tool = ReadTool(session=session)
        result = tool.execute({"file_path": "/workspace/big.py"})

        assert "Error:" in result
        assert str(MAX_READ_LINES + 500) in result
        # Preview contains first PREVIEW_LINES lines
        assert "     1\tline1" in result
        assert f"    {PREVIEW_LINES}\tline{PREVIEW_LINES}" in result
        # Does NOT contain lines beyond preview
        assert f"line{PREVIEW_LINES + 1}" not in result

    def test_tracker_marked_on_within_limit(self, mock_session: MagicMock) -> None:
        tracker = ReadTracker()
        tool = ReadTool(session=mock_session, tracker=tracker)
        tool.execute({"file_path": "/workspace/a.py"})
        assert tracker.has_been_read("/workspace/a.py") is True

    def test_tracker_not_marked_on_overlimit(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = _make_content(MAX_READ_LINES + 100)
        tracker = ReadTracker()
        tool = ReadTool(session=session, tracker=tracker)
        tool.execute({"file_path": "/workspace/big.py"})
        assert tracker.has_been_read("/workspace/big.py") is False

    def test_read_empty_file(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = ""
        tool = ReadTool(session=session)
        result = tool.execute({"file_path": "/workspace/empty.py"})
        assert "Error" not in result
        assert "cat -n" in result

    def test_file_with_trailing_newline(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        # 5 lines with trailing newline — splitlines() should give 5, not 6
        session.read_file.return_value = "a\nb\nc\nd\ne\n"
        tool = ReadTool(session=session)
        result = tool.execute({"file_path": "/workspace/f.py"})
        assert "     5\te" in result
        # No 6th line
        assert "     6\t" not in result


# ---------------------------------------------------------------------------
# Ranged-read mode (offset and/or limit)
# ---------------------------------------------------------------------------

class TestRangedRead:
    """Ranged-read mode: offset and/or limit provided."""

    def test_read_with_offset_and_limit(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/workspace/a.py", "offset": 2, "limit": 2})
        assert "     2\tline2" in result
        assert "     3\tline3" in result
        assert "line1" not in result
        assert "line4" not in result

    def test_read_with_offset_only(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/workspace/a.py", "offset": 3})
        assert "     3\tline3" in result
        assert "     5\tline5" in result
        assert "line1" not in result
        assert "line2" not in result

    def test_read_with_only_limit(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/workspace/a.py", "limit": 3})
        assert "     1\tline1" in result
        assert "     3\tline3" in result
        assert "line4" not in result

    def test_read_with_limit_exceeds_max(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = _make_content(MAX_READ_LINES + 500)
        tool = ReadTool(session=session)
        result = tool.execute({
            "file_path": "/workspace/big.py",
            "offset": 1,
            "limit": MAX_READ_LINES + 500,
        })
        # Should return content (not error) but with truncation notice
        assert "cat -n" in result
        assert "     1\tline1" in result
        assert f"  {MAX_READ_LINES}\tline{MAX_READ_LINES}" in result
        assert "[Note:" in result
        assert "capped" in result.lower() or "showing" in result.lower()

    def test_offset_only_truncated_with_notice(self) -> None:
        total = MAX_READ_LINES + 1000
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = _make_content(total)
        tool = ReadTool(session=session)
        result = tool.execute({"file_path": "/workspace/big.py", "offset": 1})
        assert "cat -n" in result
        assert "[Note:" in result

    def test_tracker_marked_on_ranged_read(self, mock_session: MagicMock) -> None:
        tracker = ReadTracker()
        tool = ReadTool(session=mock_session, tracker=tracker)
        tool.execute({"file_path": "/workspace/a.py", "offset": 1, "limit": 3})
        assert tracker.has_been_read("/workspace/a.py") is True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    """Parameter validation."""

    def test_offset_out_of_range(self, mock_session: MagicMock) -> None:
        tracker = ReadTracker()
        tool = ReadTool(session=mock_session, tracker=tracker)
        result = tool.execute({"file_path": "/workspace/a.py", "offset": 100})
        assert "Error" in result
        assert tracker.has_been_read("/workspace/a.py") is False

    def test_offset_zero_rejected(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/workspace/a.py", "offset": 0})
        assert "Error" in result

    def test_limit_negative_rejected(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/workspace/a.py", "limit": -1})
        assert "Error" in result

    def test_file_not_found(self, mock_session: MagicMock) -> None:
        mock_session.is_file.return_value = False
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/nonexist"})
        assert "Error" in result
        assert "is not a file" in result

    def test_no_session_raises(self) -> None:
        tool = ReadTool()
        result = tool.execute({"file_path": "/workspace/a.py"})
        assert "Error" in result
        assert "session" in result.lower()

    def test_no_tracker(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session, tracker=None)
        result = tool.execute({"file_path": "/workspace/a.py"})
        assert "cat -n" in result


# ---------------------------------------------------------------------------
# Character limit fallback
# ---------------------------------------------------------------------------

class TestCharLimit:
    """MAX_READ_CHARS fallback guard."""

    def test_char_limit_truncation(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        # 10 lines but each line is huge (exceeds MAX_READ_CHARS total)
        huge_line = "x" * (MAX_READ_CHARS // 5)
        session.read_file.return_value = "\n".join([huge_line] * 10)
        tool = ReadTool(session=session)
        result = tool.execute({"file_path": "/workspace/huge.json"})
        # Output should be truncated and contain notice
        assert "[Output truncated" in result
        assert len(result) <= MAX_READ_CHARS + 500  # some margin for the notice text

    def test_char_truncated_ranged_read_not_marked(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        huge_line = "x" * (MAX_READ_CHARS // 5)
        session.read_file.return_value = "\n".join([huge_line] * 10)
        tracker = ReadTracker()
        tool = ReadTool(session=session, tracker=tracker)
        result = tool.execute({"file_path": "/workspace/huge.json", "offset": 1, "limit": 10})
        assert "[Output truncated" in result
        assert tracker.has_been_read("/workspace/huge.json") is False
```

- [ ] **Step 2: Run tests to verify they all fail**

Run: `uv run pytest tests/matmaster/tools/test_read_tool.py -v 2>&1 | tail -30`
Expected: Import errors for `MAX_READ_CHARS`, `MAX_READ_LINES`, `PREVIEW_LINES` (constants don't exist yet).

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/matmaster/tools/test_read_tool.py
git commit -m "test(read-tool): rewrite tests for offset/limit, overlimit, char guard"
```

### Task 2: Rewrite ReadTool implementation

**Files:**
- Rewrite: `matmaster/tools/builtin/read_tool.py`

**Reference:** Spec execution logic at `docs/superpowers/specs/2026-03-25-read-tool-optimization-design.md` lines 72-107.

- [ ] **Step 4: Write the complete implementation**

```python
"""ReadTool -- read remote file content via session.

Returns line-numbered output (cat -n format). Supports offset/limit
for partial reads. Conditional mark_read via ReadTracker:
- Full-read within limit: mark_read
- Full-read overlimit (error + preview): no mark_read
- Ranged read (offset/limit): mark_read
"""

from __future__ import annotations

import posixpath
from typing import Any, ClassVar

from .base import BuiltinTool
from .read_tracker import ReadTracker

MAX_READ_LINES = 2000
MAX_READ_CHARS = 200_000
PREVIEW_LINES = 50


class ReadTool(BuiltinTool):
    """Read file content with line numbers (cat -n semantics)."""

    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read file contents with line numbers (cat -n format).\n\n"
        "Usage:\n"
        "- ALWAYS use read_file to read files. NEVER use cat/head/tail via execute_bash.\n"
        "- Files up to 2000 lines are returned in full. Larger files return an error with preview.\n"
        "- Use offset and limit to read specific portions of large files.\n"
        "- Always read a file before attempting to edit or overwrite it."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": (
                    "Line number to start reading from (1-indexed). Defaults to 1."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Number of lines to read. "
                    "Defaults to reading to end of file (up to 2000 lines)."
                ),
            },
        },
        "required": ["file_path"],
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        tracker: ReadTracker | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._tracker = tracker

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        file_path: str = arguments.get("file_path", "")
        offset: int | None = arguments.get("offset")
        limit: int | None = arguments.get("limit")

        # --- parameter validation ---
        if offset is not None and offset < 1:
            return "Error: offset must be >= 1."
        if limit is not None and limit < 1:
            return "Error: limit must be >= 1."

        # --- file check ---
        if not session.is_file(file_path):
            return f"Error: {file_path} is not a file"

        # --- read content ---
        content: str = session.read_file(file_path)
        lines = content.splitlines()
        total = len(lines)

        ranged = offset is not None or limit is not None

        if ranged:
            return self._ranged_read(file_path, lines, total, offset, limit)
        else:
            return self._full_read(file_path, lines, total)

    # ------------------------------------------------------------------
    # Full-read mode
    # ------------------------------------------------------------------

    def _full_read(
        self, file_path: str, lines: list[str], total: int
    ) -> str:
        if total <= MAX_READ_LINES:
            output = self._format_lines(lines, file_path, init_line=1)
            truncated, result = self._apply_char_limit(output)
            if not truncated:
                self._mark(file_path)
            return result

        # Overlimit: error + preview (no mark_read regardless)
        preview = lines[:PREVIEW_LINES]
        preview_text = self._format_lines(preview, file_path, init_line=1)
        _, result = self._apply_char_limit(
            f"Error: file has {total} lines, exceeds read limit "
            f"({MAX_READ_LINES} lines).\n"
            f"Use offset and limit to read portions, e.g. "
            f"offset=1, limit={MAX_READ_LINES} for the first {MAX_READ_LINES} lines.\n\n"
            f"Preview (first {PREVIEW_LINES} lines):\n"
            f"{preview_text}"
        )
        return result

    # ------------------------------------------------------------------
    # Ranged-read mode
    # ------------------------------------------------------------------

    def _ranged_read(
        self,
        file_path: str,
        lines: list[str],
        total: int,
        offset: int | None,
        limit: int | None,
    ) -> str:
        start = offset if offset is not None else 1

        if start < 1 or start > total:
            return (
                f"Error: offset {start} is out of range [1, {total}]."
            )

        remaining = total - start + 1
        requested = limit if limit is not None else remaining
        count = min(requested, remaining, MAX_READ_LINES)
        end = start + count - 1

        selected = lines[start - 1 : end]
        output = self._format_lines(selected, file_path, init_line=start)

        # Truncation notice (appended before char limit so it's part of content)
        if count < requested:
            output += (
                f"\n[Note: showing {count} of {remaining} remaining lines, "
                f"capped at {MAX_READ_LINES}. Use offset={end + 1} to continue reading.]"
            )

        truncated, result = self._apply_char_limit(output)
        if not truncated:
            self._mark(file_path)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mark(self, file_path: str) -> None:
        if self._tracker is not None:
            self._tracker.mark_read(posixpath.normpath(file_path))

    @staticmethod
    def _format_lines(
        lines: list[str], descriptor: str, init_line: int = 1
    ) -> str:
        """Format lines with line numbers in cat -n style."""
        numbered = "\n".join(
            f"{i + init_line:6}\t{line}"
            for i, line in enumerate(lines)
        )
        return (
            f"Here's the result of running `cat -n` on {descriptor}:\n"
            f"{numbered}"
        )

    @staticmethod
    def _apply_char_limit(output: str) -> tuple[bool, str]:
        """Truncate output if it exceeds MAX_READ_CHARS.

        Returns (truncated, output) so callers can decide whether to mark_read.
        """
        if len(output) <= MAX_READ_CHARS:
            return False, output
        return True, (
            output[:MAX_READ_CHARS]
            + "\n[Output truncated at "
            + str(MAX_READ_CHARS)
            + " chars. Use offset/limit for smaller ranges.]"
        )
```

- [ ] **Step 5: Run all ReadTool tests**

Run: `uv run pytest tests/matmaster/tools/test_read_tool.py -v`
Expected: All 24 tests PASS.

- [ ] **Step 6: Run description/routing tests to verify no regression**

Run: `uv run pytest tests/matmaster/tools/test_tool_descriptions.py -v`
Expected: All 6 tests PASS (especially `test_dedicated_tools_have_routing_declaration` and `test_routing_consistency`).

- [ ] **Step 7: Run full test suite to verify no regressions**

Run: `uv run pytest tests/ -x -q`
Expected: All tests PASS. No other component imports `line_range` from ReadTool.

- [ ] **Step 8: Commit implementation + tests**

```bash
git add matmaster/tools/builtin/read_tool.py tests/matmaster/tools/test_read_tool.py
git commit -m "refactor(read-tool): replace silent truncation with overlimit error + preview

- Replace line_range with offset/limit parameters
- Add MAX_READ_LINES (2000) + MAX_READ_CHARS (200k) guards
- Overlimit: return error + total lines + first 50 lines preview
- Ranged read truncation always emits continuation notice
- mark_read only on successful content return, not overlimit preview
- Remove evomaster dependency (MAX_OUTPUT_SIZE, maybe_truncate)
- Add parameter validation (offset >= 1, limit >= 1)"
```
