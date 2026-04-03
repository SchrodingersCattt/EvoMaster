"""Tests for ToolCompiler and ToolCatalog compiler delegation."""

from __future__ import annotations

from typing import Any

from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_compiler import ToolCompiler
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim
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


def _make_topology() -> RuntimeTopology:
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=frozenset(ToolPlane),
    )


class TestToolCompiler:
    def test_compile_builtin_bash(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("execute_bash"),
            _make_topology(),
            source="builtin",
        )

        assert instance.tool_binding.plane == ToolPlane.SESSION_SHELL
        assert instance.tool_spec.effect_level == "local_mutation"
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="session", mode="exclusive"),
        )

    def test_compile_builtin_read_file(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("read_file"),
            _make_topology(),
            source="builtin",
        )

        assert instance.tool_binding.plane == ToolPlane.SESSION_FS
        assert instance.tool_spec.effect_level == "none"
        assert instance.tool_spec.fast_path_eligible is True
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="workspace", mode="shared_read"),
        )

    def test_compile_builtin_web_search(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("mm_web_search"),
            _make_topology(),
            source="builtin",
        )

        assert instance.tool_binding.plane == ToolPlane.EXTERNAL_SERVICE
        assert instance.tool_spec.effect_level == "external_effect"
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="web", mode="counted", max_concurrent=3),
        )

    def test_compile_unknown_tool(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("custom_mcp_tool"),
            _make_topology(),
            source="mcp",
        )

        assert instance.tool_binding.plane == ToolPlane.CONTROL_PLANE
        assert instance.tool_spec.effect_level == "local_mutation"
        assert instance.tool_spec.fast_path_eligible is False
        assert instance.tool_binding.resource_claims == ()
        assert instance.tool_spec.source == "mcp"


class TestToolCompilerMaxResultChars:
    def test_read_file_max_result_chars(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("read_file"), _make_topology(), source="builtin")
        assert instance.tool_spec.max_result_chars == 12000

    def test_execute_bash_max_result_chars(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("execute_bash"), _make_topology(), source="builtin")
        assert instance.tool_spec.max_result_chars == 12000

    def test_glob_max_result_chars(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("glob"), _make_topology(), source="builtin")
        assert instance.tool_spec.max_result_chars == 8000

    def test_web_fetch_max_result_chars(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("web_fetch"), _make_topology(), source="builtin")
        assert instance.tool_spec.max_result_chars == 16000

    def test_unknown_tool_max_result_chars_zero(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("custom"), _make_topology(), source="mcp")
        assert instance.tool_spec.max_result_chars == 0


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


class TestTopologyDependentBinding:
    def test_glob_local_stateless_shared_read(self) -> None:
        """Local + stateless -> glob gets shared_read claim."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("glob"), _make_local_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="session", mode="shared_read"),
        )

    def test_grep_local_stateless_shared_read(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("grep"), _make_local_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="session", mode="shared_read"),
        )

    def test_list_dir_local_stateless_shared_read(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("list_dir"), _make_local_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="session", mode="shared_read"),
        )

    def test_glob_ssh_stays_exclusive(self) -> None:
        """SSH session -> glob stays exclusive even if stateless."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("glob"), _make_ssh_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="session", mode="exclusive"),
        )

    def test_glob_local_no_caps_stays_exclusive(self) -> None:
        """Local but session_capabilities=None -> no relaxation."""
        compiler = ToolCompiler()
        topo = RuntimeTopology(
            session_kind="local",
            control_root="/tmp/c",
            workspace_root="/tmp/w",
        )
        instance = compiler.compile(_FakeTool("glob"), topo, source="builtin")
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="session", mode="exclusive"),
        )

    def test_bash_local_stays_exclusive(self) -> None:
        """execute_bash is never relaxed."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("execute_bash"), _make_local_stateless_topology(), source="builtin"
        )
        assert instance.tool_binding.resource_claims == (
            ResourceClaim(resource="session", mode="exclusive"),
        )

    def test_custom_tool_unaffected(self) -> None:
        """Non-builtin tools are not affected by relaxation."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("my_mcp_tool"), _make_local_stateless_topology(), source="mcp"
        )
        assert instance.tool_binding.resource_claims == ()


class TestFastPathEligibleFix:
    def test_glob_fast_path_eligible(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("glob"), _make_topology(), source="builtin")
        assert instance.tool_spec.fast_path_eligible is True

    def test_grep_fast_path_eligible(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("grep"), _make_topology(), source="builtin")
        assert instance.tool_spec.fast_path_eligible is True

    def test_list_dir_fast_path_eligible(self) -> None:
        compiler = ToolCompiler()
        instance = compiler.compile(_FakeTool("list_dir"), _make_topology(), source="builtin")
        assert instance.tool_spec.fast_path_eligible is True


class TestToolCompilerInputValidator:
    def test_tool_with_validate_input_gets_bound(self) -> None:
        """Tools with validate_input get input_validator on ToolInstance."""

        class _ValidatableTool:
            name = "write_file"
            description = "validatable"
            json_schema: dict[str, Any] = {"type": "object", "properties": {}}

            async def execute(self, arguments: dict[str, Any]) -> ToolResult:
                return ToolResult(content="ok")

            async def validate_input(self, arguments: dict[str, Any]):
                return None

        compiler = ToolCompiler()
        instance = compiler.compile(
            _ValidatableTool(), _make_topology(), source="builtin"
        )
        assert instance.input_validator is not None

    def test_tool_without_validate_input_gets_none(self) -> None:
        """Regular tools get input_validator=None."""
        compiler = ToolCompiler()
        instance = compiler.compile(
            _FakeTool("read_file"), _make_topology(), source="builtin"
        )
        assert instance.input_validator is None


class TestToolCatalogCompilerDelegation:
    def test_get_tool_uses_compiler(self) -> None:
        registry = ToolRegistry()
        tool = _FakeTool("execute_bash")
        registry.register(tool, source="builtin")

        topology = _make_topology()
        compiler = ToolCompiler()
        catalog = ToolCatalog(registry, compiler=compiler, topology=topology)

        instance = catalog.get_tool("execute_bash")
        expected = compiler.compile(tool, topology, source="builtin")

        assert instance is not None
        assert instance.tool_binding == expected.tool_binding
        assert instance.tool_spec == expected.tool_spec

    def test_register_overlay_uses_compiler(self) -> None:
        registry = ToolRegistry()
        topology = _make_topology()
        compiler = ToolCompiler()
        catalog = ToolCatalog(registry, compiler=compiler, topology=topology)

        overlay = _FakeTool("overlay_tool")
        catalog.register_overlay(overlay, source="mcp")
        instance = catalog.get_tool("overlay_tool")

        assert instance is not None
        assert instance.tool_spec.source == "mcp"
        assert instance.tool_binding.plane == ToolPlane.CONTROL_PLANE
        assert instance.tool_binding.binding_key == "control_plane:overlay_tool"
        assert instance.tool_binding.resource_claims == ()
