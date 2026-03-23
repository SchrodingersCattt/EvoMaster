"""Tests for WorkspaceHandler.

Covers:
- Only processes ToolResultEvent, ignores other types
- Skips when ssh_attached is True
- Debounce: skips when less than debounce_seconds since last check
- Snapshot comparison: skips when snapshot unchanged
- Calls upload when snapshot has changed
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from matmaster.types.events import (
    FinishEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.integration.workspace_handler import WorkspaceHandler


class TestWorkspaceHandler:
    """WorkspaceHandler: debounced workspace snapshot/upload on ToolResultEvent."""

    def _make_handler(
        self,
        ssh_attached: bool = False,
        debounce_seconds: float = 0.0,
        upload_fn: Any = None,
        snapshot_fn: Any = None,
    ) -> WorkspaceHandler:
        return WorkspaceHandler(
            session_id="sess1",
            task_id="task1",
            ssh_attached=ssh_attached,
            archival_config=None,
            workspace_path=Path("/tmp/workspace"),
            upload_fn=upload_fn or MagicMock(),
            snapshot_fn=snapshot_fn,
            debounce_seconds=debounce_seconds,
        )

    def test_only_processes_tool_result(self) -> None:
        """handle() only processes ToolResultEvent, ignores other types."""
        upload_fn = MagicMock()
        handler = self._make_handler(upload_fn=upload_fn)

        # These should all be ignored
        handler.handle(FinishEvent(source="Agent", reason="done"))
        handler.handle(ThoughtEvent(source="Agent", content="thinking"))
        handler.handle(
            ToolCallEvent(
                source="Agent", call_id="c1", tool_name="bash", arguments={}
            )
        )

        upload_fn.assert_not_called()

    def test_skips_when_ssh_attached(self) -> None:
        """handle() skips when ssh_attached is True (remote workspace)."""
        upload_fn = MagicMock()
        handler = self._make_handler(ssh_attached=True, upload_fn=upload_fn)

        handler.handle(
            ToolResultEvent(
                source="Agent", call_id="c1", tool_name="bash", result="ok"
            )
        )

        upload_fn.assert_not_called()

    def test_debounce_skips_rapid_calls(self) -> None:
        """handle() skips when less than debounce_seconds since last check."""
        upload_fn = MagicMock()
        snapshot_fn = MagicMock(return_value=frozenset({("a.txt", 1.0, 100)}))
        handler = self._make_handler(
            debounce_seconds=10.0,
            upload_fn=upload_fn,
            snapshot_fn=snapshot_fn,
        )

        event = ToolResultEvent(
            source="Agent", call_id="c1", tool_name="bash", result="ok"
        )

        # First call should proceed (snapshot changes from None)
        handler.handle(event)
        assert upload_fn.call_count == 1

        # Second call within debounce window should be skipped
        handler.handle(event)
        assert upload_fn.call_count == 1  # still 1

    def test_skips_when_snapshot_unchanged(self) -> None:
        """handle() calls _get_snapshot() and skips when snapshot unchanged."""
        upload_fn = MagicMock()
        snapshot = frozenset({("a.txt", 1.0, 100)})
        snapshot_fn = MagicMock(return_value=snapshot)
        handler = self._make_handler(
            debounce_seconds=0.0,
            upload_fn=upload_fn,
            snapshot_fn=snapshot_fn,
        )

        event = ToolResultEvent(
            source="Agent", call_id="c1", tool_name="bash", result="ok"
        )

        # First call: snapshot changes from None -> snapshot
        handler.handle(event)
        assert upload_fn.call_count == 1

        # Second call: snapshot same
        handler.handle(event)
        assert upload_fn.call_count == 1  # unchanged, no upload

    def test_uploads_when_snapshot_changes(self) -> None:
        """handle() calls _upload() when snapshot has changed."""
        upload_fn = MagicMock()
        call_count = [0]
        snapshots = [
            frozenset({("a.txt", 1.0, 100)}),
            frozenset({("a.txt", 1.0, 100), ("b.txt", 2.0, 200)}),
        ]

        def varying_snapshot(workspace_path: Path) -> frozenset:
            idx = min(call_count[0], len(snapshots) - 1)
            call_count[0] += 1
            return snapshots[idx]

        handler = self._make_handler(
            debounce_seconds=0.0,
            upload_fn=upload_fn,
            snapshot_fn=varying_snapshot,
        )

        event = ToolResultEvent(
            source="Agent", call_id="c1", tool_name="bash", result="ok"
        )

        # First call: None -> snapshot[0], upload
        handler.handle(event)
        assert upload_fn.call_count == 1

        # Second call: snapshot[0] -> snapshot[1], different, upload again
        handler.handle(event)
        handler.close()
        assert upload_fn.call_count == 2

    def test_handle_returns_before_slow_upload_finishes(
        self, tmp_path: Path
    ) -> None:
        """handle() should not block the router thread on slow uploads."""
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir()

        started = threading.Event()
        release = threading.Event()

        def slow_upload(_session_id: str, _task_id: str, _workspace_path: Path) -> None:
            started.set()
            release.wait(timeout=5)

        handler = WorkspaceHandler(
            session_id="sess-1",
            task_id="task-1",
            ssh_attached=False,
            archival_config=None,
            workspace_path=workspace_path,
            upload_fn=slow_upload,
            snapshot_fn=MagicMock(return_value=frozenset({("a.txt", 1.0, 100)})),
            debounce_seconds=0.0,
        )
        event = ToolResultEvent(
            source="Agent", call_id="c1", tool_name="bash", result="ok"
        )
        caller = threading.Thread(target=handler.handle, args=(event,), daemon=True)
        caller.start()
        assert started.wait(timeout=1.0)

        try:
            caller.join(timeout=0.2)
            assert not caller.is_alive()
        finally:
            release.set()
            caller.join(timeout=2.0)
            close = getattr(handler, "close", None)
            if callable(close):
                close()

    def test_close_waits_for_inflight_upload(self, tmp_path: Path) -> None:
        """close() waits for background uploads to finish."""
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir()

        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow_upload(_session_id: str, _task_id: str, _workspace_path: Path) -> None:
            started.set()
            release.wait(timeout=5)
            finished.set()

        handler = WorkspaceHandler(
            session_id="sess-1",
            task_id="task-1",
            ssh_attached=False,
            archival_config=None,
            workspace_path=workspace_path,
            upload_fn=slow_upload,
            snapshot_fn=MagicMock(return_value=frozenset({("a.txt", 1.0, 100)})),
            debounce_seconds=0.0,
        )
        event = ToolResultEvent(
            source="Agent", call_id="c1", tool_name="bash", result="ok"
        )
        caller = threading.Thread(target=handler.handle, args=(event,), daemon=True)
        caller.start()
        assert started.wait(timeout=1.0)

        close = getattr(handler, "close", None)
        closer: threading.Thread | None = None
        try:
            assert callable(close)
            closer = threading.Thread(target=close, daemon=True)
            closer.start()
            closer.join(timeout=0.1)
            assert closer.is_alive()
        finally:
            release.set()
            caller.join(timeout=2.0)
            if closer is not None:
                closer.join(timeout=2.0)

        assert finished.is_set()
