"""Tests for ContextBuilder -- sectioned system prompt assembly.

Covers: section ordering, disabled sections, mode contracts,
identity customization, optional sections, tool/skill integration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from matmaster.assembly.context_builder import ContextBuilder
from matmaster.assembly.tool_registry import Tool, ToolRegistry
from matmaster.types.context import PlaygroundContext


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockTool:
    """Minimal Tool implementation for testing."""

    def __init__(self, name: str, description: str = "A test tool") -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, arguments: dict[str, Any]) -> str:
        return "ok"


class MockSkillRegistry:
    """Mock skill registry with get_meta_info_context()."""

    def get_meta_info_context(self) -> str:
        return "Skill A: does X\nSkill B: does Y"


@pytest.fixture
def ctx() -> PlaygroundContext:
    """Minimal PlaygroundContext for testing."""
    return PlaygroundContext(
        workdir=Path("/tmp/test"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    )


@pytest.fixture
def tool_registry() -> ToolRegistry:
    """ToolRegistry with no tools registered."""
    return ToolRegistry()


@pytest.fixture
def builder() -> ContextBuilder:
    return ContextBuilder()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_default_sections(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """Build with identity and tool_registry only -- output contains identity
    section header and tools section, separated by SEPARATOR."""
    result = builder.build(ctx, tool_registry)
    assert "# Identity" in result
    assert ContextBuilder.SEPARATOR in result


def test_section_order_fixed(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """All sections enabled -- fixed order identity < mode_contract < skills
    < tools < memory < task."""
    reg = ToolRegistry()
    reg.register(MockTool("t1"))

    result = builder.build(
        ctx,
        reg,
        skill_registry=MockSkillRegistry(),
        memory_context="some memory",
        task_context="some task",
    )

    idx_identity = result.index("# Identity")
    idx_mode = result.index("# Mode Contract")
    idx_skills = result.index("# Skills")
    idx_tools = result.index("# Available Tools")
    idx_memory = result.index("# Memory")
    idx_task = result.index("# Task Context")

    assert idx_identity < idx_mode < idx_skills < idx_tools < idx_memory < idx_task


def test_disable_section(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """disabled_sections={'tools'} -- tools section absent."""
    result = builder.build(ctx, tool_registry, disabled_sections={"tools"})
    assert "# Available Tools" not in result


def test_disable_multiple_sections(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """disabled_sections={'skills', 'memory'} -- neither appears."""
    result = builder.build(
        ctx,
        tool_registry,
        skill_registry=MockSkillRegistry(),
        memory_context="mem",
        disabled_sections={"skills", "memory"},
    )
    assert "# Skills" not in result
    assert "# Memory" not in result


def test_direct_mode_contract(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """mode='direct' -- mode_contract section contains 'direct' description."""
    result = builder.build(ctx, tool_registry, mode="direct")
    assert "# Mode Contract" in result
    assert "direct execution mode" in result.lower()


def test_planner_mode_contract(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """mode='planner' -- mode_contract section contains 'planner' description,
    differs from direct."""
    direct_result = builder.build(ctx, tool_registry, mode="direct")
    planner_result = builder.build(ctx, tool_registry, mode="planner")

    assert "# Mode Contract" in planner_result
    assert "planner mode" in planner_result.lower()
    # Content must differ
    assert direct_result != planner_result


def test_identity_custom(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """identity='Custom Identity' -- output contains that text."""
    result = builder.build(ctx, tool_registry, identity="Custom Identity")
    assert "Custom Identity" in result


def test_tools_section_lists_tool_names(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """ToolRegistry with 2 tools -- tools section lists both names."""
    reg = ToolRegistry()
    reg.register(MockTool("file_reader", "Reads files"))
    reg.register(MockTool("web_search", "Searches the web"))

    result = builder.build(ctx, reg)
    assert "file_reader" in result
    assert "web_search" in result
    assert "Reads files" in result
    assert "Searches the web" in result


def test_memory_section_included_when_provided(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """memory_context='Previous conversation summary' -- output contains it."""
    result = builder.build(
        ctx, tool_registry, memory_context="Previous conversation summary"
    )
    assert "# Memory" in result
    assert "Previous conversation summary" in result


def test_task_section_included_when_provided(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """task_context='User task details' -- output contains it."""
    result = builder.build(ctx, tool_registry, task_context="User task details")
    assert "# Task Context" in result
    assert "User task details" in result


def test_empty_optional_sections_omitted(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """No memory_context or task_context -- neither section appears."""
    result = builder.build(ctx, tool_registry)
    assert "# Memory" not in result
    assert "# Task Context" not in result


def test_skills_section_from_registry(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """Mock skill_registry with get_meta_info_context() -- output contains
    skill descriptions."""
    result = builder.build(
        ctx, tool_registry, skill_registry=MockSkillRegistry()
    )
    assert "# Skills" in result
    assert "Skill A: does X" in result
    assert "Skill B: does Y" in result
