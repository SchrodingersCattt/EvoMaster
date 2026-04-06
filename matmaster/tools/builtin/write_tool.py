"""matmaster/tools/builtin/write_tool.py

WriteTool — create/overwrite files via session.

CC Reference: tools/FileWriteTool/ (prompt.ts, FileWriteTool.ts)
CC name: Write
"""

from __future__ import annotations

import asyncio
import hashlib
import posixpath
from pathlib import PurePosixPath
from typing import Any, ClassVar

from matmaster.tools.filesystem_semantics.content_probe import probe_content_bytes
from matmaster.tools.filesystem_semantics.snapshots import (
    FileSemanticSnapshot,
    SnapshotFingerprint,
)
from matmaster.tools.filesystem_semantics.write_resolution import resolve_write_request
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
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
            "encoding": {
                "type": "string",
                "description": "Optional explicit text encoding for the write operation.",
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
            return ToolDecision(
                decision="deny", reason=f"invalid file_path: '{file_path}'"
            )

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

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> ToolResult:
        snapshot = None
        if exec_ctx is not None and exec_ctx.runner_state is not None:
            normalized = posixpath.normpath(arguments.get("file_path", ""))
            snapshot = exec_ctx.runner_state.get("file_semantics", {}).get(normalized)

        try:
            return await asyncio.to_thread(self._execute_internal, arguments, snapshot)
        except Exception as exc:
            self.logger.error("Tool %s failed: %s", self.name, exc, exc_info=True)
            return ToolResult(status="error", content=f"Error: {exc}")

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        result = self._execute_internal(arguments, None)
        if result.status == "success":
            return result.content
        return result

    def _execute_internal(
        self,
        arguments: dict[str, Any],
        existing_snapshot: FileSemanticSnapshot | None,
    ) -> ToolResult:
        session = self._require_session()
        file_path: str = arguments.get("file_path", "")
        content: str = arguments.get("content", "")
        explicit_encoding: str | None = arguments.get("encoding")

        file_exists = session.path_exists(file_path)
        current_fingerprint: SnapshotFingerprint | None = None
        current_probe = None
        if file_exists:
            raw = session.download(file_path)
            stat = session.stat_file(file_path)
            current_fingerprint = SnapshotFingerprint(
                size=stat.size,
                mtime=stat.mtime,
                prefix_hash=hashlib.sha256(raw[:4096]).hexdigest(),
            )
            if (
                existing_snapshot is None
                or existing_snapshot.fingerprint != current_fingerprint
            ):
                current_probe = probe_content_bytes(raw[:4096])

        decision = resolve_write_request(
            existing_snapshot=existing_snapshot,
            current_fingerprint=current_fingerprint,
            current_probe=current_probe,
            explicit_encoding=explicit_encoding,
            file_exists=file_exists,
        )
        if decision.status != "allow":
            return ToolResult(
                status="error",
                content=f"Error: unable to determine a safe encoding for {file_path}",
            )

        session.write_file(file_path, content, decision.encoding or "utf-8")
        return ToolResult(
            content=f"File written successfully to: {file_path}",
            meta={
                "encoding_used": decision.encoding,
                "encoding_source": decision.source,
            },
        )
