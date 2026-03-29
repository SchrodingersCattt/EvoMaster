"""Tests for WebFetchTool."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from matmaster.tools.builtin.web_fetch_tool import (
    BROWSER_HEADERS,
    WebFetchTool,
    _WebpageDiskCache,
    _fetch_single_url,
    _extract_content,
    _MAX_CONTENT_LENGTH,
)
from matmaster.tools.tool_registry import Tool
from matmaster.tools.tool_result import ToolResult


# ── Cache tests ──────────────────────────────────────────


class TestWebpageDiskCache:
    """Disk cache get/put/eviction."""

    def test_put_and_get(self, tmp_path: Path) -> None:
        cache = _WebpageDiskCache(tmp_path / "cache")
        cache.put("http://a.com", "content-a")
        assert cache.get("http://a.com") == "content-a"

    def test_ttl_expiry(self, tmp_path: Path) -> None:
        cache = _WebpageDiskCache(tmp_path / "cache")
        cache.TTL = 0  # expire immediately
        cache.put("http://a.com", "content-a")
        assert cache.get("http://a.com") is None

    def test_cache_miss(self, tmp_path: Path) -> None:
        cache = _WebpageDiskCache(tmp_path / "cache")
        assert cache.get("http://nonexistent.com") is None

    def test_eviction(self, tmp_path: Path) -> None:
        cache = _WebpageDiskCache(tmp_path / "cache")
        cache.MAX_ENTRIES = 2
        cache.put("http://1.com", "c1")
        cache.put("http://2.com", "c2")
        cache.put("http://3.com", "c3")  # triggers eviction
        # oldest entry should be evicted
        entries = list((tmp_path / "cache").glob("*.json"))
        assert len(entries) <= 2


# ── Content extraction tests ─────────────────────────────


class TestExtractContent:
    """HTML/PDF/plain text content extraction."""

    def test_html_to_markdown(self) -> None:
        html = "<html><body><h1>Title</h1><p>Text</p></body></html>"
        content = _extract_content(html, "text/html", b"")
        assert "Title" in content
        assert "Text" in content

    def test_html_noise_removal(self) -> None:
        html = (
            "<html><body>"
            "<nav>Menu</nav>"
            "<div class='cookie-banner'>Accept</div>"
            "<p>Main content</p>"
            "<footer>Footer</footer>"
            "</body></html>"
        )
        content = _extract_content(html, "text/html", b"")
        assert "Main content" in content
        assert "Menu" not in content
        assert "Accept" not in content
        assert "Footer" not in content

    def test_plain_text_passthrough(self) -> None:
        text = "Just plain text"
        content = _extract_content(text, "text/plain", b"")
        assert content == "Just plain text"

    def test_truncation(self) -> None:
        long_text = "x" * (_MAX_CONTENT_LENGTH + 1000)
        content = _extract_content(long_text, "text/plain", b"")
        assert len(content) == _MAX_CONTENT_LENGTH

    def test_pdf_extraction(self) -> None:
        fitz = pytest.importorskip("fitz")
        # Create a minimal PDF in memory
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "PDF test content")
        pdf_bytes = doc.tobytes()
        doc.close()
        content = _extract_content("", "application/pdf", pdf_bytes)
        assert "PDF test content" in content

    def test_pdf_missing_fitz(self) -> None:
        with patch("matmaster.tools.builtin.web_fetch_tool.fitz", None):
            with pytest.raises(RuntimeError, match="PyMuPDF"):
                _extract_content("", "application/pdf", b"%PDF-fake")

    def test_octet_stream_non_pdf_is_not_treated_as_pdf(self) -> None:
        """application/octet-stream without PDF magic bytes -> plain text."""
        content = _extract_content(
            "plain data", "application/octet-stream", b"not-pdf"
        )
        assert content == "plain data"


# ── Fetch function tests ─────────────────────────────────


class TestFetchSingleUrl:
    """_fetch_single_url with mocked httpx."""

    @patch("matmaster.tools.builtin.web_fetch_tool.httpx")
    def test_403_retry_with_alternate_ua(
        self, mock_httpx: MagicMock
    ) -> None:
        first_response = MagicMock()
        first_response.status_code = 403
        second_response = MagicMock()
        second_response.status_code = 200
        second_response.headers = {"content-type": "text/plain"}
        second_response.text = "Success after retry"
        second_response.content = b"Success after retry"
        second_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [first_response, second_response]
        mock_httpx.Client.return_value = mock_client

        with patch("matmaster.tools.builtin.web_fetch_tool._time.sleep"):
            url, content, error = _fetch_single_url("http://example.com")

        assert error is None
        assert content == "Success after retry"
        assert mock_client.get.call_count == 2


# ── Tool protocol tests ──────────────────────────────────


class TestWebFetchToolProtocol:
    """WebFetchTool satisfies Tool Protocol."""

    def test_name(self, tmp_path: Path) -> None:
        tool = WebFetchTool(workdir=tmp_path)
        assert tool.name == "web_fetch"

    def test_tool_protocol(self, tmp_path: Path) -> None:
        tool = WebFetchTool(workdir=tmp_path)
        assert isinstance(tool, Tool)

    def test_schema_url_is_array(self) -> None:
        schema = WebFetchTool.json_schema
        assert schema["properties"]["url"]["type"] == "array"


# ── Execution tests ──────────────────────────────────────


class TestWebFetchToolExecution:
    """WebFetchTool._execute with mocked HTTP."""

    @patch("matmaster.tools.builtin.web_fetch_tool._fetch_single_url")
    async def test_single_url(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        mock_fetch.return_value = ("http://a.com", "Page content", None)
        tool = WebFetchTool(workdir=tmp_path)
        result = await tool.execute({"url": ["http://a.com"]})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert "Page content" in result.content

    @patch("matmaster.tools.builtin.web_fetch_tool._fetch_single_url")
    async def test_multi_url(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        mock_fetch.side_effect = [
            ("http://a.com", "Content A", None),
            ("http://b.com", "Content B", None),
        ]
        tool = WebFetchTool(workdir=tmp_path)
        result = await tool.execute({"url": ["http://a.com", "http://b.com"]})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        data = json.loads(result.content)
        assert "http://a.com" in data
        assert "http://b.com" in data

    @patch("matmaster.tools.builtin.web_fetch_tool._fetch_single_url")
    async def test_url_error_inlined(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        mock_fetch.return_value = ("http://a.com", None, "404 Not Found")
        tool = WebFetchTool(workdir=tmp_path)
        result = await tool.execute({"url": ["http://a.com"]})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "404" in result.content

    async def test_empty_url_list(self, tmp_path: Path) -> None:
        tool = WebFetchTool(workdir=tmp_path)
        result = await tool.execute({"url": []})
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    @patch("matmaster.tools.builtin.web_fetch_tool._fetch_single_url")
    async def test_all_urls_fail_returns_error(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        mock_fetch.side_effect = [
            ("http://a.com", None, "Timeout"),
            ("http://b.com", None, "404"),
        ]
        tool = WebFetchTool(workdir=tmp_path)
        result = await tool.execute({"url": ["http://a.com", "http://b.com"]})
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    async def test_string_url_normalized(self, tmp_path: Path) -> None:
        """Bare string url is normalized to list."""
        with patch(
            "matmaster.tools.builtin.web_fetch_tool._fetch_single_url"
        ) as mock_fetch:
            mock_fetch.return_value = ("http://a.com", "content", None)
            tool = WebFetchTool(workdir=tmp_path)
            result = await tool.execute({"url": "http://a.com"})
            assert isinstance(result, ToolResult)
            assert result.status == "success"
