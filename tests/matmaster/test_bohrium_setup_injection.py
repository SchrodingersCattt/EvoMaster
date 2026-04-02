"""Unit tests for the src-layer BohriumSetupService orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from matmaster.core.bus import MessageBus
from matmaster.integration.bohrium_env import BohriumSetupResult


def _make_service(*, bus: MessageBus | None = None, sessions_service: Any = None):
    from src.services.agent_run_bohrium import BohriumSetupService

    return BohriumSetupService(
        sessions_service=sessions_service or MagicMock(),
        bus=bus,
    )


class TestBohriumSetupServiceOrchestration:
    """Verify run_setup/run_cleanup orchestrate the owned runtime methods."""

    @pytest.mark.asyncio
    async def test_run_setup_loads_credentials_and_delegates(self):
        bus = MessageBus()
        svc = _make_service(bus=bus)
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
        bus = MessageBus()
        svc = _make_service(bus=bus)
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


class TestBohriumSetupServiceLocation:
    """Verify the service now lives in src/services instead of matmaster/integration."""

    def test_integration_init_no_longer_exports_bohrium_setup_service(self):
        init_file = (
            Path(__file__).parent.parent.parent / "matmaster" / "integration" / "__init__.py"
        )
        source = init_file.read_text(encoding="utf-8")
        assert "BohriumSetupService" not in source

    def test_src_agent_run_bohrium_defines_service_and_skill_sync_spec(self):
        service_file = (
            Path(__file__).parent.parent.parent
            / "src"
            / "services"
            / "agent_run_bohrium.py"
        )
        source = service_file.read_text(encoding="utf-8")
        assert "class BohriumSetupService" in source
        assert "class SkillSyncSpec" in source
