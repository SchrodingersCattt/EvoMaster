# Shell & Search Tools (Bash + Glob + Grep) — Plan 02

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Bash, Glob, and Grep tools that execute shell commands through the session abstraction.

**Architecture:** Bash delegates to `session.exec_bash()`. Glob/Grep build shell commands with `shell_escape()` protection and run via `session.exec_bash()`. Grep detects `rg` availability at first call, caches result, falls back to `grep`.

**Tech Stack:** Python 3.10+, asyncio, shlex, posixpath

**Spec:** `docs/superpowers/specs/2026-04-04-builtin-tools-design.md` — Section 3

**Depends on:** Plan 00 (infrastructure)

---

## CC Source Reference

### Bash
- **Name:** `Bash` (`tools/BashTool/toolName.ts:2`)
- **Description:** `"Executes a given bash command and returns its output."` (prompt.ts `getSimplePrompt()`)
- **Prompt:** `getSimplePrompt()` — extensive: tool preferences, multiple commands, git, sleep avoidance, sandbox
- **Schema:** `command: string`, `timeout?: number` (ms, default 120000), `description?: string`, `run_in_background?: boolean`
- **MatMaster adaptation:** Drop `run_in_background`, `dangerouslyDisableSandbox`. Keep `command`, `timeout` (ms), `description`.

### Glob
- **Name:** `Glob` (`tools/GlobTool/prompt.ts:1`)
- **Description:** Multi-line description about fast pattern matching, modification time sort
- **Schema:** `pattern: string`, `path?: string`

