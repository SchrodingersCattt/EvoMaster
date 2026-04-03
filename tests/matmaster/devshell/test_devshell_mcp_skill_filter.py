"""Lazy MCP: only struct-DB skill registered; MCP tools inject after use_skill get_info."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from matmaster.config.exp import ExpConfig, ExpSkillsConfig, ExpToolsConfig
from matmaster.config.loader import load_base_system_prompt
from matmaster.core.exp import Exp
from matmaster.providers.openai_provider import OpenAIProvider
from matmaster.sessions.local import LocalSession
from matmaster.types.context import PlaygroundContext


def _tool_names(registry: object) -> set[str]:
    return {t.name for t in registry.all_tools}


def test_devshell_mcp_only_struct_db_skill_and_lazy_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    workdir = tmp_path / "ws"
    workdir.mkdir()
    cache_area = workdir / ".cache"
    cache_area.mkdir()

    session = LocalSession(workspace_path=workdir)
    session.open()

    ctx = PlaygroundContext(
        workdir=workdir,
        session_type="local",
        cache_area=cache_area,
        session=session,
        llm_provider=OpenAIProvider(model="gpt-4o-mini", api_key="sk-test"),
        config_dir=None,
        llm_config=None,
        run_meta={"source": "test"},
    )

    exp_cfg = ExpConfig(
        tools=ExpToolsConfig(builtin=["execute_bash"]),
        skills=ExpSkillsConfig(
            enabled=True,
            skills_root=["matmaster/skills/lazymcp/mcp-mat-struct-db"],
            cache_dir="matmaster/cache",
            config_dir="matmaster_config",
            mcp_config_file="mcp_config.json",
            mcp_runtime_file="mcp.yaml",
        ),
        system_prompt=load_base_system_prompt(),
    )
    assert exp_cfg.skills.enabled is True
    assert exp_cfg.skills.skill_names == []
    assert exp_cfg.skills.skills_root == ["matmaster/skills/lazymcp/mcp-mat-struct-db"]

    async def _run() -> None:
        exp = Exp(exp_cfg)
        try:
            runtime = await exp.build_runtime(ctx)
            reg = runtime.spec.tool_registry
            names = _tool_names(reg)
            assert "use_skill" in names
            assert "mat_struct_db_fetch_structures_from_db" not in names

            skills = exp._skill_registry.get_all_skills()
            assert len(skills) == 1
            assert skills[0].meta_info.name == "mcp-mat-struct-db"

            use_skill = next(t for t in reg.all_tools if t.name == "use_skill")
            await use_skill.execute(
                {"skill_name": "mcp-mat-struct-db", "action": "get_info"}
            )
            assert "mat_struct_db_fetch_structures_from_db" in _tool_names(reg)
        finally:
            await exp._run_cleanup_callbacks()

    asyncio.run(_run())


def test_mcp_runtime_patch_limits_mat_sg_lazy_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tool_include_only for mat_sg: only whitelisted tools inject from schema cache."""
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    workdir = tmp_path / "ws"
    workdir.mkdir()
    cache_area = workdir / ".cache"
    cache_area.mkdir()

    session = LocalSession(workspace_path=workdir)
    session.open()

    ctx = PlaygroundContext(
        workdir=workdir,
        session_type="local",
        cache_area=cache_area,
        session=session,
        llm_provider=OpenAIProvider(model="gpt-4o-mini", api_key="sk-test"),
        config_dir=None,
        llm_config=None,
        run_meta={"source": "test"},
    )

    exp_cfg = ExpConfig(
        tools=ExpToolsConfig(builtin=["execute_bash"]),
        skills=ExpSkillsConfig(
            enabled=True,
            skills_root=["matmaster/skills/lazymcp/mcp-mat-sg"],
            mcp_runtime_patch={
                "tool_include_only": {
                    "mat_sg": ["build_surface_slab", "generate_ordered_replicas"],
                },
            },
            cache_dir="matmaster/cache",
            config_dir="matmaster_config",
            mcp_config_file="mcp_config.json",
            mcp_runtime_file="mcp.yaml",
        ),
        system_prompt=load_base_system_prompt(),
    )

    async def _run() -> None:
        exp = Exp(exp_cfg)
        try:
            runtime = await exp.build_runtime(ctx)
            reg = runtime.spec.tool_registry
            assert "mat_sg_build_surface_slab" not in _tool_names(reg)

            use_skill = next(t for t in reg.all_tools if t.name == "use_skill")
            await use_skill.execute({"skill_name": "mcp-mat-sg", "action": "get_info"})
            names = _tool_names(reg)
            assert "mat_sg_build_surface_slab" in names
            assert "mat_sg_generate_ordered_replicas" in names
            assert "mat_sg_get_structure_info" not in names
        finally:
            await exp._run_cleanup_callbacks()

    asyncio.run(_run())
