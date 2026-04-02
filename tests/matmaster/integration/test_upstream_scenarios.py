"""Upstream scenario tests: run interruption, workspace upload, Bohrium lifecycle,
event router filtering, cross-pod reply queue, x_master rejection.

All external dependencies mocked per D-10.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.config.exp import ExpConfig
from matmaster.core.agent import AgentKernel
from matmaster.core.bus import MessageBus
from matmaster.core.exp import Exp
from matmaster.core.hooks import BaseHook, HookAction
from matmaster.integration.bohrium_setup import BohriumSetupService, SkillSyncSpec
from matmaster.integration.persistence_handler import PersistenceHandler
from matmaster.integration.sse_handler import SSEHandler
from matmaster.integration.workspace_handler import WorkspaceHandler
from matmaster.tools.tool_registry import ToolRegistry
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

BohriumSetupResult = pytest.importorskip(
    "src.services.agent_run_bohrium",
    reason="src not available (isolation test)",
).BohriumSetupResult

# -- Mock helpers ------------------------------------------------


class _SlowMockLLM:
    """Mock LLM that takes time to respond (checks stop_event between turns)."""

    def __init__(self, turns: int = 3) -> None:
        self._turns = turns
        self._call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="done", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self._call_count += 1
        # Add a small delay to give stop_event time to be set
        await asyncio.sleep(0.05)
        yield StreamChunk(content=f"Turn {self._call_count}", finish_reason="stop")


class _NeverFinishLLM:
    """Mock LLM that always returns tool calls, never natural finish."""

    def __init__(self) -> None:
        self._call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="loop", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self._call_count += 1
        # Always yield a natural finish to avoid infinite loop
        yield StreamChunk(content=f"Turn {self._call_count}", finish_reason="stop")


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
        bus = MessageBus()

        exp = Exp(self._EXP_CONFIG)
        runtime = await exp.build_runtime(pg_ctx, bus=bus)

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
        bus = MessageBus()

        exp = Exp(self._EXP_CONFIG)
        runtime = await exp.build_runtime(pg_ctx, bus=bus)

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

    def test_bohrium_setup_lifecycle(self) -> None:
        """Verify BohriumSetupService.setup() and cleanup() delegate correctly."""
        bus = MessageBus()
        mock_load_creds = MagicMock(return_value=({}, None, "org-1"))
        mock_apply_creds = MagicMock()
        mock_setup_fn = MagicMock()
        mock_cleanup_fn = MagicMock()
        svc = BohriumSetupService(
            load_credentials_fn=mock_load_creds,
            apply_credentials_fn=mock_apply_creds,
            setup_fn=mock_setup_fn,
            cleanup_fn=mock_cleanup_fn,
            bus=bus,
        )

        skill_sync_spec = SkillSyncSpec(
            project_skill_roots=["/proj/skills"],
            remote_project_root="/remote/project",
        )
        mock_setup_fn.return_value = BohriumSetupResult(
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

        assert mock_setup_fn.called
        call_kw = mock_setup_fn.call_args.kwargs
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

        assert mock_cleanup_fn.called


# -- QUAL-04: Event router persistence ---------------------------


class TestEventRouterPersistence:
    """Verify PersistenceHandler filtering and persistence behavior."""

    async def test_event_router_persistence_on_standard_events(self) -> None:
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

    async def test_event_router_sse_push_filtering(self) -> None:
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


# -- QUAL-04: Cross-pod reply queue ------------------------------


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


class _SingleToolTurnProvider:
    """Emit one execute_bash tool turn, then finish naturally."""

    def __init__(self) -> None:
        self._call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="unused", finish_reason="stop")

    async def chat_stream(
        self,
        messages,
        tools=None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self._call_count += 1
        if self._call_count == 1:
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-bash-1",
                        "name": "execute_bash",
                        "arguments": '{"command":"echo ok"}',
                    }
                ]
            )
            yield StreamChunk(finish_reason="stop")
        else:
            yield StreamChunk(content="done", finish_reason="stop")


class _RecordingAsyncTool:
    """Async spy tool for service-layer confirmation recovery tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "execute_bash"

    @property
    def description(self) -> str:
        return "test execute_bash tool"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }

    async def execute(self, arguments: dict[str, Any]) -> str:
        self.calls.append(arguments)
        return "approved execution"


class _RecordingRuntimeHook(BaseHook):
    """Records whether runtime.spec hooks run before confirmation gate."""

    def __init__(self) -> None:
        self.pre_tool_call_count = 0

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self.pre_tool_call_count += 1
        return HookAction.CONTINUE


def _make_confirmation_runtime(
    provider: _SingleToolTurnProvider,
    tool: _RecordingAsyncTool,
    runtime_hook: _RecordingRuntimeHook,
) -> AgentRuntime:
    registry = ToolRegistry()
    registry.register(tool, source="test")
    spec = AgentRuntimeSpec(
        llm_provider=provider,
        tool_registry=registry,
        hooks=[runtime_hook],
        max_turns=5,
        system_prompt="You are a confirmation recovery test agent",
    )
    return AgentRuntime(kernel=AgentKernel(), spec=spec, cleanup=lambda: None)


