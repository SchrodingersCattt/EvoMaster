"""Tests for cross-turn LazyMCP activation in AgentRunService."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_agent_run_service_initializes_active_mcp_servers_dict():
    """AgentRunService must hold a session-keyed dict of active mcp servers."""
    from src.services.agent_run_service import AgentRunService

    svc = AgentRunService.__new__(AgentRunService)
    # Pass MagicMock to short-circuit `sessions_service or get_sessions_service()`
    # so the test does not require a live MySQL connection. This mirrors the
    # pattern used by _patched_service in tests/matmaster/services/test_agent_run_stream.py.
    AgentRunService.__init__(svc, sessions_service=MagicMock())

    assert hasattr(svc, "_active_mcp_servers")
    assert isinstance(svc._active_mcp_servers, dict)
    assert svc._active_mcp_servers == {}
