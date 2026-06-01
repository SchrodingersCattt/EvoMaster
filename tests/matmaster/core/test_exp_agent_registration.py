"""Tests for Exp agent tool registration and spawn guards."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from matmaster.config.exp import ExpConfig, ExpToolsConfig
from matmaster.core.exp import Exp
from matmaster.types.session import Session

from .test_exp import _make_ctx


@pytest.mark.asyncio
async def test_build_runtime_registers_agent_by_cc_name(tmp_path: Path) -> None:
    """Agent registers with CC name when enabled in builtin config."""
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["Agent"]),
        )
    )
    ctx = _make_ctx(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session=MagicMock(spec=Session),
        with_llm=True,
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert runtime.kernel_runtime.resources.tool_catalog.get_tool("Agent") is not None


@pytest.mark.asyncio
async def test_build_runtime_hides_agent_when_allow_spawn_false(
    tmp_path: Path,
) -> None:
    """Agent tool is hidden (exposed_to_model=False) when allow_spawn=False."""
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["Agent"]),
        ),
        allow_spawn=False,
    )
    ctx = _make_ctx(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session=MagicMock(spec=Session),
        with_llm=True,
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    tool = runtime.kernel_runtime.resources.tool_catalog.get_tool("Agent")
    assert tool is not None
    assert tool.tool_spec.exposed_to_model is False
