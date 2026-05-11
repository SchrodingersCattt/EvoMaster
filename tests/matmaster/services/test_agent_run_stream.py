"""DBUS-03: Integration tests for AgentRunService.run_agent() (single entrypoint).

Verifies the generator event -> fanout dispatch, source normalization,
StreamClosedEvent emission, error handling, and worker-mode send_cb
live delivery through SSEHandler.

After Plan 02 collapse, run_agent_stream() no longer exists;
all tests exercise run_agent() exclusively.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from matmaster.types.cancellation import CancellationController
from matmaster.types.events import (
    ResponseEvent,
    RunResultEvent,
    ThoughtEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_playground(pg_ctx: Any) -> Any:
    """Build a mock Playground that returns the given PlaygroundContext."""
    pg = MagicMock()

    def _prepare(run_meta: dict[str, Any]) -> Any:
        pg_ctx.run_meta = dict(run_meta)
        return pg_ctx

    pg.prepare.side_effect = _prepare
    return pg


def _make_mock_pg_ctx() -> MagicMock:
    """Build a mock PlaygroundContext with minimum viable fields."""
    from matmaster.types.runtime_ports import PlaygroundRuntimePorts

    ctx = MagicMock()
    ctx.workdir = '/tmp/workspace'
    ctx.execution_workdir = '/tmp/workspace'
    ctx.session = MagicMock()
    ctx.session._cancel_token = None
    ctx.session.capabilities = MagicMock()
    ctx.session.path_exists.return_value = False
    ctx.session.read_file.return_value = ''
    ctx.archival = None
    ctx.run_meta = {}
    ctx.runtime_ports = PlaygroundRuntimePorts()
    ctx.with_bohrium.return_value = ctx
    ctx.with_execution.return_value = ctx

    def _with_runtime_ports(runtime_ports: PlaygroundRuntimePorts) -> MagicMock:
        ctx.runtime_ports = runtime_ports
        return ctx

    ctx.with_runtime_ports.side_effect = _with_runtime_ports

    def _model_copy(*, update: dict[str, Any] | None = None, **_: Any) -> MagicMock:
        if update:
            for key, value in update.items():
                setattr(ctx, key, value)
        return ctx

    ctx.model_copy.side_effect = _model_copy
    return ctx


def _make_cancel_token():
    """Build a real CancellationToken for integration-style tests."""
    return CancellationController().token


class _FakeExp:
    """Minimal Exp stand-in that returns a canned async generator from run_stream."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events
        self._config = MagicMock()
        self._config.name = 'direct'
        self._cleanup_callbacks: list = []
        self.last_ctx: Any = None
        self.last_task: str | None = None
        self.last_run_kwargs: dict[str, Any] | None = None

    async def run_stream(self, *args: Any, **kwargs: Any):
        self.last_ctx = args[0] if args else None
        self.last_task = args[1] if len(args) > 1 else None
        self.last_run_kwargs = kwargs
        try:
            if callable(self._events):
                stream = self._events(self.last_ctx)
                async for event in stream:
                    yield event
            else:
                for event in self._events:
                    yield event
        finally:
            await self._run_cleanup_callbacks()

    async def build_runtime(self, *args: Any, **kwargs: Any) -> Any:
        runtime = MagicMock()
        spec = MagicMock()
        spec.hook_executor = None
        spec.tool_catalog = None
        runtime.spec = spec
        return runtime

    async def _run_cleanup_callbacks(self) -> None:
        pass


class _ImmediateReplyQueue:
    """测试用 reply queue，立即返回预设 envelope。"""

    def __init__(self, envelope: str) -> None:
        self._envelope = envelope

    def put_content(self, content: str) -> None:
        self._envelope = content

    def put_cancel(self) -> None:
        self._envelope = ''

    def get(self, timeout: float | None = None) -> str | None:
        return self._envelope


# ---------------------------------------------------------------------------
# Patches: Isolate run_agent from heavy infrastructure
# ---------------------------------------------------------------------------


