"""Tests for PaperSearchTool (mat_sn MCP facade)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from matmaster.tools.builtin.paper_search_tool import PaperSearchTool
from matmaster.tools.tool_result import ToolResult


class TestPaperSearchMetadata:
    def test_name(self) -> None:
        assert PaperSearchTool.name == "PaperSearch"


class TestPaperSearchExecution:
    def test_slims_payload_and_strips_junk(self) -> None:
        raw = {
            "data": [
                {
                    "enName": "Hello",
                    "paperUrl": "https://example.com/p",
                    "doi": "10.1000/test",
                    "authors": ["A", "B"],
                    "coverDateStart": "2021-06-01",
                    "enAbstract": "x" * 600,
                    "noise": {"nested": True},
                }
            ]
        }
        connector = AsyncMock()
        connector.call_tool.return_value = [
            {"text": json.dumps(raw, ensure_ascii=False)}
        ]

        tool = PaperSearchTool(connector=connector, mcp_config={})
        result = asyncio.run(
            tool.execute(
                {
                    "words": ["test"],
                    "question": "What is the effect?",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        payload = json.loads(result.content)
        assert len(payload["data"]) == 1
        row = payload["data"][0]
        assert row["enName"] == "Hello"
        assert "noise" not in row
        assert len(row["enAbstract"]) < 600
        connector.call_tool.assert_awaited_once()
        ca = connector.call_tool.await_args
        assert ca[0][0] == "mat_sn"
        assert ca[0][1] == "search-papers-enhanced"
        assert ca[0][2]["words"] == ["test"]
        assert ca[0][2]["question"] == "What is the effect?"

    def test_validation_empty_words(self) -> None:
        connector = AsyncMock()
        tool = PaperSearchTool(connector=connector, mcp_config={})
        result = asyncio.run(tool.execute({"words": [], "question": "q"}))
        assert result.status == "error"
        connector.call_tool.assert_not_called()
