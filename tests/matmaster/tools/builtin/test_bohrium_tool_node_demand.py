from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_result import ToolResult


@pytest.mark.asyncio
async def test_query_does_not_acquire_node(tmp_path) -> None:
    acquirer = MagicMock()
    acquirer.ensure_ready = AsyncMock()
    tool = BohriumTool(workdir=tmp_path, node_acquirer=acquirer)

    with patch.object(
        tool,
        "_execute",
        return_value=ToolResult(status="success", content="queried"),
    ):
        result = await tool.execute_with_context(
            {"action": "query", "job_id": "job-1"},
            None,
        )

    assert result == ToolResult(status="success", content="queried")
    acquirer.ensure_ready.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_acquires_node(tmp_path) -> None:
    acquirer = MagicMock()
    acquirer.ensure_ready = AsyncMock(return_value=MagicMock())
    tool = BohriumTool(workdir=tmp_path, node_acquirer=acquirer)

    with patch.object(
        tool,
        "_execute",
        return_value=ToolResult(status="success", content="submitted"),
    ):
        result = await tool.execute_with_context(
            {"action": "submit"},
            None,
        )

    assert result == ToolResult(status="success", content="submitted")
    acquirer.ensure_ready.assert_awaited_once()