def _standard_patches():
    """Return a list of patch context managers for isolating run_agent."""
    return [
        patch('src.services.agent_run_service.PlaygroundManager'),
        patch('src.services.agent_run_service.get_chat_events_table'),
        patch('src.services.agent_run_service.SSEHandler'),
        patch('src.services.agent_run_service.PersistenceHandler'),
        patch('src.services.agent_run_service.WorkspaceHandler'),
        patch('src.services.agent_run_service.BohriumSetupService'),
        patch(
            'src.services.agent_run_service.HistoryRestoreService',
            create=True,
        ),
        patch('src.services.agent_run_service.get_redis_dao'),
        patch('src.services.agent_run_service.use_quota', new_callable=AsyncMock),
        patch(
            'src.services.agent_run_service._get_agent_default_llm', return_value=None
        ),
    ]


@asynccontextmanager
async def _patched_service(events: list[Any], *, send_cb: Any = None):
    """Set up an AgentRunService with all infra patched.

    Yields (service, sse_received, persist_received).
    """
    patches = _standard_patches()
    mocks = []
    for p in patches:
        mocks.append(p.start())

    try:
        pg_mgr_cls = mocks[0]
        events_table_fn = mocks[1]
        sse_handler_cls = mocks[2]
        persistence_handler_cls = mocks[3]
        workspace_handler_cls = mocks[4]
        bohrium_cls = mocks[5]
        history_restore_cls = mocks[6]
        redis_fn = mocks[7]

        # PlaygroundManager mock
        pg_ctx = _make_mock_pg_ctx()
        pg = _make_mock_playground(pg_ctx)
        pg_mgr = MagicMock()
        pg_mgr.get_or_create.return_value = pg
        pg_mgr_cls.return_value = pg_mgr

        # SSEHandler mock -- records events it receives
        sse_received: list[Any] = []

        class _RecordingSSEHandler:
            def __init__(
                self,
                send_cb_arg: Any,
                session_id_arg: str,
                task_id_arg: str,
                invocation_id_arg: str | None,
                mode_arg: str,
            ) -> None:
                self._send_cb = send_cb_arg
                self._session_id = session_id_arg
                self._task_id = task_id_arg
                self._invocation_id = invocation_id_arg

            async def handle(self, event: Any) -> None:
                from matmaster.integration.event_payloads import (
                    build_public_sse_payload_from_bus_dump,
                )

                sse_received.append(event)
                payload = build_public_sse_payload_from_bus_dump(
                    event.model_dump(mode="json"),
                    session_id=self._session_id,
                    task_id=self._task_id,
                    invocation_id=self._invocation_id,
                    spawn_id=getattr(event, "spawn_id", None),
                )
                result = self._send_cb(payload)
                if inspect.isawaitable(result):
                    await result

        sse_handler_cls.side_effect = _RecordingSSEHandler

        # PersistenceHandler mock
        persist_received: list[Any] = []
        persist_inst = MagicMock()
        persist_inst.handle = AsyncMock(
            side_effect=lambda event: persist_received.append(event)
        )
        persistence_handler_cls.return_value = persist_inst

        # WorkspaceHandler mock
        ws_inst = MagicMock()
        ws_inst.handle = AsyncMock()
        ws_inst.close = MagicMock()
        workspace_handler_cls.return_value = ws_inst

        # Bohrium mock -- no SSH, no abort
        bohrium_inst = MagicMock()
        bohrium_result = MagicMock()
        bohrium_result.ssh_attached = False
        bohrium_result.abort_result = None
        bohrium_result.execution_session = None
        bohrium_result._asdict.return_value = {
            'ssh_attached': False,
            'abort_result': None,
        }
        bohrium_inst.run_setup = AsyncMock(return_value=bohrium_result)
        bohrium_inst.run_cleanup = AsyncMock()
        bohrium_cls.return_value = bohrium_inst

        # HistoryRestoreService mock
        history_restore_inst = MagicMock()
        history_restore_inst.restore_history.return_value = []
        history_restore_cls.return_value = history_restore_inst

        # Redis mock
        redis_mock = MagicMock()
        redis_fn.return_value = redis_mock

        # events_table mock
        events_table_fn.return_value = MagicMock()

        # Patch Exp to use our fake events
        fake_exp = _FakeExp(events)

        with (
            patch('matmaster.config.loader.load_exp_config', return_value=MagicMock()),
            patch('matmaster.config.loader.load_llm_config', return_value=MagicMock()),
            patch(
                'matmaster.providers.llm_factory.build_provider',
                return_value=MagicMock(),
            ),
            patch('matmaster.core.exp.Exp', new=lambda config: fake_exp),
        ):

            from src.services.agent_run_service import AgentRunService

            svc = AgentRunService.__new__(AgentRunService)
            svc._sessions_service = MagicMock()
            svc._sessions_service.get_session_user_id.return_value = 'user-1'
            svc._pg_manager = pg_mgr
            svc._active_skills = {}
            svc._test_fake_exp = fake_exp
            svc._test_pg_ctx = pg_ctx
            svc._test_events_table = events_table_fn.return_value
            svc._test_bohrium_svc = bohrium_inst

            yield svc, sse_received, persist_received

    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Tests: All via run_agent() -- no run_agent_stream() alias
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_stream_method_does_not_exist():
    """After Plan 02, run_agent_stream() must not exist on AgentRunService."""
    from src.services.agent_run_service import AgentRunService

    assert not hasattr(
        AgentRunService, 'run_agent_stream'
    ), "run_agent_stream() should be removed; run_agent() is the sole entrypoint"


