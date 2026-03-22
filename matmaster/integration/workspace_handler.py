"""WorkspaceHandler -- debounced workspace snapshot and upload.

Migrates the workspace snapshot/upload logic from the event_callback
closure in agent_run_service.py (lines 407-475) to a standalone
EventHandler implementation.

Key behavior:
- Only triggers on ToolResultEvent (tool execution may change files)
- Skips when ssh_attached (remote workspace)
- Debounces: skips if less than debounce_seconds since last check
- Snapshots workspace directory, skips if unchanged
- Delegates actual upload to injected upload_fn (no OSS dependency)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from matmaster.types.context import WorkspaceArchivalConfig
from matmaster.types.events import BusEvent, ToolResultEvent

logger = logging.getLogger(__name__)


class WorkspaceHandler:
    """Debounced workspace snapshot and upload handler.

    Implements EventHandler Protocol. Processes only ToolResultEvent,
    checks debounce timing, compares workspace snapshots, and triggers
    upload when files have changed.

    Upload function is injected for testability -- WorkspaceHandler
    does not depend on OSS directly.
    """

    def __init__(
        self,
        session_id: str,
        task_id: str,
        ssh_attached: bool,
        archival_config: WorkspaceArchivalConfig | None,
        workspace_path: Path,
        upload_fn: Callable[..., Any] | None = None,
        snapshot_fn: Callable[[Path], frozenset[tuple[str, float, int]] | None] | None = None,
        debounce_seconds: float = 2.0,
    ) -> None:
        self._session_id = session_id
        self._task_id = task_id
        self._ssh_attached = ssh_attached
        self._archival_config = archival_config
        self._workspace_path = workspace_path
        self._upload_fn = upload_fn
        self._snapshot_fn = snapshot_fn or self._default_snapshot
        self._debounce_seconds = debounce_seconds
        self._last_check_time: float = 0
        self._last_snapshot: frozenset[tuple[str, float, int]] | None = None

    def handle(self, event: BusEvent) -> None:  # type: ignore[arg-type]
        """Process event -- only acts on ToolResultEvent.

        Mirrors the tool_result branch of event_callback in
        agent_run_service.py (lines 407-475).
        """
        if not isinstance(event, ToolResultEvent):
            return

        if self._ssh_attached:
            logger.debug(
                "WorkspaceHandler: skip (SSH attached) session_id=%s",
                self._session_id,
            )
            return

        now = time.monotonic()
        if now - self._last_check_time < self._debounce_seconds:
            logger.debug(
                "WorkspaceHandler: skip (debounce) session_id=%s",
                self._session_id,
            )
            return

        self._last_check_time = now

        snapshot = self._get_snapshot()
        if snapshot == self._last_snapshot:
            logger.debug(
                "WorkspaceHandler: skip (snapshot unchanged) session_id=%s",
                self._session_id,
            )
            return

        self._last_snapshot = snapshot
        self._upload()

    def _get_snapshot(self) -> frozenset[tuple[str, float, int]] | None:
        """Get workspace directory snapshot via injected or default function."""
        return self._snapshot_fn(self._workspace_path)

    def _default_snapshot(
        self, workspace_path: Path
    ) -> frozenset[tuple[str, float, int]] | None:
        """Walk workspace directory, collect (relative_path, mtime, size).

        Migrated from _get_workspace_snapshot in agent_run_service.py
        (lines 247-264).
        """
        if not workspace_path.is_dir():
            return None

        out: set[tuple[str, float, int]] = set()
        try:
            for f in workspace_path.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    st = f.stat()
                    rel = str(f.relative_to(workspace_path)).replace("\\", "/")
                    out.add((rel, st.st_mtime, st.st_size))
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        return frozenset(out)

    def _upload(self) -> None:
        """Trigger workspace upload via injected function."""
        if self._upload_fn is None:
            return

        try:
            self._upload_fn(self._session_id, self._task_id, self._workspace_path)
        except Exception:
            logger.error(
                "WorkspaceHandler: upload failed session_id=%s",
                self._session_id,
                exc_info=True,
            )
