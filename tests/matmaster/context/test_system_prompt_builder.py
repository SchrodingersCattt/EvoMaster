"""Tests for SystemPromptBuilder -- sectioned system prompt assembly.

Covers: section ordering, disabled sections, mode contracts,
identity customization, optional sections, skill integration,
generic tools section (function calling guidance).
"""

from __future__ import annotations

import pytest

from matmaster.context.system_prompt import SystemPromptBuilder

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockSkillRegistry:
    """Mock skill registry with get_meta_info_context()."""

    def get_meta_info_context(self) -> str:
        return "Skill A: does X\nSkill B: does Y"


@pytest.fixture
def builder() -> SystemPromptBuilder:
    return SystemPromptBuilder()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_no_args_produces_tools_only(builder: SystemPromptBuilder) -> None:
    """Build with all defaults (empty system_prompt, empty identity) produces
    only the generic tools section."""
    result = builder.build_system_prompt()
    assert "function calling" in result
    assert "# Tools" in result


def test_build_with_identity_only(builder: SystemPromptBuilder) -> None:
    """Passing identity produces identity + tools sections."""
    result = builder.build_system_prompt(identity="I am Mat Master.")
    assert "# Identity" in result
    assert "I am Mat Master." in result
    assert "# System" not in result


def test_section_order_fixed(builder: SystemPromptBuilder) -> None:
    """All sections enabled -- fixed order system_prompt < identity < skills
    < tools < memory < task."""
    result = builder.build_system_prompt(
        system_prompt="Test system prompt",
        identity="Test identity",
        skill_registry=MockSkillRegistry(),
        memory_context="some memory",
        task_context="some task",
    )

    idx_system = result.index("# System")
    idx_identity = result.index("# Identity")
    idx_skills = result.index("# Skills")
    idx_tools = result.index("# Tools")
    idx_memory = result.index("# Memory")
    idx_task = result.index("# Task Context")

    assert idx_system < idx_identity < idx_skills < idx_tools < idx_memory < idx_task


def test_disable_section(builder: SystemPromptBuilder) -> None:
    """disabled_sections={'tools'} -- tools section absent."""
    result = builder.build_system_prompt(disabled_sections={"tools"})
    assert "# Tools" not in result


def test_disable_multiple_sections(builder: SystemPromptBuilder) -> None:
    """disabled_sections={'skills', 'memory'} -- neither appears."""
    result = builder.build_system_prompt(
        skill_registry=MockSkillRegistry(),
        memory_context="mem",
        disabled_sections={"skills", "memory"},
    )
    assert "# Skills" not in result
    assert "# Memory" not in result


def test_identity_custom(builder: SystemPromptBuilder) -> None:
    """identity='Custom Identity' -- output contains that text."""
    result = builder.build_system_prompt(identity="Custom Identity")
    assert "Custom Identity" in result


def test_strip_trailing_newlines(builder: SystemPromptBuilder) -> None:
    """TOML multi-line strings may have trailing newlines -- stripped."""
    result = builder.build_system_prompt(
        system_prompt="\nBase prompt\n",
        identity="\nMat Master\n",
    )
    assert "# System\n\nBase prompt\n\n---" in result
    assert "# Identity\n\nMat Master" in result


def test_tools_section_generic_guidance(builder: SystemPromptBuilder) -> None:
    """Tools section provides generic function calling guidance,
    not per-tool enumeration."""
    result = builder.build_system_prompt()
    assert "function calling" in result
    assert "function definitions" in result
    # Must NOT enumerate individual tools
    assert "file_reader" not in result
    assert "tool.name" not in result


def test_memory_section_included_when_provided(builder: SystemPromptBuilder) -> None:
    """memory_context='Previous conversation summary' -- output contains it."""
    result = builder.build_system_prompt(memory_context="Previous conversation summary")
    assert "# Memory" in result
    assert "Previous conversation summary" in result


def test_task_section_included_when_provided(builder: SystemPromptBuilder) -> None:
    """task_context='User task details' -- output contains it."""
    result = builder.build_system_prompt(task_context="User task details")
    assert "# Task Context" in result
    assert "User task details" in result


def test_empty_optional_sections_omitted(builder: SystemPromptBuilder) -> None:
    """No memory_context or task_context -- neither section appears.
    Tools section still present (always shown)."""
    result = builder.build_system_prompt()
    assert "# Memory" not in result
    assert "# Task Context" not in result
    assert "# Tools" in result


def test_skills_section_from_registry(builder: SystemPromptBuilder) -> None:
    """Mock skill_registry with get_meta_info_context() -- output contains
    skill descriptions."""
    result = builder.build_system_prompt(skill_registry=MockSkillRegistry())
    assert "# Skills" in result
    assert "Skill A: does X" in result
    assert "Skill B: does Y" in result


def test_build_with_system_prompt_only(builder: SystemPromptBuilder) -> None:
    """Passing system_prompt produces system + tools sections."""
    result = builder.build_system_prompt(system_prompt="Base persona.")
    assert "# System" in result
    assert "Base persona." in result
    assert "# Identity" not in result


def test_build_method_removed(builder: SystemPromptBuilder) -> None:
    assert not hasattr(builder, "build")
