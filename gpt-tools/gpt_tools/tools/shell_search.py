"""Shell and search oriented GPT-style tools."""

from __future__ import annotations

import fnmatch
import json
import re
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import Any, ClassVar

from ..base import BaseTool
from ..models import ToolDefinition, ToolResult


TYPE_GLOBS: dict[str, tuple[str, ...]] = {
    "py": ("*.py",),
    "js": ("*.js", "*.jsx", "*.mjs", "*.cjs"),
    "ts": ("*.ts", "*.tsx"),
    "md": ("*.md",),
    "json": ("*.json",),
    "yaml": ("*.yaml", "*.yml"),
}

_BLOCKED_COMMAND_PATTERNS = (
    (re.compile(r"\brm\s+-rf\s+/"), "destructive root deletion"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "filesystem formatting"),
    (re.compile(r"\bdd\s+if=.*of=/dev/"), "raw disk write"),
    (re.compile(r">\s*/dev/sd"), "disk redirection"),
)


def _relative_or_absolute(target: Path, base: Path) -> str:
    try:
        return str(target.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(target.resolve())


def _match_filters(relative_path: str, *, glob: str | None, file_type: str | None) -> bool:
    patterns: list[str] = []
    if glob:
        patterns.append(glob)
    if file_type:
        patterns.extend(TYPE_GLOBS.get(file_type, ()))
    if not patterns:
        return True
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns)