@pytest.mark.asyncio
async def test_run_agent_signature_does_not_accept_reply_queue():
    """Unused confirmation queue plumbing should not remain in run_agent()."""
    from src.services.agent_run_service import AgentRunService

    params = inspect.signature(AgentRunService.run_agent).parameters
    assert 'reply_queue' not in params, (
        "run_agent() should not accept reply_queue; "
        "confirmation queue plumbing is no longer used"
    )


@pytest.mark.asyncio
async def test_run_agent_injects_cancel_token_into_session_and_exp():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')
    cancel_token = _make_cancel_token()

    async with _patched_service([run_result]) as (svc, _, __):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=cancel_token,
            mode='direct',
            task_id='t1',
        )

    assert svc._test_pg_ctx.session._cancel_token is cancel_token
    assert svc._test_fake_exp.last_run_kwargs is not None
    assert svc._test_fake_exp.last_run_kwargs['cancel_token'] is cancel_token


@pytest.mark.asyncio
async def test_run_agent_injects_child_event_forward_sink_into_runtime_ports():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        ok, _elapsed = await svc.run_agent(
            session_id='sess-1',
            user_prompt='hello',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='task-1',
        )

    assert ok is True
    ports = svc._test_fake_exp.last_ctx.runtime_ports
    injected = ports.child_event_forward_sink
    assert callable(injected)
    assert svc._test_fake_exp.last_ctx.run_meta['task_id'] == 'task-1'


@pytest.mark.asyncio
async def test_run_agent_injects_figure_upload_config_into_pg_ctx_run_meta():
    run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='done',
    )

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        controller = CancellationController()
        await svc.run_agent(
            session_id='sess-1',
            user_prompt='make a plot',
            send_cb=lambda payload: None,
            cancel_token=controller.token,
            mode='direct',
            task_id='task-1',
        )

    figure_cfg = svc._test_fake_exp.last_ctx.run_meta['figure_upload_config']
    assert figure_cfg.session_id == 'sess-1'
    assert figure_cfg.task_id == 'task-1'
    assert callable(figure_cfg.upload_bytes)


@pytest.mark.asyncio
async def test_run_agent_injects_current_input_context_into_pg_ctx_run_meta():
    from matmaster.types.current_input import CurrentInputContext

    run_result = RunResultEvent(source="agent", status="completed", reason="natural")
    current_input_context = CurrentInputContext.from_values(
        user_text="current prompt",
        files=["https://oss.example.com/chat/current.cif"],
        pre_query_scope_event_id=21,
    )

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        ok, _elapsed = await svc.run_agent(
            session_id="sess-1",
            user_prompt="current prompt",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            current_input_context=current_input_context,
        )

    assert ok is True
    assert (
        svc._test_fake_exp.last_ctx.run_meta["current_input_context"]
        == current_input_context
    )


