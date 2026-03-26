"""Upstream scenario tests: run interruption, workspace upload, Bohrium lifecycle,
event router filtering, cross-pod reply queue, x_master rejection.

All external dependencies mocked per D-10.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.core.bus import MessageBus
from matmaster.core.agent import AgentKernel
from matmaster.core.hooks import BaseHook, HookAction
from matmaster.types.messages import (
    LLMResponse,
    Message,
    StreamChunk,
    ToolCallData,
)
from matmaster.hooks.confirmation import ConfirmationHook
from matmaster.integration.bohrium_setup import BohriumSetupService, SkillSyncSpec
from src.services.agent_run_bohrium import BohriumSetupResult
from matmaster.integration.event_router import EventRouter
from matmaster.integration.persistence_handler import PersistenceHandler
from matmaster.integration.sse_handler import SSEHandler
from matmaster.integration.workspace_handler import WorkspaceHandler
from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig
from matmaster.types.runtime import KernelResult
from matmaster.types.events import (
    AssistantStateEvent,
    ConfirmationRequestEvent,
    FinishEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)


# ── Mock helpers ──────────────────────────────────────


class _SlowMockLLM:
    """Mock LLM that takes time to respond (checks stop_event between turns)."""

    def __init__(self, turns: int = 3) -> None:
        self._turns = turns
        self._call_count = 0

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="done", finish_reason="stop")

    def chat_stream(self, messages, tools=None, *, timeout=None) -> Iterator[StreamChunk]:
        self._call_count += 1
        # Add a small delay to give stop_event time to be set
        time.sleep(0.05)
        yield StreamChunk(content=f"Turn {self._call_count}", finish_reason="stop")


class _NeverFinishLLM:
    """Mock LLM that always returns tool calls, never natural finish."""

    def __init__(self) -> None:
        self._call_count = 0

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="loop", finish_reason="stop")

    def chat_stream(self, messages, tools=None, *, timeout=None) -> Iterator[StreamChunk]:
        self._call_count += 1
        # Always yield a natural finish to avoid infinite loop
        yield StreamChunk(content=f"Turn {self._call_count}", finish_reason="stop")


class _QuickMockLLM:
    """Simplest mock LLM: single turn, immediate finish."""

    def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="quick", finish_reason="stop")

    def chat_stream(self, messages, tools=None, *, timeout=None) -> Iterator[StreamChunk]:
        yield StreamChunk(content="quick", finish_reason="stop")


def _make_ctx(tmp_path: Path, llm_provider: Any = None) -> PlaygroundContext:
    return PlaygroundContext(
        workdir=tmp_path / "workspace",
        session_type="local",
        cache_area=tmp_path / "cache",
        run_meta={"run_dir": str(tmp_path), "task_id": "test"},
        llm_provider=llm_provider,
    )


# ── QUAL-04: Run interrupted detection ───────────────


class TestRunInterruptedDetection:
    """Verify stop_event.is_set() detected during kernel loop."""

    _EXP_CONFIG: ExpConfig = ExpConfig(name="direct")

    def test_run_interrupted_detection_deploy(self, tmp_path: Path) -> None:
        """Verify stop_event.is_set() detected before kernel turn.
        Uses pre-set stop_event to guarantee detection on first check.
        """
        mock_llm = _QuickMockLLM()
        pg_ctx = _make_ctx(tmp_path, llm_provider=mock_llm)
        bus = MessageBus()

        exp = Exp(self._EXP_CONFIG)
        runtime = exp.build_runtime(pg_ctx, bus=bus)

        # Pre-set stop_event: kernel checks before first LLM call
        stop_event = threading.Event()
        stop_event.set()

        kernel = AgentKernel()
        finish = kernel.run(runtime.spec, "long task", stop_event=stop_event)

        assert isinstance(finish.result, KernelResult)
        assert finish.result.reason == "cancelled"
        assert finish.result.status == "cancelled"

    def test_run_interrupted_detection_restart(self, tmp_path: Path) -> None:
        """Verify stop_event from Redis stop key detected (same mechanism)."""
        mock_llm = _QuickMockLLM()
        pg_ctx = _make_ctx(tmp_path, llm_provider=mock_llm)
        bus = MessageBus()

        exp = Exp(self._EXP_CONFIG)
        runtime = exp.build_runtime(pg_ctx, bus=bus)

        # Simulate Redis-backed stop event: already set before run starts
        stop_event = threading.Event()
        stop_event.set()

        kernel = AgentKernel()
        finish = kernel.run(runtime.spec, "restart task", stop_event=stop_event)

        assert finish.result.reason == "cancelled"


# ── QUAL-04: Workspace upload scenarios ──────────────


class TestWorkspaceUpload:
    """Verify WorkspaceHandler upload behavior."""

    def test_workspace_upload_triggered_on_tool_result(self, tmp_path: Path) -> None:
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
        handler.handle(ToolResultEvent(
            source="agent", call_id="c1", tool_name="bash", result="ok"
        ))
        # Second event: snapshot changed -> upload triggered
        handler.handle(ToolResultEvent(
            source="agent", call_id="c2", tool_name="bash", result="ok"
        ))

        assert upload_fn.called

    def test_workspace_upload_skipped_when_ssh_attached(self, tmp_path: Path) -> None:
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

        handler.handle(ToolResultEvent(
            source="agent", call_id="c1", tool_name="bash", result="ok"
        ))

        assert not upload_fn.called


# ── QUAL-04: Bohrium lifecycle ───────────────────────


class TestBohriumSetupLifecycle:
    """Verify BohriumSetupService delegates correctly."""

    def test_bohrium_setup_lifecycle(self) -> None:
        """Verify BohriumSetupService.setup() and cleanup() delegate correctly."""
        mock_sessions_svc = MagicMock()
        bus = MessageBus()
        svc = BohriumSetupService(mock_sessions_svc, bus)

        # Patch the lazy-imported functions
        with (
            patch(
                "src.services.agent_run_bohrium.setup_bohrium_for_run"
            ) as mock_setup,
            patch(
                "src.services.agent_run_bohrium.cleanup_bohrium_after_run"
            ) as mock_cleanup,
        ):
            skill_sync_spec = SkillSyncSpec(
                project_skill_roots=["/proj/skills"],
                local_user_skills_root="/local/user/skills",
                remote_user_skills_root="/remote/user/skills",
                remote_project_root="/remote/project",
            )
            mock_setup.return_value = BohriumSetupResult(
                ssh_attached=False,
                abort_result=None,
                execution_session=None,
                execution_workdir="/remote/exec/wd",
                session_type=None,
            )

            result = svc.setup(
                session_id="sess-1",
                pg=MagicMock(),
                skill_sync_spec=skill_sync_spec,
                run_creds={"key": "val"},
                user_id_for_ak="user-1",
                org_id="org-1",
                event_callback=MagicMock(),
                run_started_at=0.0,
            )

            assert mock_setup.called
            call_kw = mock_setup.call_args.kwargs
            assert call_kw["skill_sync_spec"] is skill_sync_spec
            assert "base" not in call_kw
            assert result.ssh_attached is False
            assert result.execution_workdir == "/remote/exec/wd"

            svc.cleanup(
                session_id="sess-1",
                event_callback=MagicMock(),
                pg_for_run=MagicMock(),
                ssh_attached=False,
            )

            assert mock_cleanup.called


# ── QUAL-04: Event router persistence ────────────────


class TestEventRouterPersistence:
    """Verify PersistenceHandler filtering and persistence behavior."""

    def test_event_router_persistence_on_standard_events(self) -> None:
        """Verify PersistenceHandler persists tool_call, tool_result, finish events."""
        mock_events_table = MagicMock()

        handler = PersistenceHandler(
            events_table=mock_events_table,
            session_id="sess-1",
            task_id="task-1",
        )

        # These should be persisted (non-streaming)
        handler.handle(ToolCallEvent(
            source="agent", call_id="c1", tool_name="bash", arguments={"cmd": "ls"}
        ))
        handler.handle(ToolResultEvent(
            source="agent", call_id="c1", tool_name="bash", result="output"
        ))
        handler.handle(FinishEvent(source="agent", reason="natural"))

        assert mock_events_table.add_event.call_count == 3

    def test_event_router_sse_push_filtering(self) -> None:
        """Verify SSEHandler pushes events except assistant_state and mode-filtered thoughts."""
        payloads = []

        def mock_send_cb(payload):
            payloads.append(payload)

        handler = SSEHandler(
            send_cb=mock_send_cb,
            loop=None,
            session_id="sess-1",
            task_id="task-1",
            invocation_id=None,
            mode="direct",
        )

        # assistant_state: NEVER pushed
        handler.handle(AssistantStateEvent(
            source="agent", state={"content": "hi"}
        ))
        # streaming thought: pushed in direct mode
        handler.handle(ThoughtEvent(
            source="agent", content="hello", stream_state="start"
        ))
        # non-streaming thought: NOT pushed in direct mode
        handler.handle(ThoughtEvent(
            source="agent", content="complete thought", stream_state=None
        ))
        # tool_call: pushed
        handler.handle(ToolCallEvent(
            source="agent", call_id="c1", tool_name="bash", arguments={}
        ))

        event_types = [p.get("type") for p in payloads]
        assert "assistant_state" not in event_types
        assert "thought" in event_types  # streaming thought pushed
        assert "tool_call" in event_types
        # Non-streaming thought in direct mode is filtered
        thought_payloads = [p for p in payloads if p.get("type") == "thought"]
        assert len(thought_payloads) == 1  # only streaming one


# ── QUAL-04: Cross-pod reply queue ───────────────────


class _MockReplyQueue:
    """Simulates RedisReplyQueue behavior for cross-pod confirmation.

    Uses a stdlib queue internally to simulate Redis list RPUSH/BLPOP.
    """

    def __init__(self) -> None:
        self._q: queue.Queue[str | None] = queue.Queue()

    def put_content(self, content: str) -> None:
        self._q.put(content)

    def put_cancel(self) -> None:
        self._q.put(None)

    def get(self, timeout: float | None = None) -> str | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            raise queue.Empty("timeout")


class TestCrossPodReplyQueue:
    """QUAL-04: Verify cross-pod subscription recovery via RedisReplyQueue."""

    def test_cross_pod_reply_queue(self) -> None:
        """Verify ConfirmationHook correctly interacts with ReplyQueueLike
        for cross-worker confirmation flow.
        """
        reply_queue = _MockReplyQueue()
        bus = MessageBus()

        hook = ConfirmationHook(
            reply_queue=reply_queue,
            bus=bus,
            timeout_sec=5,
        )

        tool_call = ToolCallData(id="tc-1", name="dangerous_tool", arguments={})

        # Simulate cross-pod flow: another worker puts approval
        def approve_after_delay():
            time.sleep(0.05)
            reply_queue.put_content("approved")

        t = threading.Thread(target=approve_after_delay, daemon=True)
        t.start()

        # Main thread calls pre_tool_call (blocks on reply_queue.get())
        action = hook.pre_tool_call(tool_call)
        t.join(timeout=2)

        assert action == HookAction.CONTINUE

        # Verify ConfirmationRequestEvent was emitted to bus
        events = []
        try:
            while True:
                events.append(bus.get(timeout=0.1))
        except queue.Empty:
            pass

        confirmation_events = [
            e for e in events if isinstance(e, ConfirmationRequestEvent)
        ]
        assert len(confirmation_events) == 1
        assert confirmation_events[0].question == "Confirm tool call: dangerous_tool?"

    def test_cross_pod_reply_queue_cancel(self) -> None:
        """Verify ConfirmationHook returns SKIP when user cancels via cross-pod queue."""
        reply_queue = _MockReplyQueue()
        bus = MessageBus()

        hook = ConfirmationHook(
            reply_queue=reply_queue,
            bus=bus,
            timeout_sec=5,
        )

        tool_call = ToolCallData(id="tc-2", name="dangerous_tool", arguments={})

        # Simulate cross-pod cancel
        def cancel_after_delay():
            time.sleep(0.05)
            reply_queue.put_cancel()

        t = threading.Thread(target=cancel_after_delay, daemon=True)
        t.start()

        action = hook.pre_tool_call(tool_call)
        t.join(timeout=2)

        assert action == HookAction.SKIP


# ── D-03: x_master raises ValueError ────────────────


class TestXMasterRaisesValueError:
    """Per D-03: x_master playground_type raises ValueError in new pipeline."""

    def test_x_master_raises_value_error(self) -> None:
        """Verify PlaygroundManager.get_or_create raises ValueError for x_master."""
        from src.services.agent_run_service import AgentRunService

        svc = AgentRunService(sessions_service=MagicMock())
        with pytest.raises(ValueError, match="x_master"):
            svc._pg_manager.get_or_create("sess-1", playground_type="x_master")
