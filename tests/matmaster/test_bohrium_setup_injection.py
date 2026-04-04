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
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from matmaster.integration.bohrium_env import BohriumSetupResult
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
        sink = MagicMock()
        svc = _make_service(event_sink=sink)
        event_cb = MagicMock()
        expected = BohriumSetupResult(
            ssh_attached=True,
            abort_result=None,
            execution_session=None,
            execution_workdir="/remote",
            session_type="ssh",
        )

        with (
            patch.object(
                svc,
                "_load_run_credentials",
                return_value=({"ak": "test"}, "u1", "org1"),
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
                skill_sync_spec=None,
                run_started_at=1000.0,
            )

        mock_load.assert_called_once_with("session-123")
        mock_setup.assert_called_once()
        kwargs = mock_setup.call_args.kwargs
        assert kwargs["session_id"] == "session-123"
        assert kwargs["run_creds"] == {"ak": "test"}
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


class TestBohriumSetupServiceLocation:
    """Verify the service now lives in src/services instead of matmaster/integration."""

    def _project_root(self) -> Path:
        """Resolve project root robustly (works in worktrees too)."""
        # Walk up from test file to find the directory containing both src/ and matmaster/
        candidate = Path(__file__).resolve().parent
        for _ in range(10):
            if (candidate / "src" / "services").is_dir() and (
                candidate / "matmaster"
            ).is_dir():
                return candidate
            candidate = candidate.parent
        raise RuntimeError("Could not find project root")

    def test_integration_init_no_longer_exports_bohrium_setup_service(self):
        root = self._project_root()
        init_file = root / "matmaster" / "integration" / "__init__.py"
        source = init_file.read_text(encoding="utf-8")
        assert "BohriumSetupService" not in source

    def test_src_agent_run_bohrium_defines_service_and_skill_sync_spec(self):
        root = self._project_root()
        service_file = root / "src" / "services" / "agent_run_bohrium.py"
        source = service_file.read_text(encoding="utf-8")
        assert "class BohriumSetupService" in source
        assert "class SkillSyncSpec" in source
