"""Tests for ContextBuilder -- sectioned system prompt assembly.

Covers: section ordering, disabled sections, mode contracts,
identity customization, optional sections, skill integration,
generic tools section (function calling guidance).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.core.context_builder import ContextBuilder
from matmaster.types.context import PlaygroundContext

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


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
def builder() -> ContextBuilder:
    return ContextBuilder()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_no_args_produces_tools_only(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """Build with all defaults (empty system_prompt, empty identity) produces
    only the generic tools section."""
    result = builder.build_system_prompt(ctx)
    assert "function calling" in result
    assert "# Tools" in result


def test_build_with_identity_only(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """Passing identity produces identity + tools sections."""
    result = builder.build_system_prompt(ctx, identity="I am Mat Master.")
    assert "# Identity" in result
    assert "I am Mat Master." in result
    assert "# System" not in result


def test_section_order_fixed(builder: ContextBuilder, ctx: PlaygroundContext) -> None:
    """All sections enabled -- fixed order system_prompt < identity < skills
    < tools < memory < task."""
    result = builder.build_system_prompt(
        ctx,
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


def test_disable_section(builder: ContextBuilder, ctx: PlaygroundContext) -> None:
    """disabled_sections={'tools'} -- tools section absent."""
    result = builder.build_system_prompt(ctx, disabled_sections={"tools"})
    assert "# Tools" not in result


def test_disable_multiple_sections(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """disabled_sections={'skills', 'memory'} -- neither appears."""
    result = builder.build_system_prompt(
        ctx,
        skill_registry=MockSkillRegistry(),
        memory_context="mem",
        disabled_sections={"skills", "memory"},
    )
    assert "# Skills" not in result
    assert "# Memory" not in result


def test_identity_custom(builder: ContextBuilder, ctx: PlaygroundContext) -> None:
    """identity='Custom Identity' -- output contains that text."""
    result = builder.build_system_prompt(ctx, identity="Custom Identity")
    assert "Custom Identity" in result


def test_strip_trailing_newlines(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """TOML multi-line strings may have trailing newlines -- stripped."""
    result = builder.build_system_prompt(
        ctx,
        system_prompt="\nBase prompt\n",
        identity="\nMat Master\n",
    )
    assert "# System\n\nBase prompt\n\n---" in result
    assert "# Identity\n\nMat Master" in result


def test_tools_section_generic_guidance(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """Tools section provides generic function calling guidance,
    not per-tool enumeration."""
    result = builder.build_system_prompt(ctx)
    assert "function calling" in result
    assert "function definitions" in result
    # Must NOT enumerate individual tools
    assert "file_reader" not in result
    assert "tool.name" not in result


def test_memory_section_included_when_provided(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """memory_context='Previous conversation summary' -- output contains it."""
    result = builder.build_system_prompt(
        ctx, memory_context="Previous conversation summary"
    )
    assert "# Memory" in result
    assert "Previous conversation summary" in result


def test_task_section_included_when_provided(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """task_context='User task details' -- output contains it."""
    result = builder.build_system_prompt(ctx, task_context="User task details")
    assert "# Task Context" in result
    assert "User task details" in result


def test_empty_optional_sections_omitted(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """No memory_context or task_context -- neither section appears.
    Tools section still present (always shown)."""
    result = builder.build_system_prompt(ctx)
    assert "# Memory" not in result
    assert "# Task Context" not in result
    assert "# Tools" in result


def test_skills_section_from_registry(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """Mock skill_registry with get_meta_info_context() -- output contains
    skill descriptions."""
    result = builder.build_system_prompt(ctx, skill_registry=MockSkillRegistry())
    assert "# Skills" in result
    assert "Skill A: does X" in result
    assert "Skill B: does Y" in result


def test_build_with_system_prompt_only(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """Passing system_prompt produces system + tools sections."""
    result = builder.build_system_prompt(ctx, system_prompt="Base persona.")
    assert "# System" in result
    assert "Base persona." in result
    assert "# Identity" not in result


def test_backward_compat_tool_registry_param(
    builder: ContextBuilder, ctx: PlaygroundContext
) -> None:
    """Passing tool_registry positional arg is accepted but ignored."""
    result = builder.build_system_prompt(ctx, "some_registry_value", system_prompt="Test.")
    assert "# System" in result
    assert "function calling" in result


def test_build_method_removed(builder: ContextBuilder) -> None:
    assert not hasattr(builder, "build")


def test_build_user_request_with_text_only(builder: ContextBuilder) -> None:
    assert builder.build_user_request(user_text="run PXRD", attachments=None) == "run PXRD"


def test_build_user_request_with_attachments(builder: ContextBuilder) -> None:
    result = builder.build_user_request(
        user_text="analyze",
        attachments="[Available attachments]\nfile_1 data.csv https://oss/data.csv",
    )

    assert result == (
        "analyze\n\n"
        "[Available attachments]\n"
        "file_1 data.csv https://oss/data.csv"
    )


def test_build_user_request_empty_text_returns_attachment_block(
    builder: ContextBuilder,
) -> None:
    result = builder.build_user_request(
        user_text="",
        attachments="[Available attachments]\nfile_1 data.csv https://oss/data.csv",
    )

    assert result == "[Available attachments]\nfile_1 data.csv https://oss/data.csv"


def test_build_compact_bundle_orders_non_empty_sections(
    builder: ContextBuilder,
) -> None:
    result = builder.build_compact_bundle(
        summary="User asked for PXRD refinement.",
        rehydrated_context="<attachments>\nfile_1 a.csv https://oss/a.csv\n</attachments>",
        continuation_instruction="继续任务。",
    )

    assert result.startswith("以下是先前对话的压缩摘要")
    assert result.index("<previous_session_summary>") < result.index(
        "<rehydrated_context>"
    )
    assert result.index("<rehydrated_context>") < result.index(
        "<continuation_instruction>"
    )
    assert "User asked for PXRD refinement." in result
    assert "file_1 a.csv" in result


def test_build_compact_bundle_omits_empty_rehydrated_context(
    builder: ContextBuilder,
) -> None:
    result = builder.build_compact_bundle(
        summary="summary",
        rehydrated_context="",
        continuation_instruction=None,
    )

    assert "<previous_session_summary>" in result
    assert "<rehydrated_context>" not in result
    assert "<continuation_instruction>" in result
    assert "不要向用户复述上述摘要" in result
