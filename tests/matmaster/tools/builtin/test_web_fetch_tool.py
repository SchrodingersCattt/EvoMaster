"""tests/matmaster/tools/builtin/test_web_fetch_tool.py"""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from matmaster.tools.builtin.web_fetch_tool import WebFetchTool, _extract_content
from matmaster.tools.tool_result import ToolResult


class TestWebFetchMetadata:
    def test_name(self):
        assert WebFetchTool.name == "WebFetch"

    def test_no_session_needed(self):
        tool = WebFetchTool()
        assert tool._session is None


class TestExtractContent:
    def test_html_extraction(self):
        html = "<html><body><p>Hello world</p></body></html>"
        result = _extract_content(html, "text/html", b"")
        assert "Hello" in result

    def test_script_removal(self):
        html = "<html><body><script>alert(1)</script><p>Safe</p></body></html>"
        result = _extract_content(html, "text/html", b"")
        assert "alert" not in result
        assert "Safe" in result

    def test_plain_text(self):
        text = "Just plain text"
        result = _extract_content(text, "text/plain", b"")
        assert result == "Just plain text"

    def test_truncation(self):
        text = "x" * 100_000
        result = _extract_content(text, "text/plain", b"")
        assert len(result) <= 50_000


class TestWebFetchExecution:
    def test_empty_url_error(self):
        tool = WebFetchTool()
        result = asyncio.run(tool.execute({"url": ""}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    def test_prompt_recorded_in_payload(self):
        tool = WebFetchTool(workdir=Path("/tmp/test_wf"))
        with patch("matmaster.tools.builtin.web_fetch_tool._is_private_host", return_value=False), \
             patch("matmaster.tools.builtin.web_fetch_tool.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.text = "<html><body>Content</body></html>"
            mock_resp.content = b"<html><body>Content</body></html>"
            mock_resp.status_code = 200
            mock_resp.headers = {"content-type": "text/html"}
            mock_resp.raise_for_status.return_value = None
            mock_resp.is_redirect = False
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_httpx.Client.return_value = mock_client
            result = asyncio.run(tool.execute({
                "url": "https://example.com",
                "prompt": "summarize this",
            }))
            assert isinstance(result, ToolResult)
            assert result.payload.get("prompt") == "summarize this"


class TestWebFetchUrlValidation:
    def test_invalid_url_rejected(self):
        tool = WebFetchTool()
        result = asyncio.run(tool.execute({"url": "not-a-url"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    def test_private_ip_rejected(self):
        tool = WebFetchTool()
        result = asyncio.run(tool.execute({"url": "http://127.0.0.1/admin"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "private" in result.content.lower() or "internal" in result.content.lower()


class TestDiskCache:
    def test_cache_hit(self, tmp_path):
        tool = WebFetchTool(workdir=tmp_path)
        # Manually populate cache
        import hashlib, json, time as _t
        url = "https://example.com/page"
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        cache_dir = tmp_path / ".web_cache"
        cache_dir.mkdir()
        (cache_dir / f"{key}.json").write_text(json.dumps({
            "url": url, "content": "cached content", "fetched_at": _t.time(),
        }))
        with patch("matmaster.tools.builtin.web_fetch_tool._is_private_host", return_value=False):
            result = asyncio.run(tool.execute({"url": url}))
        assert isinstance(result, ToolResult)
        assert "cached content" in result.content

    def test_cache_expired(self, tmp_path):
        tool = WebFetchTool(workdir=tmp_path)
        import hashlib, json
        url = "https://example.com/expired"
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        cache_dir = tmp_path / ".web_cache"
        cache_dir.mkdir()
        (cache_dir / f"{key}.json").write_text(json.dumps({
            "url": url, "content": "old", "fetched_at": 0,  # epoch = expired
        }))
        # Will try to fetch (and fail), proving cache was bypassed
        result = asyncio.run(tool.execute({"url": url}))
        assert "old" not in result.content  # should NOT return expired cache
