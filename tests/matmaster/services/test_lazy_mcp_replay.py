"""Tests for cross-turn skill-driven LazyMCP activation in AgentRunService."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from matmaster.types.cancellation import CancellationController
from matmaster.types.events import RunResultEvent, SkillHitEvent
from tests.matmaster.services.test_agent_run_stream import _patched_service


def test_agent_run_service_initializes_active_skills_dict():
    """AgentRunService must hold a session-keyed dict of active skill names."""
    from src.services.agent_run_service import AgentRunService

    svc = AgentRunService.__new__(AgentRunService)
    # Pass MagicMock to short-circuit `sessions_service or get_sessions_service()`
    # so the test does not require a live MySQL connection. This mirrors the
    # pattern used by _patched_service in tests/matmaster/services/test_agent_run_stream.py.
    AgentRunService.__init__(svc, sessions_service=MagicMock())

    assert isinstance(svc._active_skills, dict)
    assert svc._active_skills == {}


def _make_cancel_token():
    return CancellationController().token


class FakeRemoteSkillSession:
    def __init__(self, root: str, files: dict[str, str]) -> None:
        self.remote_user_skills_root = root
        self.remote_project_root = None
        self.local_user_skills_root = None
        self._files = files
        self._cancel_token = None
        self.capabilities = MagicMock()
        self.path_exists = MagicMock(side_effect=self._path_exists)

    def _path_exists(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(
            candidate == path or candidate.startswith(prefix)
            for candidate in self._files
        )

    def exec_bash(self, command: str, timeout: int | None = None) -> dict[str, object]:
        payload = [
            {"path": path, "content": self._files[path]}
            for path in sorted(self._files)
            if path.endswith("/SKILL.md")
        ]
        return {"exit_code": 0, "stdout": json.dumps(payload)}

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        return self._files.get(path, "")


@pytest.mark.asyncio
async def test_run_agent_uses_hot_cache_when_present(monkeypatch):
    """When the hot cache already has a set, no DB rescan is performed."""
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _, __):
        # Helper bypasses __init__, so the field must be set explicitly.
        svc._active_skills = {"sess-1": {"pxrd"}}

        called = {"n": 0}
        original = svc._resolve_active_skill_names

        def _spy(session_id, events_table, exp_config, session=None):
            called["n"] += 1
            return original(session_id, events_table, exp_config, session)

        monkeypatch.setattr(svc, "_resolve_active_skill_names", _spy)

        await svc.run_agent(
            session_id="sess-1",
            user_prompt="hi",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
            invocation_id="inv-hot-cache",
        )

    snapshot = svc._test_fake_exp.last_ctx.run_meta["active_skills"]
    assert snapshot == frozenset({"pxrd"})
    assert isinstance(snapshot, frozenset)
    assert called["n"] == 1
    svc._test_events_table.get_session_events.assert_not_called()


@pytest.mark.asyncio
async def test_run_agent_skill_hit_event_writes_back_to_hot_cache():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service(
        [SkillHitEvent(source="agent", skill_name="test-skill"), run_result]
    ) as (svc, _, __):
        svc._active_skills = {}

        await svc.run_agent(
            session_id="sess-2",
            user_prompt="hi",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
            invocation_id="inv-skill-hit",
        )

    assert "record_active_mcp_server" not in svc._test_fake_exp.last_ctx.run_meta
    assert svc._active_skills["sess-2"] == {"test-skill"}


@pytest.mark.asyncio
async def test_run_agent_rehydrates_from_db_on_cache_miss(tmp_path, monkeypatch):
    """When the hot cache is empty, run_agent must scan skill_hit events once."""
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    async with _patched_service([run_result]) as (svc, _, __):
        svc._active_skills = {}

        # Force exp_config.skills to our tmp skill root.
        from matmaster.config.exp import ExpConfig, ExpSkillsConfig

        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        for skill_name, server_name in (
            ("pxrd", "mat_xrd"),
            ("sg", "mat_sg"),
        ):
            skill_dir = skills_root / skill_name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {skill_name}\ndescription: T\nmcp_server: {server_name}\n---\nbody\n",
                encoding="utf-8",
            )
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
        with monkeypatch.context() as scoped_monkeypatch:
            scoped_monkeypatch.setattr(
                "matmaster.config.loader.load_exp_config", lambda _name: cfg
            )

            svc._test_events_table.get_session_events = MagicMock(
                return_value=[
                    {"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
                    {"id": 2, "type": "skill_hit", "content": {"skill_name": "sg"}},
                    {"id": 3, "type": "tool_call", "tool_name": "mat_ignored_tool"},
                ]
            )

            await svc.run_agent(
                session_id="sess-rehydrate",
                user_prompt="hi",
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode="direct",
                task_id="t1",
                invocation_id="inv-rehydrate",
            )

    assert svc._active_skills["sess-rehydrate"] == {"pxrd", "sg"}
    snapshot = svc._test_fake_exp.last_ctx.run_meta["active_skills"]
    assert snapshot == frozenset({"pxrd", "sg"})


@pytest.mark.asyncio
async def test_run_agent_rehydrates_remote_skill_from_session_root(
    tmp_path,
    monkeypatch,
):
    """Skill-hit replay should include skills exposed by the active SSH session."""
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")
    remote_root = "/remote/user/skills"
    session = FakeRemoteSkillSession(
        remote_root,
        {
            f"{remote_root}/remote-skill/SKILL.md": (
                "---\n"
                "name: remote-skill\n"
                "description: Remote user skill\n"
                "mcp_server: remote_mcp\n"
                "---\n"
                "body\n"
            ),
        },
    )

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    async with _patched_service([run_result]) as (svc, _, __):
        svc._active_skills = {}
        svc._test_pg_ctx.session = session

        from matmaster.config.exp import ExpConfig, ExpSkillsConfig

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
        with monkeypatch.context() as scoped_monkeypatch:
            scoped_monkeypatch.setattr(
                "matmaster.config.loader.load_exp_config", lambda _name: cfg
            )
            svc._test_events_table.get_session_events = MagicMock(
                return_value=[
                    {
                        "id": 1,
                        "type": "skill_hit",
                        "content": {"skill_name": "remote-skill"},
                    },
                ]
            )

            await svc.run_agent(
                session_id="sess-remote-rehydrate",
                user_prompt="hi",
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode="direct",
                task_id="t1",
                invocation_id="inv-remote-rehydrate",
            )

    assert svc._active_skills["sess-remote-rehydrate"] == {"remote-skill"}
    snapshot = svc._test_fake_exp.last_ctx.run_meta["active_skills"]
    assert snapshot == frozenset({"remote-skill"})