@pytest.mark.asyncio
async def test_agent_run_service_injects_full_attachment_manifest_before_exp_run():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, _, __):
        svc._test_events_table.get_session_user_query_events.return_value = [
            {
                "id": 1,
                "source": "User",
                "type": "query",
                "content": "upload",
                "files": ["https://oss.example.com/chat/data.csv"],
            }
        ]

        await svc.run_agent(
            session_id='sess-attachments',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='task-1',
        )

    run_meta = svc._test_fake_exp.last_ctx.run_meta
    assert "attachment_manifest" in run_meta
    assert "[Available attachments]" in run_meta["attachment_manifest"]
    assert (
        "file_1 data.csv https://oss.example.com/chat/data.csv"
        in run_meta["attachment_manifest"]
    )
    history = svc._test_fake_exp.last_ctx.runtime_ports.compaction.history
    assert history is not None
    assert callable(history.query_events)
    assert callable(history.all_events)
    assert callable(history.latest_checkpoint_covered_until_event_id)
    assert callable(
        svc._test_fake_exp.last_ctx.runtime_ports.compaction.pre_compaction_barrier
    )


@pytest.mark.asyncio
async def test_run_agent_uses_history_restore_service_and_injects_spawn_aware_checkpoint_factory():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')
    restored_history = [MagicMock(name='restored_message')]
    checkpoint_sink = AsyncMock(name='checkpoint_sink')

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        with (
            patch(
                'src.services.agent_run_service.HistoryRestoreService',
                create=True,
            ) as restore_cls,
            patch(
                'src.services.agent_run_service.HistoryCheckpointService',
                create=True,
            ) as checkpoint_cls,
        ):
            restore_inst = MagicMock()
            restore_inst.restore_history.return_value = restored_history
            restore_cls.return_value = restore_inst

            checkpoint_inst = MagicMock()
            checkpoint_inst.build_checkpoint_sink.return_value = checkpoint_sink
            checkpoint_cls.return_value = checkpoint_inst

            ok, _elapsed = await svc.run_agent(
                session_id='sess-1',
                user_prompt='hello',
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode='direct',
                task_id='task-1',
                invocation_id='inv-1',
            )

        assert ok is True
        restore_cls.assert_called_once_with(svc._test_events_table)
        restore_inst.restore_history.assert_called_once_with(
            session_id='sess-1',
            spawn_id=None,
            task_id='task-1',
            raw_limit=ANY,
        )
        assert svc._test_fake_exp.last_run_kwargs is not None
        assert svc._test_fake_exp.last_run_kwargs['history'] == restored_history

        checkpoint_cls.assert_called_once_with(svc._test_events_table)
        checkpoint_sink_factory = (
            svc._test_fake_exp.last_ctx.runtime_ports.compaction.checkpoint_sink_factory
        )
        assert callable(checkpoint_sink_factory)

        built_sink = checkpoint_sink_factory(spawn_id='spawn-child-1')

        checkpoint_inst.build_checkpoint_sink.assert_called_once_with(
            fanout=ANY,
            session_id='sess-1',
            task_id='task-1',
            invocation_id='inv-1',
            spawn_id='spawn-child-1',
        )
        assert built_sink is checkpoint_sink


def test_run_agent_injects_bohrium_rebuild_events_into_pg_ctx_run_meta():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')
    rebuild_events = [
        {
            'action': 'submit',
            'job_id': 'job-1',
            'job_name': 'alpha',
            'status': 'Submitted',
            'cached': False,
        }
    ]

    async def _run() -> tuple[Any, Any]:
        async with _patched_service([run_result]) as (svc, _sse, _persist):
            svc._test_events_table.get_bohrium_events.return_value = rebuild_events

            ok, _elapsed = await svc.run_agent(
                session_id='sess-1',
                user_prompt='hello',
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode='direct',
                task_id='task-1',
            )
            return svc, ok

    svc, ok = asyncio.run(_run())

    assert ok is True
    svc._test_events_table.get_bohrium_events.assert_called_once_with('sess-1')
    assert (
        svc._test_fake_exp.last_ctx.run_meta['bohrium_rebuild_events'] == rebuild_events
    )


@pytest.mark.asyncio
async def test_stream_events_reach_handlers_via_fanout():
    """Events from exp.run_stream() are dispatched through RunEventFanout to handlers."""
    thought = ThoughtEvent(source='agent', content='thinking...')
    response = ResponseEvent(source='agent', content='hello')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, response, run_result]) as (
        svc,
        sse_events,
        persist_events,
    ):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
        )

    # SSE handler should receive: thought + response + run_result + StreamClosedEvent = 4+
    sse_types = [getattr(e, 'type', None) for e in sse_events]
    assert 'thought' in sse_types
    assert 'response' in sse_types
    assert 'run_result' in sse_types
    assert 'stream_closed' in sse_types


