"""Tests for ContextBuilder -- sectioned system prompt assembly.

Covers: section ordering, disabled sections, mode contracts,
identity customization, optional sections, tool/skill integration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from matmaster.core.context_builder import ContextBuilder
from matmaster.tools.tool_registry import ToolRegistry
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


def test_build_no_args_produces_empty(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """Build with all defaults (empty system_prompt, empty identity, no tools)
    produces empty string -- no sections to include."""
    result = builder.build(ctx, tool_registry)
    assert result == ""


def test_build_with_identity_only(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """Passing identity produces only the identity section."""
    result = builder.build(ctx, tool_registry, identity="I am Mat Master.")
    assert "# Identity" in result
    assert "I am Mat Master." in result
    assert "# System" not in result


def test_section_order_fixed(builder: ContextBuilder, ctx: PlaygroundContext) -> None:
    """All sections enabled -- fixed order system_prompt < identity < skills
    < tools < memory < task."""
    reg = ToolRegistry()
    reg.register(MockTool("t1"))

    result = builder.build(
        ctx,
        reg,
        system_prompt="Test system prompt",
        identity="Test identity",
        skill_registry=MockSkillRegistry(),
        memory_context="some memory",
        task_context="some task",
    )

    idx_system = result.index("# System")
    idx_identity = result.index("# Identity")
    idx_skills = result.index("# Skills")
    idx_tools = result.index("# Available Tools")
    idx_memory = result.index("# Memory")
    idx_task = result.index("# Task Context")

    assert idx_system < idx_identity < idx_skills < idx_tools < idx_memory < idx_task


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


def test_identity_custom(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """identity='Custom Identity' -- output contains that text."""
    result = builder.build(ctx, tool_registry, identity="Custom Identity")
    assert "Custom Identity" in result


def test_strip_trailing_newlines(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """TOML multi-line strings may have trailing newlines -- stripped."""
    result = builder.build(
        ctx,
        tool_registry,
        system_prompt="\nBase prompt\n",
        identity="\nMat Master\n",
    )
    assert "# System\n\nBase prompt\n\n---" in result
    assert "# Identity\n\nMat Master" in result


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
    result = builder.build(ctx, tool_registry, skill_registry=MockSkillRegistry())
    assert "# Skills" in result
    assert "Skill A: does X" in result
    assert "Skill B: does Y" in result


def test_build_with_system_prompt_only(
    builder: ContextBuilder, ctx: PlaygroundContext, tool_registry: ToolRegistry
) -> None:
    """Passing system_prompt produces only the system section."""
    result = builder.build(ctx, tool_registry, system_prompt="Base persona.")
    assert "# System" in result
    assert "Base persona." in result
    assert "# Identity" not in result
