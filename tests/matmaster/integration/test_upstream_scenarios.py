"""Upstream scenario tests: run interruption, workspace upload, Bohrium lifecycle,
event handler filtering, cross-pod reply queue.

All external dependencies mocked per D-10.

Phase 36 Plan 02: ConfirmationHook runtime plumbing removed. Confirmation
recovery tests deleted. Reply-queue poll tests retained (dormant plumbing
for future v2.3 bidirectional stream).
"""

from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.config.exp import ExpConfig
from matmaster.core.agent import AgentKernel
from matmaster.core.exp import Exp
from matmaster.core.hooks import BaseHook, HookAction
from matmaster.integration.persistence_handler import PersistenceHandler
from matmaster.integration.sse_handler import SSEHandler
from matmaster.integration.workspace_handler import WorkspaceHandler
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import (
    AssistantStateEvent,
    FinishEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
    ToolCallData,
)
from matmaster.types.runtime import AgentRuntime, AgentRuntimeSpec, KernelResult

_src_services = pytest.importorskip(
    "src.services.agent_run_bohrium",
    reason="src not available (isolation test)",
)
BohriumSetupResult = _src_services.BohriumSetupResult
BohriumSetupService = _src_services.BohriumSetupService
SkillSyncSpec = _src_services.SkillSyncSpec

# -- Mock helpers ------------------------------------------------


