"""Unit tests for the src-layer BohriumSetupService orchestration.

Verifies:
- event_sink constructor arg replaces bus
- _make_event_bridge() maps error -> ErrorEvent + StreamClosedEvent
- _make_event_bridge() maps stream_closed -> StreamClosedEvent
- _make_event_bridge() maps other callbacks -> BohriumNodeEvent
- run_setup/run_cleanup delegate to owned methods
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from matmaster.bohrium.runtime import get_runtime
from matmaster.types.events import (
    BohriumNodeEvent,
    ErrorEvent,
    StreamClosedEvent,
)


def _make_service(*, event_sink=None, sessions_service: Any = None):
    from src.services.agent_run_bohrium import BohriumSetupService

    return BohriumSetupService(
        sessions_service=sessions_service or MagicMock(),
        event_sink=event_sink,
    )


class TestBohriumSetupServiceOrchestration:
    """Verify run_setup/run_cleanup orchestrate the owned runtime methods."""

    @pytest.mark.asyncio
    async def test_run_setup_loads_credentials_and_delegates(self):
        from src.services.agent_run_bohrium import BohriumSetupResult

        sink = MagicMock()
        svc = _make_service(event_sink=sink)
        event_cb = MagicMock()
        expected = BohriumSetupResult(
            ssh_attached=True,
            abort_result=None,
            execution_session=None,
            execution_workdir="/remote",
            session_type="ssh",
            runtime_snapshot=None,
        )

        with (
            patch.object(
                svc,
                "_load_run_credentials",
                return_value=({"access_key": "test"}, "u1", "org1"),
            ) as mock_load,
            patch.object(
                svc,
                "_setup_bohrium_for_run",
                return_value=expected,
            ) as mock_setup,
            patch.object(svc, "_make_event_bridge", return_value=event_cb),
        ):
            result = await svc.run_setup(
                session_id="session-123",
                playground=object(),
                run_started_at=1000.0,
            )

        mock_load.assert_called_once_with("session-123")
        mock_setup.assert_called_once()
        kwargs = mock_setup.call_args.kwargs
        assert kwargs["session_id"] == "session-123"
        assert kwargs["run_creds"] == {"access_key": "test"}
        assert kwargs["user_id_for_ak"] == "u1"
        assert kwargs["org_id"] == "org1"
        assert kwargs["event_callback"] is event_cb
        assert result is expected

    @pytest.mark.asyncio
    async def test_run_cleanup_delegates_to_owned_cleanup_method(self):
        sink = MagicMock()
        svc = _make_service(event_sink=sink)
        event_cb = MagicMock()

        with (
            patch.object(svc, "_cleanup_bohrium_after_run") as mock_cleanup,
            patch.object(svc, "_make_event_bridge", return_value=event_cb),
        ):
            await svc.run_cleanup(
                session_id="s1",
                pg_for_run=object(),
                ssh_attached=True,
            )

        mock_cleanup.assert_called_once()
        kwargs = mock_cleanup.call_args.kwargs
        assert kwargs["session_id"] == "s1"
        assert kwargs["event_callback"] is event_cb
        assert kwargs["ssh_attached"] is True

    @pytest.mark.asyncio
    async def test_run_setup_delegates_bohrium_required_flag(self):
        from src.services.agent_run_bohrium import BohriumSetupResult

        svc = _make_service(event_sink=MagicMock())
        event_cb = MagicMock()
        expected = BohriumSetupResult(
            True,
            None,
            None,
            '/remote',
            'ssh',
            None,
        )

        with (
            patch.object(svc, '_make_event_bridge', return_value=event_cb),
            patch.object(
                svc, '_run_setup_sync', return_value=expected
            ) as mock_setup_sync,
        ):
            await svc.run_setup(
                session_id='sess-1',
                playground=object(),
                run_started_at=1.0,
                bohrium_required=True,
            )

        assert mock_setup_sync.call_args.kwargs['bohrium_required'] is True

    def test_run_setup_sync_required_access_key_failure_aborts_before_node_setup(
        self,
    ):
        from src.services.user_service import BohriumAccessKeyFetchResult

        svc = _make_service(event_sink=MagicMock())
        sink = MagicMock()
        failed = BohriumAccessKeyFetchResult(
            status='timeout',
            retryable=False,
            attempts=3,
            access_key=None,
        )

        with (
            patch.object(
                svc,
                '_load_run_credentials',
                return_value=({'project_id': 9}, 'u1', 'o1'),
            ),
            patch.object(svc, '_setup_bohrium_for_run') as mock_setup,
            patch(
                'src.services.agent_run_bohrium.UserService.fetch_bohrium_access_key_result',
                return_value=failed,
            ),
        ):
            result = svc._run_setup_sync(
                session_id='sess-1',
                pg=object(),
                event_callback=sink,
                run_started_at=1.0,
                bohrium_required=True,
            )

        assert result.abort_result is not None
        mock_setup.assert_not_called()

    def test_configure_remote_user_skill_root_on_ssh_session(self):
        from src.services.agent_run_bohrium import (
            _BOHRIUM_REMOTE_USER_SKILLS_ROOT,
            _configure_remote_user_skill_root,
        )

        session = SimpleNamespace()

        _configure_remote_user_skill_root(session)

        assert session.remote_user_skills_root == _BOHRIUM_REMOTE_USER_SKILLS_ROOT
        assert session.remote_skill_roots == [_BOHRIUM_REMOTE_USER_SKILLS_ROOT]


class TestBohriumEventBridgeMapping:
    """_make_event_bridge() correctly maps callback types to BusEvent objects."""

    def _collect_events(self) -> tuple[MagicMock, list[Any]]:
        """Create a sink that collects events."""
        collected: list[Any] = []

        def sink(event: Any) -> None:
            collected.append(event)

        return MagicMock(side_effect=sink), collected

    def test_error_callback_produces_error_and_stream_closed(self):
        """error type maps to ErrorEvent + StreamClosedEvent."""
        sink, collected = self._collect_events()
        svc = _make_service(event_sink=sink)
        loop = asyncio.new_event_loop()

        try:
            bridge = svc._make_event_bridge(loop)
            bridge('System', 'error', 'something failed')

            # Run scheduled callbacks
            loop.run_until_complete(asyncio.sleep(0.01))

            assert len(collected) == 2
            assert isinstance(collected[0], ErrorEvent)
            assert collected[0].message == 'something failed'
            assert isinstance(collected[1], StreamClosedEvent)
            assert collected[1].treat_as_failure is True
        finally:
            loop.close()

    def test_stream_closed_callback_produces_stream_closed_event(self):
        """stream_closed type maps to StreamClosedEvent."""
        sink, collected = self._collect_events()
        svc = _make_service(event_sink=sink)
        loop = asyncio.new_event_loop()

        try:
            bridge = svc._make_event_bridge(loop)
            bridge('System', 'stream_closed', 'session ended')

            loop.run_until_complete(asyncio.sleep(0.01))

            assert len(collected) == 1
            assert isinstance(collected[0], StreamClosedEvent)
            assert collected[0].end_reason == 'error'
        finally:
            loop.close()

    def test_bohrium_node_callback_produces_bohrium_node_event(self):
        """Non-error/non-stream_closed types map to BohriumNodeEvent."""
        sink, collected = self._collect_events()
        svc = _make_service(event_sink=sink)
        loop = asyncio.new_event_loop()

        try:
            bridge = svc._make_event_bridge(loop)
            bridge('System', 'bohrium_node', {'status': 'ready', 'message': 'ok'})

            loop.run_until_complete(asyncio.sleep(0.01))

            assert len(collected) == 1
            assert isinstance(collected[0], BohriumNodeEvent)
            assert collected[0].payload['type'] == 'bohrium_node'
            assert collected[0].payload['content']['status'] == 'ready'
        finally:
            loop.close()

    def test_bridge_emits_to_threadsafe_sink_without_loop_spin(self):
        """Thread-safe sinks should receive events immediately from worker callbacks."""
        sink, collected = self._collect_events()
        svc = _make_service(event_sink=sink)
        loop = asyncio.new_event_loop()

        try:
            bridge = svc._make_event_bridge(loop)
            bridge('System', 'bohrium_node', {'status': 'ready'})

            assert len(collected) == 1
            assert isinstance(collected[0], BohriumNodeEvent)
            assert collected[0].payload['content']['status'] == 'ready'
        finally:
            loop.close()


class TestBohriumSetupServiceConstructor:
    """Verify constructor accepts event_sink instead of bus."""

    def test_accepts_event_sink_parameter(self):
        from src.services.agent_run_bohrium import BohriumSetupService

        sink = MagicMock()
        svc = BohriumSetupService(
            sessions_service=MagicMock(),
            event_sink=sink,
        )
        assert svc._event_sink is sink

    def test_event_sink_defaults_to_none(self):
        from src.services.agent_run_bohrium import BohriumSetupService

        svc = BohriumSetupService(sessions_service=MagicMock())
        assert svc._event_sink is None


def test_apply_run_credentials_registers_runtime_without_dual_write() -> None:
    from matmaster.bohrium.runtime import (
        attach_local_bohrium_runtime_from_run_credentials,
    )

    session = SimpleNamespace()
    run_creds = {
        "access_key": "ak",
        "project_id": 42,
        "user_id": "7",
        "user_no": "U001",
        "base_url": "https://openapi.test.dp.tech/",
    }

    attach_local_bohrium_runtime_from_run_credentials(session, run_creds)

    runtime = get_runtime(session)
    assert runtime is not None
    assert runtime.credentials().access_key == "ak"
    assert not hasattr(session, "_bohrium_credentials")


def test_playground_context_with_bohrium_uses_snapshot_dict() -> None:
    from matmaster.types.context import PlaygroundContext

    ctx = PlaygroundContext(
        workdir=Path("/tmp/work"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    )

    updated = ctx.with_bohrium(
        {
            "session_type": "ssh",
            "execution_workdir": "/share",
            "remote_workspace_root": "/share",
            "remote_project_root": "/share/.matmaster",
            "node_id": 9,
            "node_ip": "10.0.0.9",
            "ssh_attached": True,
        }
    )

    assert updated.run_meta["bohrium"]["node_id"] == 9