### Grep
- **Name:** `Grep` (`tools/GrepTool/prompt.ts:4`)
- **Description:** `getDescription()` — ripgrep-based search with output modes, regex, multiline
- **Schema** (`GrepTool.ts:33-89`): `pattern`, `path?`, `glob?`, `output_mode?`, `-A?`, `-B?`, `-C?`, `context?`, `-n?`, `-i?`, `type?`, `head_limit?`, `offset?`, `multiline?`
- **MatMaster adaptation:** Drop `type` and `multiline` (grep fallback doesn't support these). Keep all others.

---

## Task 1: BashTool

**Files:**
- Create: `matmaster/tools/builtin/bash_tool.py`
- Test: `tests/matmaster/tools/builtin/test_bash_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_bash_tool.py"""
import asyncio
import pytest
from unittest.mock import MagicMock
from matmaster.tools.builtin.bash_tool import BashTool


def make_session(output="hello", exit_code=0, working_dir="/workspace"):
    s = MagicMock()
    s.exec_bash.return_value = {
        "output": output,
        "exit_code": exit_code,
        "working_dir": working_dir,
    }
    return s


class TestBashToolMetadata:
    def test_name(self):
        assert BashTool.name == "Bash"

    def test_plane(self):
        from matmaster.types.topology import ToolPlane
        assert BashTool.plane == ToolPlane.SESSION_SHELL

    def test_has_prompt(self):
        tool = BashTool()
        assert tool.prompt() is not None
        assert "Read" in tool.prompt()


class TestBashExecution:
    def test_simple_command(self):
        session = make_session(output="hello")
        tool = BashTool(session=session, workdir="/workspace")
        result = asyncio.run(tool.execute({"command": "echo hello"}))
        assert "hello" in result

    def test_empty_command_error(self):
        tool = BashTool(session=make_session())
        result = asyncio.run(tool.execute({"command": ""}))
        assert "error" in result.lower()

    def test_exit_code_in_output(self):
        session = make_session(exit_code=1)
        tool = BashTool(session=session)
        result = asyncio.run(tool.execute({"command": "false"}))
        assert "exit code 1" in result.lower()

    def test_timeout_conversion_ms_to_s(self):
        session = make_session()
        tool = BashTool(session=session)
        asyncio.run(tool.execute({"command": "ls", "timeout": 5000}))
        call_args = session.exec_bash.call_args
        assert call_args.kwargs.get("timeout") == 5 or call_args[1].get("timeout") == 5

    def test_no_session_error(self):
        tool = BashTool()
        result = asyncio.run(tool.execute({"command": "ls"}))
        assert "error" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement `bash_tool.py`**

```python
"""matmaster/tools/builtin/bash_tool.py

BashTool — execute bash commands via session.

CC Reference: tools/BashTool/ (toolName.ts, prompt.ts, BashTool.tsx)
CC name: Bash
"""

from __future__ import annotations

from typing import Any, ClassVar

from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool


class BashTool(BuiltinTool):
    """Execute bash commands in the session shell.

    CC name: Bash (BashTool)
    """

    name: ClassVar[str] = "Bash"
    description: ClassVar[str] = (
        "Executes a given bash command and returns its output."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Optional timeout in milliseconds (max 600000). "
                    "Default: 120000ms (2 minutes)."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does."
                ),
            },
        },
        "required": ["command"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="session", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"shell.execute"})
    effect_level: ClassVar[str] = "local_mutation"
    max_result_chars: ClassVar[int] = 30_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL

    def prompt(self, ctx=None) -> str:
        return (
            "Executes a given bash command and returns its output.\n\n"
            "The working directory persists between commands, but shell state "
            "does not.\n\n"
            "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, "
            "`head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly "
            "instructed. Instead, use the appropriate dedicated tool:\n"
            " - File search: Use Glob (NOT find or ls)\n"
            " - Content search: Use Grep (NOT grep or rg)\n"
            " - Read files: Use Read (NOT cat/head/tail)\n"
            " - Edit files: Use Edit (NOT sed/awk)\n"
            " - Write files: Use Write (NOT echo >/cat <<EOF)\n\n"
            "# Instructions\n"
            " - Always quote file paths that contain spaces with double quotes\n"
            " - You may specify an optional timeout in milliseconds (max 600000ms / "
            "10 minutes). By default, your command will timeout after 120000ms.\n"
            " - When issuing multiple commands that are independent, make multiple "
            "Bash tool calls in a single message.\n"
            " - For git commands: prefer creating a new commit rather than amending."
        )

    async def execute_with_context(self, arguments, exec_ctx):
        if exec_ctx is not None and hasattr(exec_ctx, "stop_event"):
            self._stop_event = exec_ctx.stop_event
        return await self.execute(arguments)

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        command: str = (arguments.get("command") or "").strip()
        if not command:
            return "Error: command is required and must not be empty."

        timeout_ms = arguments.get("timeout", 120_000)
        timeout_s = max(1, int(timeout_ms) // 1000) if timeout_ms and int(timeout_ms) > 0 else None

        result = session.exec_bash(
            command=command,
            timeout=timeout_s,
            stop_event=self._stop_event_for_exec(),
        )

        output = result.get("output", "") or result.get("stdout", "")
        exit_code = result.get("exit_code", -1)
        working_dir = result.get("working_dir", "")

        obs = output
        if working_dir:
            obs += f"\n[Current working directory: {working_dir}]"
        if exit_code != -1:
            obs += f"\n[Command finished with exit code {exit_code}]"

        return obs
```

- [ ] **Step 4: Run tests and commit**

```bash
python -m pytest tests/matmaster/tools/builtin/test_bash_tool.py -v
git add matmaster/tools/builtin/bash_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_bash_tool.py
git commit -m "feat(tools): add BashTool with timeout and prompt guidance"
```

---

## Task 2: GlobTool

**Files:**
- Create: `matmaster/tools/builtin/glob_tool.py`
- Test: `tests/matmaster/tools/builtin/test_glob_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_glob_tool.py"""
import asyncio
from unittest.mock import MagicMock
from matmaster.tools.builtin.glob_tool import GlobTool


def make_session(output="", exit_code=0):
    s = MagicMock()
    s.exec_bash.return_value = {"output": output, "exit_code": exit_code}
    return s


class TestGlobToolMetadata:
    def test_name(self):
        assert GlobTool.name == "Glob"

    def test_effect_level(self):
        assert GlobTool.effect_level == "none"

    def test_fast_path(self):
        assert GlobTool.fast_path_eligible is True


class TestGlobExecution:
    def test_no_results(self):
        tool = GlobTool(session=make_session(output=""), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": "*.xyz"}))
        assert "no files" in result.lower()

    def test_results_returned(self):
        tool = GlobTool(session=make_session(output="/workspace/a.py\n/workspace/b.py"), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": "*.py"}))
        assert "a.py" in result

    def test_shell_escape_applied(self):
        """Pattern with shell-dangerous chars should be escaped."""
        session = make_session(output="")
        tool = GlobTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "$(rm -rf /)"}))
        cmd = session.exec_bash.call_args[1].get("command") or session.exec_bash.call_args[0][0]
        assert "$(" not in cmd or "'" in cmd  # should be quoted
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement `glob_tool.py`**

```python
"""matmaster/tools/builtin/glob_tool.py

GlobTool — search file paths by glob pattern via session.

CC Reference: tools/GlobTool/ (prompt.ts, GlobTool.ts)
CC name: Glob
"""

from __future__ import annotations

from typing import Any, ClassVar

from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from ._path_safety import resolve_safe_path, shell_escape
from .base import BuiltinTool

VCS_EXCLUDES = (
    '-not -path "*/.git/*" '
    '-not -path "*/node_modules/*" '
    '-not -path "*/__pycache__/*" '
    '-not -path "*/.svn/*"'
)


class GlobTool(BuiltinTool):
    """Search file paths by glob pattern within the workspace.

    CC name: Glob (GlobTool)
    """

    name: ClassVar[str] = "Glob"
    description: ClassVar[str] = (
        "- Fast file pattern matching tool that works with any codebase size\n"
        "- Supports glob patterns like \"**/*.js\" or \"src/**/*.ts\"\n"
        "- Returns matching file paths sorted by modification time\n"
        "- Use this tool when you need to find files by name patterns\n"
        "- When you are doing an open ended search that may require multiple "
        "rounds of globbing and grepping, use the Agent tool instead"
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against",
            },
            "path": {
                "type": "string",
                "description": (
                    "The directory to search in. If not specified, the current "
                    "working directory will be used."
                ),
            },
        },
        "required": ["pattern"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="session", mode="shared_read"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.search.path"})
    effect_level: ClassVar[str] = "none"
    fast_path_eligible: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 8_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        pattern: str = arguments.get("pattern", "")
        path: str = arguments.get("path", "") or ""
        workdir = str(self._workdir) if self._workdir else "/workspace"
        safe_path = resolve_safe_path(path, workdir)

        command = (
            f"find {shell_escape(safe_path)} -type f "
            f"-name {shell_escape(pattern)} "
            f"{VCS_EXCLUDES} "
            f"2>/dev/null | head -200"
        )
        result = session.exec_bash(
            command=command,
            timeout=30,
            stop_event=self._stop_event_for_exec(),
        )

        output = result.get("output", "") or result.get("stdout", "")

        if not output.strip():
            return f"No files matching pattern '{pattern}' found in {safe_path}"

        return output
```

- [ ] **Step 4: Run tests and commit**

```bash
python -m pytest tests/matmaster/tools/builtin/test_glob_tool.py -v
git add matmaster/tools/builtin/glob_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_glob_tool.py
git commit -m "feat(tools): add GlobTool with shell_escape protection"
```

---

## Task 3: GrepTool

**Files:**
- Create: `matmaster/tools/builtin/grep_tool.py`
- Test: `tests/matmaster/tools/builtin/test_grep_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_grep_tool.py"""
import asyncio
from unittest.mock import MagicMock
from matmaster.tools.builtin.grep_tool import GrepTool


def make_session(output="", exit_code=0):
    s = MagicMock()
    s.exec_bash.return_value = {"output": output, "exit_code": exit_code}
    return s


class TestGrepToolMetadata:
    def test_name(self):
        assert GrepTool.name == "Grep"

    def test_schema_has_output_mode(self):
        assert "output_mode" in GrepTool.json_schema["properties"]

    def test_schema_has_context_flags(self):
        props = GrepTool.json_schema["properties"]
        assert "-A" in props
        assert "-B" in props
        assert "-C" in props


class TestGrepExecution:
    def test_no_matches(self):
        tool = GrepTool(session=make_session(output=""), workdir="/workspace")
        result = asyncio.run(tool.execute({"pattern": "notfound"}))
        assert "no matches" in result.lower()

    def test_files_with_matches_mode(self):
        tool = GrepTool(
            session=make_session(output="/workspace/a.py\n/workspace/b.py"),
            workdir="/workspace",
        )
        result = asyncio.run(tool.execute({
            "pattern": "import",
            "output_mode": "files_with_matches",
        }))
        assert "a.py" in result

    def test_content_mode(self):
        output = "/workspace/a.py:1:import os"
        tool = GrepTool(session=make_session(output=output), workdir="/workspace")
        result = asyncio.run(tool.execute({
            "pattern": "import",
            "output_mode": "content",
        }))
        assert "import os" in result

    def test_shell_escape_pattern(self):
        session = make_session(output="")
        tool = GrepTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "$(evil)"}))
        cmd = session.exec_bash.call_args[1].get("command") or session.exec_bash.call_args[0][0]
        assert "$(" not in cmd.split("'")[0]  # pattern should be escaped