class _QuickMockLLM:
    """Simplest mock LLM: single turn, immediate finish."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="quick", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="quick", finish_reason="stop")


def _make_ctx(tmp_path: Path, llm_provider: Any = None) -> PlaygroundContext:
    return PlaygroundContext(
        workdir=tmp_path / "workspace",
        session_type="local",
        cache_area=tmp_path / "cache",
        run_meta={"run_dir": str(tmp_path), "task_id": "test"},
        llm_provider=llm_provider,
    )


# -- QUAL-04: Run interrupted detection -------------------------


class TestRunInterruptedDetection:
    """Verify stop_event.is_set() detected during kernel loop."""

    _EXP_CONFIG: ExpConfig = ExpConfig(name="direct")

    async def test_run_interrupted_detection_deploy(self, tmp_path: Path) -> None:
        """Verify stop_event.is_set() detected before kernel turn.
        Uses pre-set stop_event to guarantee detection on first check.
        """
        mock_llm = _QuickMockLLM()
        pg_ctx = _make_ctx(tmp_path, llm_provider=mock_llm)

        exp = Exp(self._EXP_CONFIG)
        runtime = await exp.build_runtime(pg_ctx)

        # Pre-set stop_event: kernel checks before first LLM call
        stop_event = threading.Event()
        stop_event.set()

        kernel = AgentKernel()
        finish = await kernel.run(runtime.spec, "long task", stop_event=stop_event)

        assert isinstance(finish.result, KernelResult)
        assert finish.result.reason == "cancelled"
        assert finish.result.status == "cancelled"

    async def test_run_interrupted_detection_restart(self, tmp_path: Path) -> None:
        """Verify stop_event from Redis stop key detected (same mechanism)."""
        mock_llm = _QuickMockLLM()
        pg_ctx = _make_ctx(tmp_path, llm_provider=mock_llm)

        exp = Exp(self._EXP_CONFIG)
        runtime = await exp.build_runtime(pg_ctx)

        # Simulate Redis-backed stop event: already set before run starts
        stop_event = threading.Event()
        stop_event.set()

        kernel = AgentKernel()
        finish = await kernel.run(runtime.spec, "restart task", stop_event=stop_event)

        assert finish.result.reason == "cancelled"


# -- QUAL-04: Workspace upload scenarios -------------------------


class TestWorkspaceUpload:
    """Verify WorkspaceHandler upload behavior."""

    async def test_workspace_upload_triggered_on_tool_result(
        self, tmp_path: Path
    ) -> None:
        """Verify WorkspaceHandler triggers upload when workspace files change."""
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir()
        (workspace_path / "file.txt").write_text("content")

        upload_fn = MagicMock()
        snapshot_fn = MagicMock(
            side_effect=[
                frozenset({("file.txt", 1.0, 7)}),
                frozenset({("file.txt", 1.0, 7), ("new.txt", 2.0, 5)}),
            ]
        )

        handler = WorkspaceHandler(
            session_id="sess-1",
            task_id="task-1",
            ssh_attached=False,
            archival_config=None,
            workspace_path=workspace_path,
            upload_fn=upload_fn,
            snapshot_fn=snapshot_fn,
            debounce_seconds=0,
        )

        # First event: sets initial snapshot
        await handler.handle(
            ToolResultEvent(source="agent", call_id="c1", tool_name="bash", result="ok")
        )
        # Second event: snapshot changed -> upload triggered
        await handler.handle(
            ToolResultEvent(source="agent", call_id="c2", tool_name="bash", result="ok")
        )

        assert upload_fn.called

    async def test_workspace_upload_skipped_when_ssh_attached(
        self, tmp_path: Path
    ) -> None:
        """Verify WorkspaceHandler skips upload in SSH mode."""
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir()

        upload_fn = MagicMock()

        handler = WorkspaceHandler(
            session_id="sess-1",
            task_id="task-1",
            ssh_attached=True,
            archival_config=None,
            workspace_path=workspace_path,
            upload_fn=upload_fn,
            debounce_seconds=0,
        )

        await handler.handle(
            ToolResultEvent(source="agent", call_id="c1", tool_name="bash", result="ok")
        )

        assert not upload_fn.called


# -- QUAL-04: Bohrium lifecycle ----------------------------------


class TestBohriumSetupLifecycle:
    """Verify BohriumSetupService delegates correctly."""

    @pytest.mark.asyncio
    async def test_bohrium_setup_lifecycle(self) -> None:
        """Verify run_setup/run_cleanup delegate to the owned runtime methods."""
        mock_sessions = MagicMock()
        svc = BohriumSetupService(
            sessions_service=mock_sessions,
            event_sink=MagicMock(),
        )

        skill_sync_spec = SkillSyncSpec(
            project_skill_roots=["/proj/skills"],
            remote_project_root="/remote/project",
        )
        expected = BohriumSetupResult(
            ssh_attached=False,
            abort_result=None,
            execution_session=None,
            execution_workdir="/remote/exec/wd",
            session_type=None,
        )

        with (
            patch.object(
                svc,
                "_load_run_credentials",
                return_value=({"key": "val"}, "user-1", "org-1"),
            ) as mock_load,
            patch.object(
                svc,
                "_setup_bohrium_for_run",
                return_value=expected,
            ) as mock_setup,
            patch.object(svc, "_make_event_bridge", return_value=MagicMock()),
        ):
            result = await svc.run_setup(
                session_id="sess-1",
                playground=MagicMock(),
                skill_sync_spec=skill_sync_spec,
                run_started_at=0.0,
            )

        mock_load.assert_called_once_with("sess-1")
        call_kw = mock_setup.call_args.kwargs
        assert call_kw["skill_sync_spec"] is skill_sync_spec
        assert call_kw["run_creds"] == {"key": "val"}
        assert result.ssh_attached is False
        assert result.execution_workdir == "/remote/exec/wd"

        with (
            patch.object(svc, "_cleanup_bohrium_after_run") as mock_cleanup,
            patch.object(svc, "_make_event_bridge", return_value=MagicMock()),
        ):
            await svc.run_cleanup(
                session_id="sess-1",
                pg_for_run=MagicMock(),
                ssh_attached=False,
            )

        assert mock_cleanup.called


# -- QUAL-04: Event handler persistence ---------------------------


class TestEventHandlerPersistence:
    """Verify PersistenceHandler filtering and persistence behavior."""

    async def test_event_persistence_on_standard_events(self) -> None:
        """Verify PersistenceHandler persists tool_call, tool_result, finish events."""
        mock_events_table = MagicMock()

        handler = PersistenceHandler(
            events_table=mock_events_table,
            session_id="sess-1",
            task_id="task-1",
        )

        # These should be persisted (non-streaming)
        await handler.handle(
            ToolCallEvent(
                source="agent", call_id="c1", tool_name="bash", arguments={"cmd": "ls"}
            )
        )
        await handler.handle(
            ToolResultEvent(
                source="agent", call_id="c1", tool_name="bash", result="output"
            )
        )
        await handler.handle(FinishEvent(source="agent", reason="natural"))

        assert mock_events_table.add_event.call_count == 3

    async def test_sse_push_filtering(self) -> None:
        """Verify SSEHandler pushes events except assistant_state and mode-filtered thoughts."""
        payloads = []

        async def mock_send_cb(payload):
            payloads.append(payload)

        handler = SSEHandler(
            send_cb=mock_send_cb,
            session_id="sess-1",
            task_id="task-1",
            invocation_id=None,
            mode="direct",
        )

        # assistant_state: NEVER pushed
        await handler.handle(
            AssistantStateEvent(source="agent", state={"content": "hi"})
        )
        # streaming thought: pushed in direct mode
        await handler.handle(
            ThoughtEvent(source="agent", content="hello", stream_state="start")
        )
        # non-streaming thought: NOT pushed in direct mode
        await handler.handle(
            ThoughtEvent(source="agent", content="complete thought", stream_state=None)
        )
        # tool_call: pushed
        await handler.handle(
            ToolCallEvent(source="agent", call_id="c1", tool_name="bash", arguments={})
        )

        event_types = [p.get("type") for p in payloads]
        assert "assistant_state" not in event_types
        assert "thought" in event_types  # streaming thought pushed
        assert "tool_call" in event_types
        # Non-streaming thought in direct mode is filtered
        thought_payloads = [p for p in payloads if p.get("type") == "thought"]
        assert len(thought_payloads) == 1  # only streaming one


# -- Reply queue dormant plumbing (retained for v2.3) ----------------


class _RedisCompatibleReplyQueue:
    """Reply queue fake that enforces current Redis integer-timeout semantics."""

    def __init__(self) -> None:
        self._q: queue.Queue[str | None] = queue.Queue()
        self.requested_timeouts: list[int] = []

    def put_content(self, content: str) -> None:
        self._q.put(content)

    def put_cancel(self) -> None:
        self._q.put(None)

    def get(self, timeout: float | None = None) -> str | None:
        if timeout is None or timeout < 1 or int(timeout) != timeout:
            raise AssertionError(
                "Redis-compatible bridge must poll with integer-second timeout"
            )
        sec = int(timeout)
        self.requested_timeouts.append(sec)
        try:
            return self._q.get(timeout=sec)
        except queue.Empty as exc:
            raise queue.Empty("timeout") from exc


class TestReplyQueueDormantPlumbing:
    """_poll_reply_queue tests -- dormant plumbing for future v2.3 bidirectional stream."""

    @pytest.mark.asyncio
    async def test_poll_reply_queue_uses_integer_second_timeout(self) -> None:
        _poll_reply_queue = pytest.importorskip(
            "src.services.agent_run_service",
            reason="src not available (isolation test)",
        )._poll_reply_queue

        reply_queue = _RedisCompatibleReplyQueue()
        reply_queue.put_content("approved")

        result = await _poll_reply_queue(reply_queue)

        assert result == "approved"
        assert 1 in reply_queue.requested_timeouts

    @pytest.mark.asyncio
    async def test_poll_reply_queue_cancel_returns_none(self) -> None:
        _poll_reply_queue = pytest.importorskip(
            "src.services.agent_run_service",
            reason="src not available (isolation test)",
        )._poll_reply_queue

        reply_queue = _RedisCompatibleReplyQueue()
        reply_queue.put_cancel()

        result = await _poll_reply_queue(reply_queue)

        assert result is None

    @pytest.mark.asyncio
    async def test_poll_reply_queue_retries_on_empty(self) -> None:
        _poll_reply_queue = pytest.importorskip(
            "src.services.agent_run_service",
            reason="src not available (isolation test)",
        )._poll_reply_queue

        reply_queue = _RedisCompatibleReplyQueue()
        threading.Timer(0.05, reply_queue.put_content, args=("delayed",)).start()

        result = await asyncio.wait_for(_poll_reply_queue(reply_queue), timeout=3.0)

        assert result == "delayed"
        assert len(reply_queue.requested_timeouts) >= 1

    @pytest.mark.asyncio
    async def test_poll_reply_queue_timeout_via_wait_for(self) -> None:
        _poll_reply_queue = pytest.importorskip(
            "src.services.agent_run_service",
            reason="src not available (isolation test)",
        )._poll_reply_queue

        reply_queue = _RedisCompatibleReplyQueue()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_poll_reply_queue(reply_queue), timeout=0.1)


# -- Negative assertion: ConfirmationHook no longer importable -------


def test_confirmation_hook_not_in_hooks_package():
    """ConfirmationHook runtime path must be fully removed (D-03/D-04)."""
    import matmaster.hooks

    assert not hasattr(matmaster.hooks, "ConfirmationHook"), \
        "ConfirmationHook should not be exported from matmaster.hooks"


def test_confirmation_hook_module_absent():
    """matmaster/hooks/confirmation.py must be physically deleted."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("matmaster.hooks.confirmation")
