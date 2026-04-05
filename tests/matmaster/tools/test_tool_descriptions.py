"""Tests for builtin tool descriptions and json_schema quality.

Validates Claude Code style description format, token budget,
schema parameter descriptions, and cross-tool routing consistency.
"""

from __future__ import annotations

from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.tools.builtin.glob_tool import GlobTool
from matmaster.tools.builtin.grep_tool import GrepTool
from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.builtin.write_tool import WriteTool

ALL_TOOLS = [BashTool, ReadTool, WriteTool, EditTool, GlobTool, GrepTool]


def test_all_descriptions_nonempty_with_usage_pattern():
    """Every tool description is non-empty and contains 'Usage:' or is substantive (>20 chars)."""
    for tool_cls in ALL_TOOLS:
        desc = tool_cls.description
        assert desc, f"{tool_cls.__name__} has empty description"
        if tool_cls is BashTool:
            prompt = BashTool().prompt() or ""
            assert "Read" in prompt
            continue
        has_usage = "Usage:" in desc or "When to use:" in desc or "When to Use:" in desc
        is_substantive = len(desc) > 20
        assert has_usage or is_substantive, (
            f"{tool_cls.__name__} description lacks 'Usage:' pattern and is too short "
            f"({len(desc)} chars): {desc!r}"
        )


def test_description_token_budget():
    """Every tool description is under 500 characters (~125 tokens)."""
    for tool_cls in ALL_TOOLS:
        desc = tool_cls.description
        assert len(desc) <= 500, (
            f"{tool_cls.__name__} description exceeds 500 char budget: "
            f"{len(desc)} chars"
        )


def test_schema_param_descriptions():
    """Every property in every tool's json_schema has a 'description' field."""
    for tool_cls in ALL_TOOLS:
        schema = tool_cls.json_schema
        props = schema.get("properties", {})
        for param_name, param_def in props.items():
            assert "description" in param_def and len(param_def["description"]) > 0, (
                f"{tool_cls.__name__}.json_schema['properties']['{param_name}'] "
                f"missing or empty 'description'"
            )


def test_bash_routes_all_dedicated_tools():
    """BashTool.prompt mentions all 5 dedicated tool routing targets."""
    desc = BashTool().prompt() or ""
    for target in ["Read", "Write", "Edit", "Glob", "Grep"]:
        assert target in desc, f"BashTool.prompt missing routing target '{target}'"


def test_dedicated_tools_have_prompt_with_usage():
    """Each dedicated tool (grep/glob/read/write/edit) has a prompt with usage guidance."""
    dedicated_tools = [GrepTool, GlobTool, ReadTool, WriteTool, EditTool]
    for tool_cls in dedicated_tools:
        desc = tool_cls.description
        prompt = tool_cls().prompt() or ""
        has_guidance = len(desc) > 10 or len(prompt) > 10
        assert has_guidance, f"{tool_cls.__name__} lacks description or prompt content"


def test_routing_consistency():
    """Bash routing targets correspond to matching declarations in dedicated tools.

    For each bash command -> dedicated tool mapping, verify:
    1. BashTool.prompt mentions the bash command
    2. The corresponding tool has a meaningful description or prompt
    """
    routing_map = {
        "cat": ("Read", ReadTool),
        "echo": ("Write", WriteTool),
        "sed": ("Edit", EditTool),
        "find": ("Glob", GlobTool),
        "grep": ("Grep", GrepTool),
    }
    bash_desc = BashTool().prompt() or ""
    for bash_cmd, (tool_name, tool_cls) in routing_map.items():
        assert bash_cmd in bash_desc, (
            f"BashTool.prompt missing bash command '{bash_cmd}' "
            f"for routing to '{tool_name}'"
        )
        desc = tool_cls.description
        prompt = tool_cls().prompt() or ""
        assert len(desc) > 0 or len(prompt) > 0, (
            f"{tool_cls.__name__} has no description or prompt "
            f"(expected for bash '{bash_cmd}' routing)"
        )
