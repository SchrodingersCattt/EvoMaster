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
                    "paperId": "812702945886863360",
                    "doi": "10.1000/test",
                    "authors": ["A", "B"],
                    "coverDateStart": "2021-06-01",
                    "publicationEnName": "Test Journal",
                    "impactFactor": 3.1,
                    "citationNums": 42,
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
        assert row["publicationEnName"] == "Test Journal"
        assert row["impactFactor"] == 3.1
        assert row["citationNums"] == 42
        assert "paperUrl" not in row
        assert "paperId" not in row
        assert "noise" not in row
        assert len(row["enAbstract"]) < 600
        connector.call_tool.assert_awaited_once()
        ca = connector.call_tool.await_args
        assert ca[0][0] == "mat_sn"
        assert ca[0][1] == "search-papers-enhanced"
        assert ca[0][2]["words"] == ["test"]
        assert ca[0][2]["question"] == "What is the effect?"

    def test_prefers_zh_when_en_title_missing(self) -> None:
        raw = {
            "data": [
                {
                    "zhName": "仅中文标题",
                    "zhAbstract": "中文摘要一段",
                    "doi": "10.1/x",
                    "paperUrl": "https://doi.org/10.1/x",
                }
            ]
        }
        connector = AsyncMock()
        connector.call_tool.return_value = [
            {"text": json.dumps(raw, ensure_ascii=False)}
        ]
        tool = PaperSearchTool(connector=connector, mcp_config={})
        result = asyncio.run(tool.execute({"words": ["x"], "question": "y?"}))
        row = json.loads(result.content)["data"][0]
        assert row["enName"] == "仅中文标题"
        assert "zhName" not in row
        assert row["enAbstract"].startswith("中文摘要")
        assert "zhAbstract" not in row
        assert row["doi"] == "10.1/x"
        assert "paperUrl" not in row

    def test_keeps_zero_citations(self) -> None:
        raw = {
            "data": [
                {
                    "enName": "T",
                    "enAbstract": "a",
                    "doi": "10.1/z",
                    "citationNums": 0,
                }
            ]
        }
        connector = AsyncMock()
        connector.call_tool.return_value = [
            {"text": json.dumps(raw, ensure_ascii=False)}
        ]
        tool = PaperSearchTool(connector=connector, mcp_config={})
        result = asyncio.run(tool.execute({"words": ["x"], "question": "y?"}))
        row = json.loads(result.content)["data"][0]
        assert row["citationNums"] == 0

    def test_keeps_zero_impact_factor(self) -> None:
        raw = {
            "data": [
                {
                    "enName": "T",
                    "enAbstract": "a",
                    "doi": "10.2/z",
                    "impactFactor": 0.0,
                }
            ]
        }
        connector = AsyncMock()
        connector.call_tool.return_value = [
            {"text": json.dumps(raw, ensure_ascii=False)}
        ]
        tool = PaperSearchTool(connector=connector, mcp_config={})
        result = asyncio.run(tool.execute({"words": ["x"], "question": "y?"}))
        row = json.loads(result.content)["data"][0]
        assert row["impactFactor"] == 0.0

    def test_validation_empty_words(self) -> None:
        connector = AsyncMock()
        tool = PaperSearchTool(connector=connector, mcp_config={})
        result = asyncio.run(tool.execute({"words": [], "question": "q"}))
        assert result.status == "error"
        connector.call_tool.assert_not_called()
