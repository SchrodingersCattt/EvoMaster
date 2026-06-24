from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from matmaster.config.exp import ExpConfig
from matmaster.core.exp import Exp
from matmaster.core.playground import ExecutionEnvironment
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.run_metadata import RunMetadata
from matmaster.types.session import Session
from tests.matmaster.core.conftest import MockLLMProvider


def _make_ctx(
    *,
    workdir: Path,
    execution_workdir: str | None = None,
    session_type: str = "local",
    metadata_source: str = "",
) -> AgentRunContext:
    env_kwargs: dict = {
        "workdir": workdir,
        "session_type": session_type,
        "cache_area": workdir / "cache",
        "session": MagicMock(spec=Session),
        "metadata": RunMetadata(source=metadata_source),
    }
    if execution_workdir is not None:
        env_kwargs["execution_workdir"] = execution_workdir
    return AgentRunContext(
        environment=ExecutionEnvironment(**env_kwargs),
        request=AgentRunRequest(llm_provider=MockLLMProvider()),
    )


def _build_bohrium_tool(ctx: AgentRunContext) -> BohriumTool:
    exp = Exp(ExpConfig(name="test"))
    registry = ToolRegistry()

    exp._init_builtin_tools(ctx, registry, ["Bohrium"])

    tool = registry.get_raw("Bohrium")
    assert isinstance(tool, BohriumTool)
    return tool


def test_bohrium_tool_disallows_local_paths_outside_devshell(tmp_path: Path) -> None:
    tool = _build_bohrium_tool(_make_ctx(workdir=tmp_path))

    assert tool._allow_local_paths is False
    assert tool._workdir == tmp_path


def test_bohrium_tool_allows_local_paths_for_devshell(tmp_path: Path) -> None:
    tool = _build_bohrium_tool(_make_ctx(workdir=tmp_path, metadata_source="devshell"))

    assert tool._allow_local_paths is True
    assert tool._workdir == tmp_path


def test_bohrium_tool_uses_remote_workdir_for_ssh(tmp_path: Path) -> None:
    tool = _build_bohrium_tool(
        _make_ctx(
            workdir=tmp_path,
            execution_workdir="/share/session",
            session_type="ssh",
        )
    )

    assert tool._allow_local_paths is False
    assert tool._workdir == Path("/share/session")