class TestAgentRunServiceConfirmationRecovery:
    """QUAL-04: service-layer confirmation recovery under Worker + Redis semantics."""

    def _make_playground(self, tmp_path: Path) -> tuple[MagicMock, PlaygroundContext]:
        mock_pg = MagicMock()
        mock_pg_ctx = _make_ctx(tmp_path)
        mock_pg.prepare.return_value = mock_pg_ctx
        mock_pg.config_path = Path("config/config.yaml")
        mock_pg.config = SimpleNamespace()
        mock_pg.session = None
        return mock_pg, mock_pg_ctx

    def _run_with_runtime(
        self,
        tmp_path: Path,
        *,
        reply_queue: _RedisCompatibleReplyQueue,
        reply_fn,
    ) -> tuple[
        tuple[bool | tuple[bool, str], int],
        list[dict[str, Any]],
        _RecordingAsyncTool,
        _RecordingRuntimeHook,
    ]:
        AgentRunService = pytest.importorskip(
            "src.services.agent_run_service",
            reason="src not available (isolation test)",
        ).AgentRunService

        svc = AgentRunService(sessions_service=MagicMock())
        svc._sessions_service.get_session_user_id.return_value = None

        provider = _SingleToolTurnProvider()
        tool = _RecordingAsyncTool()
        runtime_hook = _RecordingRuntimeHook()
        runtime = _make_confirmation_runtime(provider, tool, runtime_hook)
        mock_pg, _ = self._make_playground(tmp_path)
        payloads: list[dict[str, Any]] = []

        async def send_cb(payload: dict[str, Any]) -> None:
            payloads.append(payload)

        async def _fake_build_runtime(self, pg_ctx, bus=None, skills=None):
            return runtime

        mock_bohrium_result = MagicMock()
        mock_bohrium_result.ssh_attached = False
        mock_bohrium_result.abort_result = None
        mock_bohrium_result.execution_session = None
        mock_bohrium_result.execution_workdir = None
        mock_bohrium_result.session_type = None
        mock_bohrium_result._asdict.return_value = {
            "ssh_attached": False,
            "abort_result": None,
            "execution_session": None,
            "execution_workdir": None,
            "session_type": None,
        }

        mock_events_table = MagicMock()
        mock_events_table.get_session_events.return_value = []

        with (
            patch.object(svc._pg_manager, "get_or_create", return_value=mock_pg),
            patch(
                "src.services.agent_run_service.BohriumSetupService"
            ) as mock_bohrium_cls,
            patch(
                "src.services.agent_run_service.get_chat_events_table",
                return_value=mock_events_table,
            ),
            patch(
                "src.services.agent_run_service.get_redis_dao",
                return_value=MagicMock(),
            ),
            patch(
                "matmaster.config.loader.load_llm_config",
                return_value=MagicMock(),
            ),
            patch(
                "matmaster.providers.llm_factory.build_provider",
                return_value=provider,
            ),
            patch.object(Exp, "build_runtime", _fake_build_runtime),
        ):
            mock_bohrium_svc = mock_bohrium_cls.return_value
            mock_bohrium_svc.load_credentials.return_value = ({}, None, "org-1")
            mock_bohrium_svc.run_setup = AsyncMock(return_value=mock_bohrium_result)
            mock_bohrium_svc.run_cleanup = AsyncMock()

            responder = threading.Thread(target=reply_fn, daemon=True)
            responder.start()
            result = asyncio.run(
                svc.run_agent(
                    session_id="sess-confirmation",
                    user_prompt="run command",
                    send_cb=send_cb,
                    stop_event=threading.Event(),
                    mode="direct",
                    reply_queue=reply_queue,
                    task_id="task-confirmation",
                )
            )
            responder.join(timeout=2.0)

        return result, payloads, tool, runtime_hook

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

    def test_run_agent_executes_tool_without_confirmation(self, tmp_path: Path) -> None:
        """With _CONFIRM_TOOLS empty, tools execute directly without confirmation gate."""
        reply_queue = _RedisCompatibleReplyQueue()

        def noop() -> None:
            pass

        result, payloads, tool, runtime_hook = self._run_with_runtime(
            tmp_path,
            reply_queue=reply_queue,
            reply_fn=noop,
        )

        assert result[0] is True
        assert len(tool.calls) == 1
        assert runtime_hook.pre_tool_call_count == 1
        # No confirmation events when _CONFIRM_TOOLS is empty
        confirmation_events = [
            payload
            for payload in payloads
            if payload.get("type") == "confirmation_request"
        ]
        assert len(confirmation_events) == 0
        assert not any(
            payload.get("type") == "error"
            and "AttributeError" in str(payload.get("message"))
            for payload in payloads
        )
