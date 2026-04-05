"""tests/matmaster/tools/builtin/test_web_search_tool.py"""

import asyncio
import os
from unittest.mock import MagicMock, patch

from matmaster.tools.builtin.web_search_tool import WebSearchTool
from matmaster.tools.tool_result import ToolResult


class TestWebSearchMetadata:
    def test_name(self):
        assert WebSearchTool.name == "WebSearch"

    def test_no_session_needed(self):
        tool = WebSearchTool()  # no session
        assert tool._session is None


class TestWebSearchValidation:
    def test_empty_query_error(self):
        tool = WebSearchTool()
        result = asyncio.run(tool.execute({"query": ""}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    def test_missing_api_key_error(self):
        tool = WebSearchTool()
        with patch.dict(os.environ, {}, clear=True):
            result = asyncio.run(tool.execute({"query": "test"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "key" in result.content.lower()


class TestDomainFiltering:
    def test_allowed_domains_appended(self):
        tool = WebSearchTool()
        args = {"query": "python async", "allowed_domains": ["docs.python.org"]}
        with patch.dict(os.environ, {"SEARCHAPI_API_KEY": "fake"}):
            with patch("matmaster.tools.builtin.web_search_tool.httpx") as mock_httpx:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"organic_results": []}
                mock_resp.raise_for_status.return_value = None
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_resp
                mock_httpx.Client.return_value = mock_client
                asyncio.run(tool.execute(args))
                call_args = mock_client.get.call_args
                params = call_args.kwargs.get("params") or call_args[1].get(
                    "params", {}
                )
                assert "site:docs.python.org" in params.get("q", "")

    def test_blocked_domains_appended(self):
        tool = WebSearchTool()
        args = {"query": "python async", "blocked_domains": ["example.com"]}
        with patch.dict(os.environ, {"SEARCHAPI_API_KEY": "fake"}):
            with patch("matmaster.tools.builtin.web_search_tool.httpx") as mock_httpx:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"organic_results": []}
                mock_resp.raise_for_status.return_value = None
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_resp
                mock_httpx.Client.return_value = mock_client
                asyncio.run(tool.execute(args))
                call_args = mock_client.get.call_args
                params = call_args.kwargs.get("params") or call_args[1].get(
                    "params", {}
                )
                assert "-site:example.com" in params.get("q", "")


class TestDomainMutualExclusion:
    def test_allowed_and_blocked_rejects(self):
        tool = WebSearchTool()
        with patch.dict(os.environ, {"SEARCHAPI_API_KEY": "fake"}):
            result = asyncio.run(
                tool.execute(
                    {
                        "query": "test",
                        "allowed_domains": ["a.com"],
                        "blocked_domains": ["b.com"],
                    }
                )
            )
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "both" in result.content.lower()

    def test_short_query_rejected(self):
        tool = WebSearchTool()
        with patch.dict(os.environ, {"SEARCHAPI_API_KEY": "fake"}):
            result = asyncio.run(tool.execute({"query": "x"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
