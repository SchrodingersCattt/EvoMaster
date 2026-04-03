"""Tests for builtin tool input validators (WriteTool, EditTool)."""

from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.tools.builtin.write_tool import WriteTool
from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.types.tool_decision import ToolDecision


class TestWriteToolValidator:
    @pytest.mark.asyncio
    async def test_deny_empty_path(self) -> None:
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "", "content": "x"})
        assert result is not None
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_deny_path_outside_workdir(self) -> None:
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "/etc/passwd", "content": "x"})
        assert result is not None
        assert result.decision == "deny"
        assert "outside workspace" in result.reason

    @pytest.mark.asyncio
    async def test_deny_same_prefix_different_dir(self) -> None:
        """'/workspace_evil/f.txt' must NOT pass for workdir='/workspace'."""
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "/workspace_evil/f.txt", "content": "x"})
        assert result is not None
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_allow_path_inside_workdir(self) -> None:
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "/workspace/src/main.py", "content": "x"})
        assert result is None

    @pytest.mark.asyncio
    async def test_deny_when_no_workdir(self) -> None:
        """Fail closed: workdir=None -> deny (safety boundary)."""
        tool = WriteTool()
        result = await tool.validate_input({"file_path": "/anywhere/file.txt", "content": "x"})
        assert result is not None
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_deny_traversal_escaping_workdir(self) -> None:
        tool = WriteTool(workdir=Path("/workspace"))
        result = await tool.validate_input({"file_path": "/workspace/../etc/passwd", "content": "x"})
        assert result is not None
        assert result.decision == "deny"


class TestEditToolValidator:
    @pytest.mark.asyncio
    async def test_deny_empty_old_str(self) -> None:
        tool = EditTool()
        result = await tool.validate_input(
            {"file_path": "f.py", "old_str": "", "new_str": "x"}
        )
        assert result is not None
        assert result.decision == "deny"
        assert "empty" in result.reason

    @pytest.mark.asyncio
    async def test_deny_identical_strings(self) -> None:
        tool = EditTool()
        result = await tool.validate_input(
            {"file_path": "f.py", "old_str": "same", "new_str": "same"}
        )
        assert result is not None
        assert result.decision == "deny"
        assert "identical" in result.reason

    @pytest.mark.asyncio
    async def test_allow_valid_edit(self) -> None:
        tool = EditTool()
        result = await tool.validate_input(
            {"file_path": "f.py", "old_str": "old", "new_str": "new"}
        )
        assert result is None
