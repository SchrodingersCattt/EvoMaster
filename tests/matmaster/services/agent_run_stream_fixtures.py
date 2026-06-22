from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from matmaster.core.playground import ExecutionEnvironment
from matmaster.types.cancellation import CancellationController
from matmaster.types.run_metadata import RunMetadata
from matmaster.types.session import Session


def _make_mock_session() -> MagicMock:
    """Build a MagicMock session with the configured returns the service reads.

    ``spec=Session`` makes the mock pass ExecutionEnvironment's
    ``session: Session | None`` isinstance validation (the Session Protocol is
    ``@runtime_checkable``); a bare MagicMock would be rejected by pydantic.
    """
    session = MagicMock(spec=Session)
    session._cancel_token = None
    session.capabilities = MagicMock()
    session.path_exists.return_value = False
    session.read_file.return_value = ''
    return session


def _make_mock_environment(session: MagicMock | None = None) -> ExecutionEnvironment:
    """Build a REAL ExecutionEnvironment (physical substrate) for the service.

    The session stays a MagicMock (arbitrary types are allowed on the frozen
    pydantic model). The service now builds AgentRunContext / AgentRunRequest
    itself, so prepare() only needs to return this physical environment.
    """
    return ExecutionEnvironment(
        workdir=Path('/tmp/workspace'),
        session_type='local',
        cache_area=Path('/tmp/workspace/.cache'),
        session=session if session is not None else _make_mock_session(),
        metadata=RunMetadata(),
    )


def _make_mock_playground(environment: ExecutionEnvironment) -> Any:
    """Build a mock Playground whose prepare() returns a real ExecutionEnvironment.

    ``prepare`` reads ``pg._base_env`` each call so a test can swap the
    environment's session before invoking ``run_agent`` (the frozen
    ExecutionEnvironment cannot be mutated in place).
    """
    pg = MagicMock()
    pg._base_env = environment

    def _prepare(
        metadata: RunMetadata,
        *,
        session_id: str = "",
    ) -> ExecutionEnvironment:
        return pg._base_env.model_copy(
            update={'session_id': session_id, 'metadata': metadata}
        )

    pg.prepare.side_effect = _prepare
    return pg


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
        if self.last_task is None and self.last_ctx is not None:
            turn_input = self.last_ctx.request.turn_input
            self.last_task = turn_input.user_text if turn_input is not None else None
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
        resources = MagicMock()
        resources.hook_executor = None
        resources.tool_catalog = None
        kernel_runtime = MagicMock()
        kernel_runtime.resources = resources
        runtime.kernel_runtime = kernel_runtime
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


def _standard_patches():
    """Return a list of patch context managers for isolating run_agent."""
    return [
        patch('src.services.agent_run_service.PlaygroundManager'),
        patch('src.services.agent_run_service.get_chat_events_table'),
        patch('src.services.agent_run_service.SSEHandler'),
        patch('src.services.agent_run_service.PersistenceHandler'),
        patch('src.services.agent_run_bohrium_stage.WorkspaceHandler'),
        patch('src.services.agent_run_bohrium_stage.BohriumSetupService'),
        patch(
            'src.services.agent_run_history_wiring.ModelHistoryRestoreService',
            create=True,
        ),
        patch('src.services.agent_run_service.get_redis_dao'),
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

        mock_session = _make_mock_session()
        environment = _make_mock_environment(mock_session)
        pg = _make_mock_playground(environment)
        pg_mgr = MagicMock()
        pg_mgr.get_or_create.return_value = pg
        pg_mgr_cls.return_value = pg_mgr

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

        persist_received: list[Any] = []
        persist_inst = MagicMock()
        persist_inst.handle = AsyncMock(
            side_effect=lambda event: persist_received.append(event)
        )
        persistence_handler_cls.return_value = persist_inst

        ws_inst = MagicMock()
        ws_inst.handle = AsyncMock()
        ws_inst.close = MagicMock()
        workspace_handler_cls.return_value = ws_inst

        bohrium_inst = MagicMock()
        bohrium_result = MagicMock()
        bohrium_result.ssh_attached = False
        bohrium_result.abort_result = None
        # None runtime_snapshot / execution_session / session_type means the
        # real run_bohrium_stage passes the ExecutionEnvironment through
        # unchanged (no with_bohrium / with_execution rebinding).
        bohrium_result.runtime_snapshot = None
        bohrium_result.execution_session = None
        bohrium_result.session_type = None
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

        redis_mock = MagicMock()
        redis_fn.return_value = redis_mock

        events_table = MagicMock()
        events_table.get_recent_context_anchor_events.return_value = []
        events_table.query_user_turn_context_by_invocation.return_value = None
        events_table.add_event.return_value = True
        events_table.get_session_user_query_events.return_value = []
        events_table.query_context_events.return_value = []
        events_table_fn.return_value = events_table

        fake_exp = _FakeExp(events)
        provider_bundle = SimpleNamespace(
            provider=MagicMock(),
            model="test-model",
            model_profile="test-profile",
            model_route="test-route",
            provider_name="openai",
            context_limit=345_000,
            context_limit_source="profile",
            supports_vision=False,
            vision_detail=None,
        )

        with (
            patch('matmaster.config.loader.load_exp_config', return_value=MagicMock()),
            patch('matmaster.config.loader.load_llm_config', return_value=MagicMock()),
            patch(
                'matmaster.providers.llm_factory.build_provider_bundle',
                return_value=provider_bundle,
            ),
            patch('matmaster.core.exp.Exp', new=lambda config: fake_exp),
        ):

            from src.services.agent_run_service import AgentRunService

            svc = AgentRunService.__new__(AgentRunService)
            svc._sessions_service = MagicMock()
            svc._sessions_service.get_session_user_id.return_value = 'user-1'
            svc._pg_manager = pg_mgr
            svc._test_fake_exp = fake_exp
            svc._test_environment = environment
            svc._test_session = mock_session
            svc._test_playground = pg
            svc._test_events_table = events_table_fn.return_value
            svc._test_bohrium_svc = bohrium_inst
            svc._test_redis_dao = redis_mock

            yield svc, sse_received, persist_received

    finally:
        for p in patches:
            p.stop()
