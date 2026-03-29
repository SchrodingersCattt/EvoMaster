"""Tests for WebSearchTool."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from matmaster.tools.builtin.web_search_tool import (
    SEARCH_API_ENDPOINT,
    WebSearchTool,
    _normalize_results,
    _resolve_api_key,
)
from matmaster.tools.tool_registry import Tool
from matmaster.tools.tool_result import ToolResult


class TestWebSearchToolProtocol:
    """WebSearchTool satisfies Tool Protocol."""

    def test_name(self) -> None:
        tool = WebSearchTool()
        assert tool.name == "web_search"

    def test_tool_protocol(self) -> None:
        tool = WebSearchTool()
        assert isinstance(tool, Tool)

    def test_has_required_schema_fields(self) -> None:
        schema = WebSearchTool.json_schema
        assert "query" in schema["properties"]
        assert "query" in schema["required"]


class TestResolveApiKey:
    """API key resolution from environment."""

    def test_searchapi_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARCHAPI_API_KEY", "key-1")
        assert _resolve_api_key() == "key-1"

    def test_searchapi_key_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
        monkeypatch.setenv("SEARCHAPI_KEY", "key-2")
        assert _resolve_api_key() == "key-2"

    def test_no_key_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
        monkeypatch.delenv("SEARCHAPI_KEY", raising=False)
        assert _resolve_api_key() == ""


class TestNormalizeResults:
    """SearchApi response normalization."""

    def test_basic(self) -> None:
        payload = {
            "organic_results": [
                {"title": "T1", "link": "http://a.com", "snippet": "S1"},
                {"title": "T2", "link": "http://b.com", "snippet": "S2"},
            ]
        }
        results = _normalize_results(payload, top_k=10)
        assert len(results) == 2
        assert results[0] == {"title": "T1", "link": "http://a.com", "snippet": "S1"}

    def test_top_k_truncation(self) -> None:
        payload = {
            "organic_results": [
                {"title": f"T{i}", "link": f"http://{i}.com", "snippet": f"S{i}"}
                for i in range(20)
            ]
        }
        results = _normalize_results(payload, top_k=3)
        assert len(results) == 3

    def test_skips_empty_links(self) -> None:
        payload = {
            "organic_results": [
                {"title": "T1", "link": "", "snippet": "S1"},
                {"title": "T2", "link": "http://b.com", "snippet": "S2"},
            ]
        }
        results = _normalize_results(payload, top_k=10)
        assert len(results) == 1
        assert results[0]["link"] == "http://b.com"

    def test_empty_organic(self) -> None:
        assert _normalize_results({}, top_k=10) == []
        assert _normalize_results({"organic_results": "bad"}, top_k=10) == []


class TestWebSearchToolExecution:
    """WebSearchTool._execute with mocked HTTP."""

    async def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
        monkeypatch.delenv("SEARCHAPI_KEY", raising=False)
        tool = WebSearchTool()
        result = await tool.execute({"query": "test"})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "SearchApi key" in result.content

    async def test_empty_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARCHAPI_API_KEY", "fake")
        tool = WebSearchTool()
        result = await tool.execute({"query": "  "})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "query" in result.content.lower()

    @patch("matmaster.tools.builtin.web_search_tool.httpx")
    async def test_successful_search(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SEARCHAPI_API_KEY", "fake")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "organic_results": [
                {"title": "Result", "link": "http://example.com", "snippet": "text"}
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_httpx.Client.return_value = mock_client

        tool = WebSearchTool()
        result = await tool.execute({"query": "hello"})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        data = json.loads(result.content)
        assert len(data["results"]) == 1
        assert data["results"][0]["link"] == "http://example.com"

    @patch("matmaster.tools.builtin.web_search_tool.httpx")
    async def test_http_error(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SEARCHAPI_API_KEY", "fake")
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("503 Server Error")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_httpx.Client.return_value = mock_client

        tool = WebSearchTool()
        result = await tool.execute({"query": "hello"})
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    @patch("matmaster.tools.builtin.web_search_tool.httpx")
    async def test_default_params(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SEARCHAPI_API_KEY", "fake-key")
        mock_response = MagicMock()
        mock_response.json.return_value = {"organic_results": []}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_httpx.Client.return_value = mock_client

        tool = WebSearchTool()
        await tool.execute({"query": "test"})

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
        assert params["engine"] == "google"
        assert params["q"] == "test"
        assert params["api_key"] == "fake-key"
        assert params["gl"] == "us"
        assert params["hl"] == "en"
