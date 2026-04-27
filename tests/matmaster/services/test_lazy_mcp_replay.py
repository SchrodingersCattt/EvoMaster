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

    assert isinstance(svc._active_mcp_servers, dict)
    assert svc._active_mcp_servers == {}


import pytest
from unittest.mock import AsyncMock

from matmaster.types.cancellation import CancellationController
from matmaster.types.events import RunResultEvent
from tests.matmaster.services.test_agent_run_stream import _patched_service


def _make_cancel_token():
    return CancellationController().token


@pytest.mark.asyncio
async def test_run_agent_uses_hot_cache_when_present(monkeypatch):
    """When the hot cache already has a set, no DB rescan is performed."""
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _, __):
        # Helper bypasses __init__, so the field must be set explicitly.
        svc._active_mcp_servers = {"sess-1": {"mat_xrd"}}

        called = {"n": 0}
        original = (
            __import__("src.services.agent_run_service", fromlist=["x"])
            ._resolve_active_mcp_servers_from_events
        )

        def _spy(events, cache_dir, registry):
            called["n"] += 1
            return original(events, cache_dir, registry)

        monkeypatch.setattr(
            "src.services.agent_run_service._resolve_active_mcp_servers_from_events",
            _spy,
        )

        await svc.run_agent(
            session_id="sess-1",
            user_prompt="hi",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
        )

    snapshot = svc._test_fake_exp.last_ctx.run_meta["active_mcp_servers"]
    assert snapshot == frozenset({"mat_xrd"})
    assert isinstance(snapshot, frozenset)
    assert called["n"] == 0  # cache hit -> no DB scan


@pytest.mark.asyncio
async def test_run_agent_record_callback_writes_back_to_hot_cache():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _, __):
        svc._active_mcp_servers = {}

        await svc.run_agent(
            session_id="sess-2",
            user_prompt="hi",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
        )

    record = svc._test_fake_exp.last_ctx.run_meta["record_active_mcp_server"]
    assert callable(record)
    record("mat_sg")
    assert svc._active_mcp_servers["sess-2"] == {"mat_sg"}


@pytest.mark.asyncio
async def test_run_agent_rehydrates_from_db_on_cache_miss(tmp_path, monkeypatch):
    """When the hot cache is empty, run_agent must scan events_table once."""
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "mat_xrd.json").write_text("[]")
    (cache_dir / "mat_sg.json").write_text("[]")

    async with _patched_service([run_result]) as (svc, _, __):
        svc._active_mcp_servers = {}

        # Force exp_config.skills.cache_dir to our tmp cache_dir + an empty skills_root.
        from matmaster.config.exp import ExpConfig, ExpSkillsConfig

        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        cfg = ExpConfig(
            skills=ExpSkillsConfig(
                enabled=True,
                skills_root=str(skills_root),
                cache_dir=str(cache_dir),
                config_dir=str(tmp_path),
                mcp_config_file="mcp_config.json",
                mcp_runtime_file="mcp.yaml",
            )
        )
        monkeypatch.setattr(
            "matmaster.config.loader.load_exp_config", lambda _name: cfg
        )

        # events_table.get_session_events returns persisted events from a prior turn.
        svc._test_events_table.get_session_events = MagicMock(
            return_value=[
                {
                    "type": "assistant_state",
                    "content": {"tool_calls": [{"name": "mat_xrd_read"}]},
                },
                {"type": "tool_call", "tool_name": "mat_sg_build_bulk"},
            ]
        )

        await svc.run_agent(
            session_id="sess-rehydrate",
            user_prompt="hi",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
        )

    assert svc._active_mcp_servers["sess-rehydrate"] == {"mat_xrd", "mat_sg"}
    snapshot = svc._test_fake_exp.last_ctx.run_meta["active_mcp_servers"]
    assert snapshot == frozenset({"mat_xrd", "mat_sg"})
