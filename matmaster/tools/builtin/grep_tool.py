"""matmaster/tools/builtin/grep_tool.py

GrepTool — search file content by regex via session.
rg preferred, grep fallback (runtime detection).

CC Reference: tools/GrepTool/ (prompt.ts, GrepTool.ts)
CC name: Grep
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar

from matmaster.bohrium.runtime import get_runtime
from matmaster.tools.filesystem_semantics.snapshots import FileSemanticSnapshot
from matmaster.tools.filesystem_semantics.text_resolution import resolve_text_bytes
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

from ._path_safety import resolve_safe_path, shell_escape
from .base import BuiltinTool
from .glob_tool import GlobTool

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
        "Search file contents by regex, powered by ripgrep "
        "with glob / type / multiline filtering."
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
                "description": "Alias for context.",
            },
            "context": {
                "type": "integer",
                "description": "Number of lines to show before and after each match. Takes precedence over -C.",
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers in output. Defaults to true.",
            },
            "type": {
                "type": "string",
                "description": "File type to search (rg --type). E.g., \"py\", \"js\". Only works with rg.",
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode (rg -U --multiline-dotall). Only works with rg. Default: false.",
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
        ResourceClaim(resource="workspace", mode="shared_read"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"workspace.search.content"})
    effect_level: ClassVar[str] = "none"
    fast_path_eligible: ClassVar[bool] = True
    max_result_chars: ClassVar[int] = 20_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL

    def prompt(self, ctx=None) -> str:
        return (
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

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> ToolResult:
        snapshots: dict[str, FileSemanticSnapshot] = {}
        if exec_ctx is not None and exec_ctx.runner_state is not None:
            snapshots = dict(exec_ctx.runner_state.get("file_semantics", {}))

        try:
            return await asyncio.to_thread(self._execute_internal, arguments, snapshots)
        except Exception as exc:
            self.logger.error("Tool %s failed: %s", self.name, exc, exc_info=True)
            return ToolResult(status="error", content=f"Error: {exc}")

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        return self._execute_internal(arguments, {})

    def _execute_internal(
        self,
        arguments: dict[str, Any],
        snapshots: dict[str, FileSemanticSnapshot],
    ) -> ToolResult:
        session = self._require_session()

        pattern: str = arguments.get("pattern", "")
        path: str = arguments.get("path", "") or ""
        file_glob: str = arguments.get("glob", "") or ""
        output_mode: str = arguments.get("output_mode", "files_with_matches")
        after: int | None = arguments.get("-A")
        before: int | None = arguments.get("-B")
        # context takes precedence over -C (CC behavior)
        ctx_lines: int | None = arguments.get("context")
        if ctx_lines is None:
            ctx_lines = arguments.get("-C")
        case_insensitive: bool = arguments.get("-i", False)
        show_line_nums: bool = arguments.get("-n", True)
        file_type: str = arguments.get("type", "") or ""
        multiline: bool = arguments.get("multiline", False)
        head_limit: int | None = arguments.get("head_limit")
        offset: int = arguments.get("offset", 0)

        workdir = str(self._workdir) if self._workdir else "/workspace"
        safe_path = resolve_safe_path(
            path,
            workdir,
            allowed_roots=self._path_access_roots,
        )
        use_rg = self._detect_rg()

        if use_rg:
            cmd = self._build_rg_command(
                pattern,
                safe_path,
                file_glob,
                output_mode,
                after,
                before,
                ctx_lines,
                case_insensitive,
                show_line_nums,
                file_type,
                multiline,
            )
        else:
            cmd = self._build_grep_command(
                pattern,
                safe_path,
                file_glob,
                output_mode,
                after,
                before,
                ctx_lines,
                case_insensitive,
                show_line_nums,
            )

        # Pagination
        if offset > 0:
            cmd += f" | tail -n +{offset + 1}"
        # head_limit=0 means unlimited (CC behavior), None means use default
        if head_limit is None:
            cmd += f" | head -{DEFAULT_HEAD_LIMIT}"
        elif head_limit > 0:
            cmd += f" | head -{head_limit}"
        # head_limit == 0: no head pipe (unlimited)

        from matmaster.tools.script_env import inject_env

        runtime = get_runtime(session)
        env = runtime.build_env() if runtime is not None else {}
        cmd = inject_env(cmd, env, session)

        result = session.exec_bash(
            command=cmd,
            timeout=30,
            cancel_token=self._cancel_token_for_exec(),
        )
        output = result.get("output", "") or result.get("stdout", "")

        if self._needs_semantic_fallback(output, safe_path, snapshots):
            content = self._semantic_search(
                pattern,
                safe_path,
                file_glob,
                output_mode=output_mode,
                case_insensitive=case_insensitive,
            )
            if not content:
                content = f"No matches for pattern '{pattern}' in {safe_path}"
            return ToolResult(
                status="success",
                content=content,
                meta={"fallback_mode": "semantic"},
            )

        if not output.strip():
            return ToolResult(
                status="success",
                content=f"No matches for pattern '{pattern}' in {safe_path}",
                meta={"fallback_mode": "backend"},
            )
        return ToolResult(
            status="success",
            content=output,
            meta={"fallback_mode": "backend"},
        )

    def _needs_semantic_fallback(
        self,
        output: str,
        safe_path: str,
        snapshots: dict[str, FileSemanticSnapshot],
    ) -> bool:
        lowered = output.lower()
        if "binary file matches" in lowered:
            return True
        return any(
            snapshot.kind != "definite_text"
            for path, snapshot in snapshots.items()
            if path.startswith(safe_path)
        )

    def _list_candidate_files(self, safe_path: str, file_glob: str) -> list[str]:
        session = self._require_session()
        if session.is_file(safe_path):
            return [safe_path]

        from matmaster.tools.script_env import inject_env

        find_cmd = GlobTool._build_find_command(file_glob or "**", safe_path)
        runtime = get_runtime(session)
        env = runtime.build_env() if runtime is not None else {}
        result = session.exec_bash(
            command=inject_env(find_cmd, env, session),
            timeout=30,
            cancel_token=self._cancel_token_for_exec(),
        )
        output = result.get("output", "") or result.get("stdout", "")
        return [line for line in output.splitlines() if line.strip()]

    def _semantic_search(
        self,
        pattern: str,
        safe_path: str,
        file_glob: str,
        *,
        output_mode: str,
        case_insensitive: bool,
    ) -> str:
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)
        paths = self._list_candidate_files(safe_path, file_glob)
        matches: list[str] = []
        for path in paths:
            raw = self._require_session().download(path)
            resolution = resolve_text_bytes(raw, explicit_encoding=None)
            if resolution.status != "success" or resolution.text is None:
                continue

            match_count = 0
            for lineno, line in enumerate(resolution.text.splitlines(), start=1):
                if regex.search(line):
                    match_count += 1
                    if output_mode == "content":
                        matches.append(f"{path}:{lineno}:{line}")
            if output_mode == "files_with_matches" and match_count > 0:
                matches.append(path)
            elif output_mode == "count" and match_count > 0:
                matches.append(f"{path}:{match_count}")
        return "\n".join(matches)

    def _build_rg_command(
        self,
        pattern,
        safe_path,
        file_glob,
        output_mode,
        after,
        before,
        context,
        case_insensitive,
        show_line_nums,
        file_type,
        multiline,
    ) -> str:
        flags = ["--max-columns", "500"]  # prevent base64/minified pollution
        if output_mode == "files_with_matches":
            flags.append("--files-with-matches")
        elif output_mode == "count":
            flags.append("--count")
        else:
            if show_line_nums:
                flags.append("-n")
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
        if file_type:
            flags.append(f"--type {shell_escape(file_type)}")
        if multiline:
            flags.extend(["-U", "--multiline-dotall"])

        flag_str = " ".join(flags)
        return f"rg {flag_str} {shell_escape(pattern)} {shell_escape(safe_path)} 2>/dev/null"

    def _build_grep_command(
        self,
        pattern,
        safe_path,
        file_glob,
        output_mode,
        after,
        before,
        context,
        case_insensitive,
        show_line_nums,
    ) -> str:
        flags = ["-r"]
        if output_mode == "files_with_matches":
            flags.append("-l")
        elif output_mode == "count":
            flags.append("-c")
        else:
            if show_line_nums:
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
