"""WorkspaceHandler -- debounced workspace snapshot and upload.

Key behavior:
- Only triggers on ToolResultEvent (tool execution may change files)
- Skips when ssh_attached (remote workspace)
- Debounces: skips if less than debounce_seconds since last check
- Snapshots workspace directory, skips if unchanged
- Delegates actual upload to injected upload_fn (no OSS dependency)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

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
        workspace_path: Path,
        upload_fn: Callable[..., Any] | None = None,
        snapshot_fn: (
            Callable[[Path], frozenset[tuple[str, float, int]] | None] | None
        ) = None,
        debounce_seconds: float = 2.0,
    ) -> None:
        self._session_id = session_id
        self._task_id = task_id
        self._ssh_attached = ssh_attached
        self._workspace_path = workspace_path
        self._upload_fn = upload_fn
        self._snapshot_fn = snapshot_fn or self._default_snapshot
        self._debounce_seconds = debounce_seconds
        self._last_check_time: float = 0.0
        self._last_snapshot: frozenset[tuple[str, float, int]] | None = None
        self._close_lock = threading.Lock()
        self._closed = False
        self._upload_executor = (
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="workspace-upload",
            )
            if upload_fn is not None
            else None
        )

    async def handle(self, event: BusEvent) -> None:
        """Process event -- only acts on ToolResultEvent."""
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

        snapshot = await asyncio.to_thread(self._get_snapshot)
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
        """Walk workspace directory, collect (relative_path, mtime, size)."""
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
        """Queue workspace upload on a dedicated worker."""
        with self._close_lock:
            if self._upload_fn is None or self._upload_executor is None or self._closed:
                return
            executor = self._upload_executor

        try:
            executor.submit(self._run_upload)
        except RuntimeError:
            # Executor was shut down between snapshot and submit — benign race.
            logger.debug(
                "WorkspaceHandler: skip upload after close session_id=%s",
                self._session_id,
            )

    def _run_upload(self) -> None:
        """Run the upload task and log failures without blocking the router."""
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

    def close(self) -> None:
        """Wait for any queued uploads, then release the worker."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            executor = self._upload_executor
            self._upload_executor = None

        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
