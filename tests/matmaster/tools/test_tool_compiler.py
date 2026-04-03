"""Tests for ToolCompiler after self-describing metadata migration."""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.tools.tool_compiler import ToolCompiler
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext, ToolSpec
from matmaster.types.topology import RuntimeTopology, SessionCapabilities, ToolPlane


class _FakeTool:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"fake tool {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content=f"executed {self._name}")


class _SelfDescribingTool(_FakeTool):
    resource_claims = (ResourceClaim(resource="workspace", mode="shared_read"),)
    capabilities = frozenset({"workspace.read"})
    effect_level = "none"
    fast_path_eligible = True
    max_result_chars = 12000
    plane = ToolPlane.SESSION_FS
    state_mode = "persistent"
    stop_mode = "best_effort"
    exposed_to_model = True


class _ContextAwareTool(_SelfDescribingTool):
    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext,
    ) -> ToolResult:
        return ToolResult(content=f"ctx:{arguments.get('x', '')}")


class _ValidatableTool(_FakeTool):
    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: Any | None = None,
    ) -> None:
        return None


def _make_topology() -> RuntimeTopology:
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=frozenset(ToolPlane),
    )


def _make_local_stateless_topology() -> RuntimeTopology:
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=frozenset(ToolPlane),
        session_capabilities=SessionCapabilities(
            shell_persistence="stateless",
            file_ops="native",
        ),
    )


def _make_ssh_stateless_topology() -> RuntimeTopology:
    return RuntimeTopology(
        session_kind="ssh",
        control_root="/tmp/control",
        workspace_root="/remote/workspace",
        active_planes=frozenset(ToolPlane),
        session_capabilities=SessionCapabilities(
            shell_persistence="stateless",
            file_ops="sftp",
        ),
    )


class TestToolCompiler:
    def test_compile_self_describing_tool(self) -> None:
        compiler = ToolCompiler()
        tool = _SelfDescribingTool("my_read")

        instance = compiler.compile(tool, _make_topology(), source="builtin")

        assert instance.tool_binding.plane == ToolPlane.SESSION_FS
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="workspace", mode="shared_read"),
        )
        assert instance.tool_binding.state_mode == "persistent"
        assert instance.tool_binding.stop_mode == "best_effort"
        assert instance.tool_spec == ToolSpec(
            tool_name="my_read",
            description="fake tool my_read",
            args_schema={"type": "object", "properties": {}},
            source="builtin",
            capabilities=frozenset({"workspace.read"}),
            effect_level="none",
            fast_path_eligible=True,
            max_result_chars=12000,
            exposed_to_model=True,
        )

    def test_compile_minimal_tool_uses_defaults(self) -> None:
        compiler = ToolCompiler()

        instance = compiler.compile(_FakeTool("unknown"), _make_topology(), source="mcp")

        assert instance.tool_binding.plane == ToolPlane.CONTROL_PLANE
        assert instance.tool_binding.resource_claims == ()
        assert instance.tool_binding.state_mode == "stateless"
        assert instance.tool_binding.stop_mode == "cancellable"
        assert instance.tool_spec.effect_level == "local_mutation"
        assert instance.tool_spec.fast_path_eligible is False
        assert instance.tool_spec.max_result_chars == 0
        assert instance.tool_spec.capabilities == frozenset()

    @pytest.mark.asyncio
    async def test_compile_prefers_execute_with_context(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _ContextAwareTool("ctx_tool"),
            _make_topology(),
            source="builtin",
        )

        result = await instance.tool_executor(
            {"x": "hello"},
            ToolExecutionContext(),
        )

        assert isinstance(result, ToolResult)
        assert result.content == "ctx:hello"

    def test_tool_with_validate_input_gets_bound(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _ValidatableTool("validatable"),
            _make_topology(),
            source="builtin",
        )

        assert instance.input_validator is not None

    def test_tool_without_validate_input_gets_none(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("plain"), _make_topology(), source="builtin")

        assert instance.input_validator is None


class TestTopologyDependentBinding:
    @pytest.mark.parametrize("tool_name", ["list_dir", "glob", "grep"])
    def test_local_stateless_relaxes_shell_readers(self, tool_name: str) -> None:
        compiler = ToolCompiler()
        tool = _SelfDescribingTool(tool_name)
        tool.resource_claims = (ResourceClaim(resource="session", mode="exclusive"),)
        tool.plane = ToolPlane.SESSION_SHELL

        instance = compiler.compile(tool, _make_local_stateless_topology(), source="builtin")

        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="session", mode="shared_read"),
        )

    @pytest.mark.parametrize("tool_name", ["list_dir", "glob", "grep"])
    def test_non_local_sessions_do_not_relax(self, tool_name: str) -> None:
        compiler = ToolCompiler()
        tool = _SelfDescribingTool(tool_name)
        tool.resource_claims = (ResourceClaim(resource="session", mode="exclusive"),)
        tool.plane = ToolPlane.SESSION_SHELL

        instance = compiler.compile(tool, _make_ssh_stateless_topology(), source="builtin")

        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="session", mode="exclusive"),
        )