@pytest.mark.asyncio
async def test_child_event_sink_reaches_sse_and_persistence():
    async def child_then_parent(ctx):
        await ctx.runtime_ports.child_event_forward_sink(
            ResponseEvent(
                source='MatMaster:direct',
                spawn_id='childdeadbeef123',
                content='child answer',
            )
        )
        yield RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service(child_then_parent) as (
        svc,
        sse_events,
        persist_events,
    ):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
        )

    assert any(
        getattr(event, 'spawn_id', None) == 'childdeadbeef123' for event in sse_events
    )
    assert any(
        getattr(event, 'spawn_id', None) == 'childdeadbeef123'
        for event in persist_events
    )


@pytest.mark.asyncio
async def test_run_agent_does_not_store_callback_ports_in_run_meta():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        await svc.run_agent(
            session_id="session-1",
            user_prompt="hello",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-1",
        )

    run_meta = svc._test_fake_exp.last_ctx.run_meta
    forbidden = {
        "event_sink",
        "checkpoint_sink_factory",
        "get_query_events",
        "get_all_events",
        "get_latest_checkpoint_covered_until_event_id",
        "pre_compaction_barrier",
    }
    assert forbidden.isdisjoint(run_meta)


@pytest.mark.asyncio
async def test_source_normalization_on_events():
    """Event source is normalized to MatMaster before fanout dispatch."""
    thought = ThoughtEvent(source='agent', content='thinking...')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, run_result]) as (svc, sse_events, _):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
        )

    # All non-System events should be normalized to MatMaster
    for event in sse_events:
        src = getattr(event, 'source', '')
        if src != 'System':
            assert src == 'MatMaster', f'Expected MatMaster, got {src}'


@pytest.mark.asyncio
async def test_stream_closed_after_run_result():
    """StreamClosedEvent is dispatched after RunResultEvent."""
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, sse_events, _):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
        )

    stream_closed = [
        e for e in sse_events if getattr(e, 'type', None) == 'stream_closed'
    ]
    assert len(stream_closed) == 1
    sc = stream_closed[0]
    assert sc.task_completed is True
    assert sc.end_reason == 'natural'


@pytest.mark.asyncio
async def test_cancelled_run_emits_cancelled_and_closed():
    """Cancelled run dispatches CancelledEvent then StreamClosedEvent."""
    run_result = RunResultEvent(source='agent', status='cancelled', reason='cancelled')

    async with _patched_service([run_result]) as (svc, sse_events, _):
        result = await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
        )

    assert result[0] == (False, 'cancelled')

    types = [getattr(e, 'type', None) for e in sse_events]
    assert 'cancelled' in types
    assert 'stream_closed' in types

    sc = [e for e in sse_events if getattr(e, 'type', None) == 'stream_closed'][0]
    assert sc.end_reason == 'cancelled'
    assert sc.task_completed is False


