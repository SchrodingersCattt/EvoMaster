"""Tests for builtin tool ResourceClaim declarations in ToolCatalog.

Verifies that core builtin tools have correct ResourceClaim, ToolPlane,
effect_level, and fast_path_eligible metadata after ToolCatalog.get_tool()
lookup.

Per D-09: builtin tools must declare resource claims so Scheduler
exclusive/shared_read/counted constraints work on real tools.
"""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.tools.tool_catalog import BUILTIN_CLAIMS, BUILTIN_META, ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane


# ── Helpers ──────────────────────────────────────────────


class _MockTool:
    """Minimal tool satisfying Tool Protocol for testing."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"mock tool {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content="mock")


def _make_catalog(*tool_names: str) -> ToolCatalog:
    """Create a ToolCatalog with mock tools registered as builtin."""
    registry = ToolRegistry()
    for name in tool_names:
        registry.register(_MockTool(name), source="builtin")
    return ToolCatalog(registry)


# ── Bash ─────────────────────────────────────────────────


class TestBashClaim:
    def test_execute_bash_has_session_exclusive(self) -> None:
        """execute_bash -> ResourceClaim(resource='session', mode='exclusive')."""
        catalog = _make_catalog("execute_bash")
        instance = catalog.get_tool("execute_bash")

        assert instance is not None
        claims = instance.tool_binding.resource_claims
        assert ResourceClaim(resource="session", mode="exclusive") in claims

    def test_execute_bash_plane_session_shell(self) -> None:
        """execute_bash plane is SESSION_SHELL."""
        catalog = _make_catalog("execute_bash")
        instance = catalog.get_tool("execute_bash")

        assert instance is not None
        assert instance.tool_binding.plane == ToolPlane.SESSION_SHELL


# ── Write ────────────────────────────────────────────────


class TestWriteClaim:
    def test_write_file_has_workspace_exclusive(self) -> None:
        """write_file -> ResourceClaim(resource='workspace', mode='exclusive')."""
        catalog = _make_catalog("write_file")
        instance = catalog.get_tool("write_file")

        assert instance is not None
        claims = instance.tool_binding.resource_claims
        assert ResourceClaim(resource="workspace", mode="exclusive") in claims


# ── Read ─────────────────────────────────────────────────


class TestReadClaim:
    def test_read_file_has_workspace_shared_read(self) -> None:
        """read_file -> ResourceClaim(resource='workspace', mode='shared_read')."""
        catalog = _make_catalog("read_file")
        instance = catalog.get_tool("read_file")

        assert instance is not None
        claims = instance.tool_binding.resource_claims
        assert ResourceClaim(resource="workspace", mode="shared_read") in claims


# ── Edit ─────────────────────────────────────────────────


class TestEditClaim:
    def test_edit_file_has_workspace_exclusive(self) -> None:
        """edit_file -> ResourceClaim(resource='workspace', mode='exclusive')."""
        catalog = _make_catalog("edit_file")
        instance = catalog.get_tool("edit_file")

        assert instance is not None
        claims = instance.tool_binding.resource_claims
        assert ResourceClaim(resource="workspace", mode="exclusive") in claims


# ── Glob/Grep/ListDir ────────────────────────────────────


class TestGlobGrepListDir:
    @pytest.mark.parametrize("tool_name", ["glob", "grep", "list_dir"])
    def test_session_exclusive(self, tool_name: str) -> None:
        """glob/grep/list_dir -> ResourceClaim(resource='session', mode='exclusive')."""
        catalog = _make_catalog(tool_name)
        instance = catalog.get_tool(tool_name)

        assert instance is not None
        claims = instance.tool_binding.resource_claims
        assert ResourceClaim(resource="session", mode="exclusive") in claims


# ── Web Tools ────────────────────────────────────────────


class TestWebTools:
    @pytest.mark.parametrize("tool_name", ["mm_web_search", "web_fetch"])
    def test_web_counted(self, tool_name: str) -> None:
        """mm_web_search/web_fetch -> ResourceClaim(resource='web', mode='counted', max_concurrent=3)."""
        catalog = _make_catalog(tool_name)
        instance = catalog.get_tool(tool_name)

        assert instance is not None
        claims = instance.tool_binding.resource_claims
        assert (
            ResourceClaim(resource="web", mode="counted", max_concurrent=3) in claims
        )


# ── Spawn ────────────────────────────────────────────────


class TestSpawn:
    def test_spawn_counted(self) -> None:
        """spawn -> ResourceClaim(resource='spawn', mode='counted', max_concurrent=2)."""
        catalog = _make_catalog("spawn")
        instance = catalog.get_tool("spawn")

        assert instance is not None
        claims = instance.tool_binding.resource_claims
        assert (
            ResourceClaim(resource="spawn", mode="counted", max_concurrent=2) in claims
        )


# ── Fast Path Eligible ───────────────────────────────────


class TestFastPathEligible:
    def test_read_file_fast_path(self) -> None:
        """read_file -> fast_path_eligible=True, effect_level='pure_read'."""
        catalog = _make_catalog("read_file")
        instance = catalog.get_tool("read_file")

        assert instance is not None
        assert instance.tool_spec.fast_path_eligible is True
        assert instance.tool_spec.effect_level == "none"


class TestEffectLevelConsistency:
    """Builtin effect levels should match the canonical ToolSpec enum."""

    def test_all_effect_levels_are_canonical(self) -> None:
        canonical = {"none", "local_mutation", "external_effect"}

        for tool_name, (_plane, effect_level, _fast, *_rest) in BUILTIN_META.items():
            assert effect_level in canonical, (
                f"{tool_name} has non-canonical effect_level={effect_level!r}"
            )


# ── Unknown/MCP Tool Default ────────────────────────────


class TestUnknownToolNoClaim:
    def test_unknown_tool_empty_claims(self) -> None:
        """Tool not in BUILTIN_CLAIMS -> resource_claims is empty tuple."""
        catalog = _make_catalog("custom_mcp_tool")
        instance = catalog.get_tool("custom_mcp_tool")

        assert instance is not None
        assert instance.tool_binding.resource_claims == ()


# ── Plane Mapping ────────────────────────────────────────


class TestPlaneMapping:
    def test_execute_bash_session_shell(self) -> None:
        """execute_bash plane is SESSION_SHELL."""
        catalog = _make_catalog("execute_bash")
        instance = catalog.get_tool("execute_bash")

        assert instance is not None
        assert instance.tool_binding.plane == ToolPlane.SESSION_SHELL

    def test_read_file_session_fs(self) -> None:
        """read_file plane is SESSION_FS."""
        catalog = _make_catalog("read_file")
        instance = catalog.get_tool("read_file")

        assert instance is not None
        assert instance.tool_binding.plane == ToolPlane.SESSION_FS