class BashTool(BaseTool):
    """Execute shell commands with persisted working directory."""

    name: ClassVar[str] = "Bash"
    description: ClassVar[str] = (
        "Executes a given bash command and returns its output. "
        "The working directory persists between commands, but shell state does not. "
        "Supports timeout, background execution, and safety checks for dangerous commands."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to execute."},
            "timeout": {
                "type": "number",
                "description": "Optional timeout in milliseconds, up to 600000.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Run the command in the background.",
            },
            "description": {
                "type": "string",
                "description": "Short active-voice description of the command.",
            },
            "dangerouslyDisableSandbox": {
                "type": "boolean",
                "description": "Accepted for API compatibility; sandboxing is not implemented.",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        command = arguments["command"].strip()
        if not command:
            return ToolResult.error("Error: command cannot be empty.")

        dangerous, reason = self._check_dangerous(command)
        if dangerous:
            return ToolResult.error(f"Blocked: command was rejected due to {reason}.")

        cwd = self.context.current_working_directory
        next_cwd, runnable = self._extract_leading_cd(command, cwd)

        timeout_ms = min(int(arguments.get("timeout", 120_000) or 120_000), 600_000)
        run_in_background = bool(arguments.get("run_in_background", False))
        description = str(arguments.get("description", "") or "")

        if runnable is None:
            self.context.set_current_working_directory(next_cwd)
            return ToolResult.ok(
                f"Working directory changed to {next_cwd}",
                cwd=str(next_cwd),
            )

        if run_in_background:
            return self._run_background(runnable, next_cwd, description=description)

        return self._run_foreground(runnable, next_cwd, timeout_ms=timeout_ms)

    def _run_foreground(
        self,
        command: str,
        cwd: Path,
        *,
        timeout_ms: int,
    ) -> ToolResult:
        try:
            completed = subprocess.run(
                ["bash", "-lc", command],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_ms / 1000,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.error(
                f"Error: command timed out after {timeout_ms}ms.",
                cwd=str(cwd),
            )

        self.context.set_current_working_directory(cwd)
        output = (completed.stdout or "") + (completed.stderr or "")
        output = output.rstrip()
        if output:
            output += "\n"
        output += f"[Current working directory: {cwd}]"
        output += f"\n[Command finished with exit code {completed.returncode}]"
        if completed.returncode != 0:
            return ToolResult.error(output, exit_code=completed.returncode, cwd=str(cwd))
        return ToolResult.ok(output, exit_code=completed.returncode, cwd=str(cwd))

    def _run_background(
        self,
        command: str,
        cwd: Path,
        *,
        description: str,
    ) -> ToolResult:
        log_dir = self.context.workspace_root / ".gpt-tools" / "background"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{uuid.uuid4().hex[:12]}.log"
        handle = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            ["bash", "-lc", command],
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        handle.close()
        self.context.set_current_working_directory(cwd)
        record = self.context.register_background_command(
            command,
            cwd,
            log_path,
            process,
            description=description,
        )
        return ToolResult.ok(
            f"Background command started: {record.job_id}",
            job_id=record.job_id,
            pid=record.pid,
            log_path=str(record.log_path),
            cwd=str(cwd),
        )

    def _extract_leading_cd(
        self,
        command: str,
        current_cwd: Path,
    ) -> tuple[Path, str | None]:
        match = re.match(r"^\s*cd\s+(.+?)(?:\s*(?:&&|;)\s*(.*))?$", command)
        if match is None:
            return current_cwd, command

        raw_target = match.group(1).strip()
        remainder = match.group(2)
        try:
            target_tokens = shlex.split(f"cd {raw_target}")
        except ValueError as exc:
            raise ValueError(f"unable to parse cd command: {exc}") from exc

        if len(target_tokens) < 2:
            raise ValueError("cd command requires a target directory")

        target = Path(target_tokens[1])
        if not target.is_absolute():
            target = (current_cwd / target).resolve()
        else:
            target = target.resolve()
        if not target.is_dir():
            raise ValueError(f"target directory does not exist: {target}")
        if remainder is None or not remainder.strip():
            return target, None
        return target, remainder

    @staticmethod
    def _check_dangerous(command: str) -> tuple[bool, str]:
        for pattern, reason in _BLOCKED_COMMAND_PATTERNS:
            if pattern.search(command):
                return True, reason
        return False, ""


class GlobTool(BaseTool):
    """Find files by glob pattern."""

    name: ClassVar[str] = "Glob"
    description: ClassVar[str] = (
        "Fast file pattern matching tool. Supports recursive glob patterns and "
        "returns paths sorted by modification time."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern to match."},
            "path": {
                "type": "string",
                "description": "Directory to search in. Defaults to the current working directory.",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        pattern = arguments["pattern"]
        base = self.context.resolve_directory(arguments.get("path"))
        if not base.exists() or not base.is_dir():
            return ToolResult.error(f"Error: search path is not a directory: {base}")

        matches = [
            item
            for item in base.glob(pattern)
            if item.is_file()
        ]
        matches.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)

        capped = matches[:100]
        rendered = [
            _relative_or_absolute(item, self.context.current_working_directory)
            for item in capped
        ]
        content = "\n".join(rendered) if rendered else "No files matched the pattern."
        if len(matches) > len(capped):
            content += "\n\n[Results truncated to 100 matches.]"
        return ToolResult.ok(content, matches=rendered, truncated=len(matches) > 100)


class GrepTool(BaseTool):
    """Regex search implemented in pure Python for portability."""

    name: ClassVar[str] = "Grep"
    description: ClassVar[str] = (
        "Search file contents using regular expressions. Supports content, "
        "files_with_matches, and count modes, plus optional context lines."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for."},
            "path": {"type": "string", "description": "File or directory to search in."},
            "glob": {"type": "string", "description": "Optional file glob filter."},
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Search output mode.",
            },
            "-B": {"type": "integer", "description": "Context lines before each match."},
            "-A": {"type": "integer", "description": "Context lines after each match."},
            "-C": {"type": "integer", "description": "Context lines before and after each match."},
            "-n": {"type": "boolean", "description": "Include line numbers in content mode."},
            "-i": {"type": "boolean", "description": "Case-insensitive regex search."},
            "type": {"type": "string", "description": "Optional file type shortcut, such as py or ts."},
            "head_limit": {"type": "integer", "description": "Maximum number of results to return."},
            "offset": {"type": "integer", "description": "Skip this many result entries before returning."},
            "multiline": {"type": "boolean", "description": "Allow matches across line boundaries."},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        pattern = arguments["pattern"]
        root = self.context.resolve_directory(arguments.get("path"))
        if not root.exists():
            return ToolResult.error(f"Error: search path does not exist: {root}")

        output_mode = arguments.get("output_mode", "files_with_matches")
        head_limit = arguments.get("head_limit", 250)
        offset = max(int(arguments.get("offset", 0) or 0), 0)
        multiline = bool(arguments.get("multiline", False))
        line_numbers = bool(arguments.get("-n", False))
        before = int(arguments.get("-B", 0) or 0)
        after = int(arguments.get("-A", 0) or 0)
        around = int(arguments.get("-C", 0) or 0)
        if around:
            before = around
            after = around

        flags = re.IGNORECASE if arguments.get("-i") else 0
        if multiline:
            flags |= re.DOTALL | re.MULTILINE
        regex = re.compile(pattern, flags)

        candidate_files = self._list_candidate_files(
            root,
            glob=arguments.get("glob"),
            file_type=arguments.get("type"),
        )

        if output_mode == "files_with_matches":
            entries = self._search_files_with_matches(candidate_files, regex)
        elif output_mode == "count":
            entries = self._search_counts(candidate_files, regex)
        else:
            entries = self._search_content(
                candidate_files,
                regex,
                before=before,
                after=after,
                line_numbers=line_numbers,
                multiline=multiline,
            )

        sliced = entries[offset:]
        if head_limit is not None and head_limit >= 0:
            sliced = sliced[:head_limit]
        content = "\n".join(sliced) if sliced else "No matches found."
        if offset:
            content += f"\n\n[Skipped the first {offset} result(s).]"
        return ToolResult.ok(content, results=sliced, output_mode=output_mode)

    def _list_candidate_files(
        self,
        root: Path,
        *,
        glob: str | None,
        file_type: str | None,
    ) -> list[Path]:
        if root.is_file():
            return [root]

        files = [path for path in root.rglob("*") if path.is_file()]
        filtered: list[Path] = []
        for path in files:
            relative = _relative_or_absolute(path, root)
            if _match_filters(relative, glob=glob, file_type=file_type):
                filtered.append(path)
        return filtered

    def _search_files_with_matches(
        self,
        files: list[Path],
        regex: re.Pattern[str],
    ) -> list[str]:
        matched = []
        for path in files:
            if regex.search(path.read_text(encoding="utf-8", errors="replace")):
                matched.append(path)
        matched.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
        return [
            _relative_or_absolute(item, self.context.current_working_directory)
            for item in matched
        ]

    def _search_counts(
        self,
        files: list[Path],
        regex: re.Pattern[str],
    ) -> list[str]:
        entries: list[str] = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            count = len(regex.findall(text))
            if count:
                entries.append(
                    f"{_relative_or_absolute(path, self.context.current_working_directory)}:{count}"
                )
        return entries

    def _search_content(
        self,
        files: list[Path],
        regex: re.Pattern[str],
        *,
        before: int,
        after: int,
        line_numbers: bool,
        multiline: bool,
    ) -> list[str]:
        entries: list[str] = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = _relative_or_absolute(path, self.context.current_working_directory)
            if multiline:
                for match in regex.finditer(text):
                    snippet = match.group(0).strip()
                    entries.append(f"{relative}:{snippet}")
                continue

            lines = text.splitlines()
            for index, line in enumerate(lines):
                if not regex.search(line):
                    continue
                start = max(0, index - before)
                end = min(len(lines), index + after + 1)
                for pointer in range(start, end):
                    prefix = f"{relative}:"
                    if line_numbers:
                        prefix += f"{pointer + 1}:"
                    entries.append(f"{prefix}{lines[pointer]}")
        return entries


class ToolSearchTool(BaseTool):
    """Resolve deferred tool definitions by query."""

    name: ClassVar[str] = "ToolSearch"
    description: ClassVar[str] = (
        "Fetches schema definitions for deferred tools so they can be called. "
        "Supports exact select queries and keyword matching."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Tool search query or select:<tool1,tool2> list.",
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "description": "Maximum number of tools to return.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    search_hint: ClassVar[str] = "fetch deferred tool schemas"

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        registry = self.context.registry
        if registry is None:
            return ToolResult.error("Error: ToolSearch requires a registry on the ToolContext.")

        query = arguments["query"].strip()
        max_results = max(1, int(arguments.get("max_results", 5) or 5))
        available = {definition.name: definition for definition in registry.definitions()}
        deferred = {
            definition.name: definition for definition in registry.deferred_definitions()
        }

        if query.startswith("select:"):
            requested_names = [
                name.strip()
                for name in query.removeprefix("select:").split(",")
                if name.strip()
            ]
            selected = [
                available[name]
                for name in requested_names
                if name in available
            ]
        else:
            required_terms = [
                term[1:].lower()
                for term in query.split()
                if term.startswith("+") and len(term) > 1
            ]
            terms = [term.lower() for term in query.split() if not term.startswith("+")]
            scored: list[tuple[int, ToolDefinition]] = []
            for definition in deferred.values():
                haystack = (
                    f"{definition.name} {definition.description} {definition.search_hint}"
                ).lower()
                if required_terms and not all(term in definition.name.lower() for term in required_terms):
                    continue
                score = 0
                for term in terms:
                    if term in definition.name.lower():
                        score += 5
                    if term in definition.description.lower():
                        score += 2
                    if term in definition.search_hint.lower():
                        score += 1
                if score:
                    scored.append((score, definition))
            scored.sort(key=lambda item: (-item[0], item[1].name))
            selected = [definition for _, definition in scored[:max_results]]

        payload = {"tools": [definition.as_dict() for definition in selected]}
        if not selected and self.context.pending_mcp_servers:
            payload["pending_mcp_servers"] = list(self.context.pending_mcp_servers)

        if not selected:
            message = "No tools matched the query."
            if self.context.pending_mcp_servers:
                message += (
                    " Pending MCP servers: "
                    + ", ".join(self.context.pending_mcp_servers)
                )
            return ToolResult.ok(message, **payload)

        return ToolResult.ok(
            json.dumps(payload["tools"], ensure_ascii=False, indent=2),
            **payload,
        )