@pytest.mark.asyncio
async def test_exception_emits_error_and_closed():
    """Exception during streaming dispatches error + StreamClosedEvent via fanout."""

    class _ErrorExp(_FakeExp):
        async def run_stream(self, *args, **kwargs):
            raise RuntimeError('test explosion')
            yield  # make it a generator  # noqa: E501

    patches = _standard_patches()
    mocks = []
    for p in patches:
        mocks.append(p.start())

    try:
        pg_mgr_cls = mocks[0]
        events_table_fn = mocks[1]
        sse_handler_cls = mocks[2]
        persistence_handler_cls = mocks[3]
        workspace_handler_cls = mocks[4]
        bohrium_cls = mocks[5]
        history_restore_cls = mocks[7]
        redis_fn = mocks[8]

        pg_ctx = _make_mock_pg_ctx()
        pg = _make_mock_playground(pg_ctx)
        pg_mgr = MagicMock()
        pg_mgr.get_or_create.return_value = pg
        pg_mgr_cls.return_value = pg_mgr

        # SSE handler mock
        sse_received: list[Any] = []
        sse_inst = MagicMock()
        sse_inst.handle = AsyncMock(
            side_effect=lambda event: sse_received.append(event)
        )
        sse_handler_cls.return_value = sse_inst

        # Persistence handler mock
        persist_inst = MagicMock()
        persist_inst.handle = AsyncMock()
        persistence_handler_cls.return_value = persist_inst

        # Workspace handler mock
        ws_inst = MagicMock()
        ws_inst.handle = AsyncMock()
        ws_inst.close = MagicMock()
        workspace_handler_cls.return_value = ws_inst

        bohrium_inst = MagicMock()
        bohrium_result = MagicMock()
        bohrium_result.ssh_attached = False
        bohrium_result.abort_result = None
        bohrium_result.execution_session = None
        bohrium_result._asdict.return_value = {
            'ssh_attached': False,
            'abort_result': None,
        }
        bohrium_inst.run_setup = AsyncMock(return_value=bohrium_result)
        bohrium_inst.run_cleanup = AsyncMock()
        bohrium_cls.return_value = bohrium_inst

        history_restore_inst = MagicMock()
        history_restore_inst.restore_history.return_value = []
        history_restore_cls.return_value = history_restore_inst
        redis_fn.return_value = MagicMock()
        events_table_fn.return_value = MagicMock()

        error_exp = _ErrorExp([])

        with (
            patch('matmaster.config.loader.load_exp_config', return_value=MagicMock()),
            patch('matmaster.config.loader.load_llm_config', return_value=MagicMock()),
            patch(
                'matmaster.providers.llm_factory.build_provider',
                return_value=MagicMock(),
            ),
            patch('matmaster.core.exp.Exp', new=lambda config: error_exp),
        ):

            from src.services.agent_run_service import AgentRunService

            svc = AgentRunService.__new__(AgentRunService)
            svc._sessions_service = MagicMock()
            svc._pg_manager = pg_mgr
            svc._active_skills = {}

            result = await svc.run_agent(
                session_id='s1',
                user_prompt='hi',
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode='direct',
                task_id='t1',
            )
    finally:
        for p in patches:
            p.stop()

    assert result[0] == (False, 'test explosion')


@pytest.mark.asyncio
async def test_successful_run_returns_true():
    """Successful completion returns (True, elapsed_ms)."""
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, _, __):
        result = await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
        )

    assert result[0] is True
    assert isinstance(result[1], int)
    assert result[1] >= 0


@pytest.mark.asyncio
async def test_failed_run_returns_false_with_reason():
    """Failed run returns ((False, reason), elapsed_ms)."""
    run_result = RunResultEvent(source='agent', status='failed', reason='max_turns')

    async with _patched_service([run_result]) as (svc, _, __):
        result = await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
        )

    assert result[0] == (False, 'max_turns')
    assert isinstance(result[1], int)


@pytest.mark.asyncio
async def test_worker_mode_send_cb_receives_live_events():
    """Worker mode SSEHandler(send_cb,...) stays on the live delivery path.

    Verifies the contract: send_cb -> Redis publish -> active SSE subscriber
    remains intact for both generator events and terminal/system parity events.
    """
    thought = ThoughtEvent(source='agent', content='thinking...')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, run_result]) as (svc, sse_events, _):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
        )

    # SSE handler must receive generator events + terminal events
    sse_types = [getattr(e, 'type', None) for e in sse_events]
    # Generator events
    assert 'thought' in sse_types
    assert 'run_result' in sse_types
    # Terminal/system parity events through SSEHandler
    assert 'stream_closed' in sse_types


@pytest.mark.asyncio
async def test_persistence_receives_events():
    """PersistenceHandler receives events through fanout dispatch."""
    thought = ThoughtEvent(source='agent', content='thinking...')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, run_result]) as (svc, _, persist_events):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
        )

    # Persistence handler should receive all events including terminal
    persist_types = [getattr(e, 'type', None) for e in persist_events]
    assert 'thought' in persist_types
    assert 'run_result' in persist_types
    assert 'stream_closed' in persist_types


@pytest.mark.asyncio
async def test_run_agent_passes_remote_workdir_to_bohrium_setup():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        await svc.run_agent(
            session_id="sess-1",
            user_prompt="hello",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            remote_workdir="/share/case",
        )

        bohrium_svc = svc._test_bohrium_svc
        call_kwargs = bohrium_svc.run_setup.call_args.kwargs

    assert call_kwargs["remote_workdir"] == "/share/case"
    assert call_kwargs["bohrium_required"] is True
