"""Tests for builtin tool descriptions and json_schema quality.

Validates Claude Code style description format, token budget,
schema parameter descriptions, and cross-tool routing consistency.
"""

from __future__ import annotations

from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.tools.builtin.glob_tool import GlobTool
from matmaster.tools.builtin.grep_tool import GrepTool
from matmaster.tools.builtin.listdir_tool import ListDirTool
from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.builtin.task.task_complete import TaskCompleteTool
from matmaster.tools.builtin.task.task_create import TaskCreateTool
from matmaster.tools.builtin.task.task_get import TaskGetTool
from matmaster.tools.builtin.task.task_list import TaskListTool
from matmaster.tools.builtin.task.task_update import TaskUpdateTool
from matmaster.tools.builtin.write_tool import WriteTool

ALL_TOOLS = [
    BashTool,
    ListDirTool,
    ReadTool,
    WriteTool,
    EditTool,
    GlobTool,
    GrepTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    TaskCompleteTool,
]


def test_all_descriptions_nonempty_with_usage_pattern():
    """Every tool description is non-empty and contains 'Usage:' or is substantive (>50 chars)."""
    for tool_cls in ALL_TOOLS:
        desc = tool_cls.description
        assert desc, f"{tool_cls.__name__} has empty description"
        has_usage = "Usage:" in desc or "When to use:" in desc or "When to Use:" in desc
        is_substantive = len(desc) > 50
        assert has_usage or is_substantive, (
            f"{tool_cls.__name__} description lacks 'Usage:' pattern and is too short "
            f"({len(desc)} chars): {desc!r}"
        )


def test_description_token_budget():
    """Every tool description is under 400 characters (~100 tokens)."""
    for tool_cls in ALL_TOOLS:
        desc = tool_cls.description
        assert len(desc) <= 400, (
            f"{tool_cls.__name__} description exceeds 400 char budget: "
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
    """BashTool.description mentions all 5 dedicated tool routing targets."""
    desc = BashTool.description
    for target in ["read_file", "write_file", "edit_file", "glob", "grep"]:
        assert target in desc, (
            f"BashTool.description missing routing target '{target}'"
        )


def test_dedicated_tools_have_routing_declaration():
    """Each dedicated tool (grep/glob/read/write/edit) has ALWAYS or NEVER routing declaration."""
    dedicated_tools = [GrepTool, GlobTool, ReadTool, WriteTool, EditTool]
    for tool_cls in dedicated_tools:
        desc = tool_cls.description
        assert "ALWAYS" in desc or "NEVER" in desc, (
            f"{tool_cls.__name__}.description lacks ALWAYS/NEVER routing declaration"
        )


def test_routing_consistency():
    """Bash routing targets correspond to matching declarations in dedicated tools.

    For each bash command -> dedicated tool mapping, verify both:
    1. BashTool.description mentions the bash command
    2. The corresponding tool has a routing declaration
    """
    routing_map = {
        "cat": ("read_file", ReadTool),
        "echo": ("write_file", WriteTool),
        "sed": ("edit_file", EditTool),
        "find": ("glob", GlobTool),
        "grep": ("grep", GrepTool),
    }
    bash_desc = BashTool.description
    for bash_cmd, (tool_name, tool_cls) in routing_map.items():
        assert bash_cmd in bash_desc, (
            f"BashTool.description missing bash command '{bash_cmd}' "
            f"for routing to '{tool_name}'"
        )
        tool_desc = tool_cls.description
        assert "ALWAYS" in tool_desc or "NEVER" in tool_desc, (
            f"{tool_cls.__name__}.description lacks routing declaration "
            f"(expected for bash '{bash_cmd}' routing)"
        )