class TestGrepRgDetection:
    def test_rg_detection_cached(self):
        session = make_session(output="")
        # First call detects rg
        rg_check = MagicMock()
        rg_check.return_value = {"output": "/usr/bin/rg", "exit_code": 0}
        session.exec_bash.side_effect = [
            {"output": "/usr/bin/rg", "exit_code": 0},  # which rg
            {"output": "", "exit_code": 1},               # actual grep
        ]
        tool = GrepTool(session=session, workdir="/workspace")
        asyncio.run(tool.execute({"pattern": "test"}))
        assert tool._use_rg is True
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement `grep_tool.py`**

```python
"""matmaster/tools/builtin/grep_tool.py

GrepTool — search file content by regex via session.
rg preferred, grep fallback (runtime detection).

CC Reference: tools/GrepTool/ (prompt.ts, GrepTool.ts)
CC name: Grep
"""

from __future__ import annotations

from typing import Any, ClassVar

from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

from ._path_safety import resolve_safe_path, shell_escape
from .base import BuiltinTool

DEFAULT_HEAD_LIMIT = 250
VCS_EXCLUDE_RG = ""  # rg auto-excludes .git
VCS_EXCLUDE_GREP = (
    "--exclude-dir=.git --exclude-dir=node_modules "
    "--exclude-dir=__pycache__ --exclude-dir=.svn"
)


class GrepTool(BuiltinTool):
    """Search file content by regex pattern within the workspace.

    CC name: Grep (GrepTool)
    """

    name: ClassVar[str] = "Grep"
    description: ClassVar[str] = (
        "A powerful search tool built on ripgrep\n\n"
        "  Usage:\n"
        "  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` "
        "as a Bash command.\n"
        "  - Supports full regex syntax (e.g., \"log.*Error\", \"function\\s+\\w+\")\n"
        "  - Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\")\n"
        "  - Output modes: \"content\" shows matching lines, "
        "\"files_with_matches\" shows only file paths (default), "
        "\"count\" shows match counts\n"
        "  - Pattern syntax: Uses ripgrep when available, falls back to grep"
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for in file contents",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Defaults to current working directory.",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g. \"*.js\", \"*.{ts,tsx}\")",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": (
                    "Output mode: \"content\" shows matching lines, "
                    "\"files_with_matches\" shows file paths (default), "
                    "\"count\" shows match counts."
                ),
            },
            "-A": {
                "type": "integer",
                "description": "Number of lines to show after each match. Requires output_mode: \"content\".",
            },
            "-B": {
                "type": "integer",
                "description": "Number of lines to show before each match. Requires output_mode: \"content\".",
            },
            "-C": {
                "type": "integer",
                "description": "Number of lines to show before and after each match.",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers in output. Defaults to true.",
            },
            "head_limit": {
                "type": "integer",
                "description": (
                    "Limit output to first N lines/entries. "
                    "Defaults to 250. Pass 0 for unlimited."
                ),
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N lines/entries before applying head_limit. Defaults to 0.",
            },
        },
        "required": ["pattern"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="session", mode="shared_read"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.search.content"})
    effect_level: ClassVar[str] = "none"
    fast_path_eligible: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 20_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._use_rg: bool | None = None

    def _detect_rg(self) -> bool:
        if self._use_rg is not None:
            return self._use_rg
        try:
            session = self._require_session()
            result = session.exec_bash(command="which rg 2>/dev/null", timeout=5)
            self._use_rg = result.get("exit_code", 1) == 0
        except Exception:
            self._use_rg = False
        return self._use_rg

    def _execute(self, arguments: dict[str, Any]) -> str:
        session = self._require_session()

        pattern: str = arguments.get("pattern", "")
        path: str = arguments.get("path", "") or ""
        file_glob: str = arguments.get("glob", "") or ""
        output_mode: str = arguments.get("output_mode", "files_with_matches")
        after: int | None = arguments.get("-A")
        before: int | None = arguments.get("-B")
        context: int | None = arguments.get("-C")
        case_insensitive: bool = arguments.get("-i", False)
        head_limit: int = arguments.get("head_limit", DEFAULT_HEAD_LIMIT)
        offset: int = arguments.get("offset", 0)

        workdir = str(self._workdir) if self._workdir else "/workspace"
        safe_path = resolve_safe_path(path, workdir)
        use_rg = self._detect_rg()

        if use_rg:
            cmd = self._build_rg_command(
                pattern, safe_path, file_glob, output_mode,
                after, before, context, case_insensitive,
            )
        else:
            cmd = self._build_grep_command(
                pattern, safe_path, file_glob, output_mode,
                after, before, context, case_insensitive,
            )

        # Pagination
        if offset > 0:
            cmd += f" | tail -n +{offset + 1}"
        effective_limit = head_limit if head_limit != 0 else None
        if effective_limit is None:
            effective_limit = DEFAULT_HEAD_LIMIT
        cmd += f" | head -{effective_limit}"

        result = session.exec_bash(
            command=cmd, timeout=30, stop_event=self._stop_event_for_exec(),
        )
        output = result.get("output", "") or result.get("stdout", "")

        if not output.strip():
            return f"No matches for pattern '{pattern}' in {safe_path}"
        return output

    def _build_rg_command(
        self, pattern, safe_path, file_glob, output_mode,
        after, before, context, case_insensitive,
    ) -> str:
        flags = []
        if output_mode == "files_with_matches":
            flags.append("--files-with-matches")
        elif output_mode == "count":
            flags.append("--count")
        else:
            flags.append("-n")  # content mode, line numbers
            if after:
                flags.append(f"-A {after}")
            if before:
                flags.append(f"-B {before}")
            if context:
                flags.append(f"-C {context}")
        if case_insensitive:
            flags.append("--ignore-case")
        if file_glob:
            flags.append(f"--glob {shell_escape(file_glob)}")

        flag_str = " ".join(flags)
        return f"rg {flag_str} {shell_escape(pattern)} {shell_escape(safe_path)} 2>/dev/null"

    def _build_grep_command(
        self, pattern, safe_path, file_glob, output_mode,
        after, before, context, case_insensitive,
    ) -> str:
        flags = ["-r"]
        if output_mode == "files_with_matches":
            flags.append("-l")
        elif output_mode == "count":
            flags.append("-c")
        else:
            flags.append("-n")
            if after:
                flags.append(f"-A {after}")
            if before:
                flags.append(f"-B {before}")
            if context:
                flags.append(f"-C {context}")
        if case_insensitive:
            flags.append("-i")
        if file_glob:
            flags.append(f"--include={shell_escape(file_glob)}")

        flag_str = " ".join(flags)
        return (
            f"grep {flag_str} {VCS_EXCLUDE_GREP} "
            f"{shell_escape(pattern)} {shell_escape(safe_path)} 2>/dev/null"
        )
```

- [ ] **Step 4: Run tests and commit**

```bash
python -m pytest tests/matmaster/tools/builtin/test_grep_tool.py -v
git add matmaster/tools/builtin/grep_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_grep_tool.py
git commit -m "feat(tools): add GrepTool with rg/grep dual-path and output modes"
```

---

## Final `__init__.py` additions after Plan 02

```python
from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.builtin.glob_tool import GlobTool
from matmaster.tools.builtin.grep_tool import GrepTool
# add to __all__
```
