# Builtin Web Tools Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `web_search` and `web_fetch` as native builtin tools in `matmaster/tools/builtin/`, migrating core functionality from legacy `playground/mat_master/tools/`.

**Architecture:** Two new `BuiltinTool` subclasses making control-plane HTTP calls via sync `httpx.Client`. `WebSearchTool` wraps SearchApi.io. `WebFetchTool` fetches URLs with HTML-to-markdown conversion, PDF support, and disk cache. Both return `ToolResult` directly. Registration via TOML config + `_init_builtin_tools` instantiation.

**Tech Stack:** httpx (sync), beautifulsoup4+lxml, markdownify, PyMuPDF (optional)

**Spec:** `docs/superpowers/specs/2026-03-27-builtin-web-tools-design.md`

---

## Chunk 0: Base class type fix

### Task 0: Widen BuiltinTool.execute return type

**Files:**
- Modify: `matmaster/tools/builtin/base.py:45,54`

The base class `execute` returns `str` and `_execute` is typed `async def ... -> str`. Both web tools return `ToolResult` from `_execute`. This works at runtime (`normalize_tool_result` handles `ToolResult` pass-through), but the type annotations are wrong. Fix both:

- [ ] **Step 1: Update `base.py` return types**

Change `execute` return type from `-> str` to `-> str | ToolResult`:

```python
    def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
```

Change `_execute` from `async def` to `def` and widen return type:

```python
    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
```

Note: all 13 existing subclasses already implement `_execute` as sync `def` (not `async`). The `async` on the ABC was a Phase 13 placeholder that doesn't match current usage. Removing it aligns the ABC with reality.

- [ ] **Step 2: Run existing tests**

Run: `uv run pytest tests/matmaster/tools/ -v --timeout=30`
Expected: ALL PASS (no behavioral change, only type annotations)

- [ ] **Step 3: Commit**

```bash
git add matmaster/tools/builtin/base.py
git commit -m "fix: align BuiltinTool execute/\_execute type annotations with actual usage"
```

---

## Chunk 1: WebSearchTool

### Task 1: WebSearchTool tests

**Files:**
- Create: `tests/matmaster/tools/test_web_search_tool.py`

- [ ] **Step 1: Write test file**

```python
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

    def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
        monkeypatch.delenv("SEARCHAPI_KEY", raising=False)
        tool = WebSearchTool()
        result = tool.execute({"query": "test"})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "SearchApi key" in result.content

    def test_empty_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARCHAPI_API_KEY", "fake")
        tool = WebSearchTool()
        result = tool.execute({"query": "  "})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "query" in result.content.lower()

    @patch("matmaster.tools.builtin.web_search_tool.httpx")
    def test_successful_search(
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
        result = tool.execute({"query": "hello"})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        data = json.loads(result.content)
        assert len(data["results"]) == 1
        assert data["results"][0]["link"] == "http://example.com"

    @patch("matmaster.tools.builtin.web_search_tool.httpx")
    def test_http_error(
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
        result = tool.execute({"query": "hello"})
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    @patch("matmaster.tools.builtin.web_search_tool.httpx")
    def test_default_params(
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
        tool.execute({"query": "test"})

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
        assert params["engine"] == "google"
        assert params["q"] == "test"
        assert params["api_key"] == "fake-key"
        assert params["gl"] == "us"
        assert params["hl"] == "en"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_web_search_tool.py -v`
Expected: FAIL (ImportError -- module not found)

- [ ] **Step 3: Commit test file**

```bash
git add tests/matmaster/tools/test_web_search_tool.py
git commit -m "test: add web_search_tool tests (red phase)"
```

---

### Task 2: WebSearchTool implementation

**Files:**
- Create: `matmaster/tools/builtin/web_search_tool.py`

- [ ] **Step 1: Write implementation**

```python
"""WebSearchTool -- SearchApi.io backed web search.

Control-plane HTTP call via sync httpx.Client. No session dependency.
Returns ToolResult directly for correct error status propagation.
"""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar

import httpx

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult

SEARCH_API_ENDPOINT = "https://www.searchapi.io/api/v1/search"


def _resolve_api_key() -> str:
    """Resolve SearchApi key from environment variables."""
    for var in ("SEARCHAPI_API_KEY", "SEARCHAPI_KEY"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return ""


def _normalize_results(
    payload: dict[str, Any], top_k: int
) -> list[dict[str, str]]:
    """Extract organic results into [{title, link, snippet}]."""
    organic = payload.get("organic_results", [])
    if not isinstance(organic, list):
        return []

    results: list[dict[str, str]] = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if not link:
            continue
        results.append(
            {
                "title": str(item.get("title") or "").strip(),
                "link": link,
                "snippet": str(item.get("snippet") or "").strip(),
            }
        )
        if len(results) >= top_k:
            break
    return results


class WebSearchTool(BuiltinTool):
    """Search the web via SearchApi.io (Google engine)."""

    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "Search the web using a search query and return results.\n\n"
        "Returns up to top_k results, each with title, link, and snippet.\n"
        "Requires SEARCHAPI_API_KEY or SEARCHAPI_KEY environment variable."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query.",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of results to return.",
                "default": 10,
            },
            "gl": {
                "type": "string",
                "description": "Country code (e.g. us, cn).",
                "default": "us",
            },
            "hl": {
                "type": "string",
                "description": "Language code (e.g. en, zh-cn).",
                "default": "en",
            },
        },
        "required": ["query"],
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        query = (arguments.get("query") or "").strip()
        if not query:
            return ToolResult(
                status="error", content="Error: query is required."
            )

        api_key = _resolve_api_key()
        if not api_key:
            return ToolResult(
                status="error",
                content=(
                    "Error: Missing SearchApi key. "
                    "Set SEARCHAPI_API_KEY or SEARCHAPI_KEY in environment."
                ),
            )

        top_k = max(1, int(arguments.get("top_k", 10)))
        gl = (arguments.get("gl") or "us").strip()
        hl = (arguments.get("hl") or "en").strip()

        params: dict[str, Any] = {
            "engine": "google",
            "q": query,
            "api_key": api_key,
            "gl": gl,
            "hl": hl,
        }

        try:
            with httpx.Client(timeout=20) as client:
                response = client.get(
                    SEARCH_API_ENDPOINT,
                    params=params,
                    headers={"User-Agent": "matmaster-web-search/1.0"},
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return ToolResult(
                status="error",
                content=f"Error: {type(exc).__name__}: {exc}",
            )

        results = _normalize_results(payload, top_k=top_k)
        return ToolResult(
            status="success",
            content=json.dumps(
                {"status": "success", "results": results},
                ensure_ascii=False,
            ),
        )
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/tools/test_web_search_tool.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add matmaster/tools/builtin/web_search_tool.py
git commit -m "feat: add WebSearchTool (SearchApi.io builtin)"
```

---

## Chunk 2: WebFetchTool

### Task 3: WebFetchTool tests

**Files:**
- Create: `tests/matmaster/tools/test_web_fetch_tool.py`

- [ ] **Step 1: Write test file**

```python
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

    def test_markdownify_fallback(self) -> None:
        """When markdownify import fails, falls back to plain text."""
        html = "<html><body><p>Fallback text</p></body></html>"
        with patch(
            "matmaster.tools.builtin.web_fetch_tool.markdownify",
            side_effect=ImportError,
        ):
            # Force the import inside _extract_content to fail
            import matmaster.tools.builtin.web_fetch_tool as mod
            original = mod._extract_content
            # Patch at the markdownify import point
            with patch.dict("sys.modules", {"markdownify": None}):
                content = _extract_content(html, "text/html", b"")
                assert "Fallback text" in content


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
    def test_single_url(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        mock_fetch.return_value = ("http://a.com", "Page content", None)
        tool = WebFetchTool(workdir=tmp_path)
        result = tool.execute({"url": ["http://a.com"]})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert "Page content" in result.content

    @patch("matmaster.tools.builtin.web_fetch_tool._fetch_single_url")
    def test_multi_url(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        mock_fetch.side_effect = [
            ("http://a.com", "Content A", None),
            ("http://b.com", "Content B", None),
        ]
        tool = WebFetchTool(workdir=tmp_path)
        result = tool.execute({"url": ["http://a.com", "http://b.com"]})
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        data = json.loads(result.content)
        assert "http://a.com" in data
        assert "http://b.com" in data

    @patch("matmaster.tools.builtin.web_fetch_tool._fetch_single_url")
    def test_url_error_inlined(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        mock_fetch.return_value = ("http://a.com", None, "404 Not Found")
        tool = WebFetchTool(workdir=tmp_path)
        result = tool.execute({"url": ["http://a.com"]})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "404" in result.content

    def test_empty_url_list(self, tmp_path: Path) -> None:
        tool = WebFetchTool(workdir=tmp_path)
        result = tool.execute({"url": []})
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    @patch("matmaster.tools.builtin.web_fetch_tool._fetch_single_url")
    def test_all_urls_fail_returns_error(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        mock_fetch.side_effect = [
            ("http://a.com", None, "Timeout"),
            ("http://b.com", None, "404"),
        ]
        tool = WebFetchTool(workdir=tmp_path)
        result = tool.execute({"url": ["http://a.com", "http://b.com"]})
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    def test_string_url_normalized(self, tmp_path: Path) -> None:
        """Bare string url is normalized to list."""
        with patch(
            "matmaster.tools.builtin.web_fetch_tool._fetch_single_url"
        ) as mock_fetch:
            mock_fetch.return_value = ("http://a.com", "content", None)
            tool = WebFetchTool(workdir=tmp_path)
            result = tool.execute({"url": "http://a.com"})
            assert isinstance(result, ToolResult)
            assert result.status == "success"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_web_fetch_tool.py -v`
Expected: FAIL (ImportError -- module not found)

- [ ] **Step 3: Commit test file**

```bash
git add tests/matmaster/tools/test_web_fetch_tool.py
git commit -m "test: add web_fetch_tool tests (red phase)"
```

---

### Task 4: WebFetchTool implementation

**Files:**
- Create: `matmaster/tools/builtin/web_fetch_tool.py`

- [ ] **Step 1: Write implementation**

```python
"""WebFetchTool -- fetch and extract content from web pages.

Control-plane HTTP call via sync httpx.Client. Supports HTML (BeautifulSoup
+ markdownify), PDF (PyMuPDF), and plain text. Disk cache with TTL.
No session dependency; uses workdir for cache storage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, ClassVar

import httpx
from bs4 import BeautifulSoup

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

logger = logging.getLogger(__name__)

_MAX_CONTENT_LENGTH = 50_000

BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_ALTERNATE_UA_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
        "Gecko/20100101 Firefox/121.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_NOISE_PATTERN = re.compile(r"cookie|banner|sidebar|menu", re.I)


# ── Disk Cache ───────────────────────────────────────────


class _WebpageDiskCache:
    """Workspace-scoped disk cache for fetched web pages.

    JSON files at {cache_dir}/{url_hash}.json. TTL-based expiry.
    Oldest-first eviction when MAX_ENTRIES exceeded.
    """

    TTL: int = 900  # 15 minutes
    MAX_ENTRIES: int = 200

    def __init__(self, cache_dir: Path) -> None:
        self._dir = Path(cache_dir)
        self._evict_lock = threading.Lock()

    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def get(self, url: str) -> str | None:
        path = self._dir / f"{self._key(url)}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if _time.time() - data.get("fetched_at", 0) > self.TTL:
                return None
            return data.get("content")
        except Exception:
            return None

    def put(self, url: str, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "url": url,
            "content": content,
            "fetched_at": _time.time(),
        }
        target = self._dir / f"{self._key(url)}.json"
        try:
            fd = tempfile.NamedTemporaryFile(
                mode="w",
                dir=str(self._dir),
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            )
            try:
                json.dump(entry, fd, ensure_ascii=False)
                fd.flush()
            finally:
                fd.close()
            Path(fd.name).replace(target)
        except Exception:
            logger.warning(
                "Failed to write cache entry for %s", url, exc_info=True
            )
            return
        self._maybe_evict()

    def _maybe_evict(self) -> None:
        with self._evict_lock:
            try:
                entries = sorted(
                    self._dir.glob("*.json"),
                    key=lambda p: p.stat().st_mtime,
                )
            except Exception:
                return
            excess = len(entries) - self.MAX_ENTRIES
            if excess <= 0:
                return
            for path in entries[:excess]:
                try:
                    path.unlink()
                except Exception:
                    pass


# ── Content extraction ───────────────────────────────────


def _extract_content(
    text: str,
    content_type: str,
    raw_bytes: bytes,
) -> str:
    """Extract readable content from response body.

    Dispatches by content_type: HTML -> markdown, PDF -> PyMuPDF, else raw.
    """
    is_pdf = "application/pdf" in content_type or (
        "application/octet-stream" in content_type
        and raw_bytes[:5] == b"%PDF-"
    )

    if is_pdf and raw_bytes:
        if fitz is None:
            raise RuntimeError(
                "PyMuPDF (fitz) is not available; cannot extract PDF content."
            )
        doc = fitz.open(stream=raw_bytes, filetype="pdf")
        content = "".join(page.get_text() for page in doc)
        doc.close()
    elif text.strip().startswith("<"):
        # HTML
        try:
            soup = BeautifulSoup(text, "lxml")
        except Exception:
            soup = BeautifulSoup(text, "html.parser")

        for tag in soup(
            ["script", "style", "nav", "footer", "aside", "noscript", "iframe"]
        ):
            tag.decompose()
        for tag in soup.find_all(attrs={"class": _NOISE_PATTERN}):
            tag.decompose()
        for tag in soup.find_all(attrs={"id": _NOISE_PATTERN}):
            tag.decompose()

        try:
            import markdownify as _md

            content = _md.markdownify(
                str(soup), heading_style="ATX", strip=["img", "svg"]
            )
            content = re.sub(r"\n{3,}", "\n\n", content)
        except Exception:
            lines = (line.strip() for line in soup.get_text().splitlines())
            chunks = (
                phrase.strip()
                for line in lines
                for phrase in line.split("  ")
            )
            content = " ".join(chunk for chunk in chunks if chunk)
    else:
        content = text

    # Clean control characters and truncate
    content = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\uFFFD]", "", content)
    if len(content) > _MAX_CONTENT_LENGTH:
        content = content[:_MAX_CONTENT_LENGTH]
    return content


# ── Single URL fetch ─────────────────────────────────────


def _fetch_single_url(
    url: str,
    cache: _WebpageDiskCache | None = None,
) -> tuple[str, str | None, str | None]:
    """Fetch one URL. Returns (url, content_or_None, error_or_None)."""
    # Check cache
    if cache is not None:
        cached = cache.get(url)
        if cached is not None:
            return (url, cached, None)

    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            response = client.get(url, headers=BROWSER_HEADERS)
            if response.status_code in (403, 429):
                logger.warning(
                    "Got %s for %s; retrying with alternate UA.",
                    response.status_code,
                    url,
                )
                _time.sleep(1.5)
                response = client.get(url, headers=_ALTERNATE_UA_HEADERS)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        content = _extract_content(
            response.text, content_type, response.content
        )

        if cache is not None:
            cache.put(url, content)

        return (url, content, None)
    except Exception as exc:
        return (url, None, f"{type(exc).__name__}: {exc}")


# ── Tool class ───────────────────────────────────────────


class WebFetchTool(BuiltinTool):
    """Fetch and extract text content from web pages."""

    name: ClassVar[str] = "web_fetch"
    description: ClassVar[str] = (
        "Fetch and extract text content from one or more web page URLs.\n\n"
        "Handles HTML pages (converted to markdown) and PDF documents.\n"
        "Results are cached for 15 minutes to avoid redundant fetches."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of URLs to fetch and extract text from."
                ),
            },
        },
        "required": ["url"],
    }

    def __init__(self, *, workdir: Path | None = None) -> None:
        super().__init__(workdir=workdir)
        cache_dir = Path(workdir) / ".web_cache" if workdir else None
        self._cache = _WebpageDiskCache(cache_dir) if cache_dir else None

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        urls = arguments.get("url", [])
        # Normalize bare string to list
        if isinstance(urls, str):
            urls = [urls]
        if not urls:
            return ToolResult(
                status="error", content="Error: url list is empty."
            )

        if len(urls) == 1:
            return self._fetch_one(urls[0])
        return self._fetch_many(urls)

    def _fetch_one(self, url: str) -> ToolResult:
        _, content, error = _fetch_single_url(url, self._cache)
        if error:
            return ToolResult(status="error", content=f"Error: {error}")
        return ToolResult(status="success", content=content or "")

    def _fetch_many(self, urls: list[str]) -> ToolResult:
        results: dict[str, Any] = {}
        any_success = False
        with ThreadPoolExecutor(max_workers=min(len(urls), 8)) as pool:
            futures = {
                pool.submit(_fetch_single_url, u, self._cache): u
                for u in urls
            }
            for future in as_completed(futures):
                url, content, error = future.result()
                if error:
                    results[url] = {"error": error}
                else:
                    results[url] = {"content": content}
                    any_success = True

        return ToolResult(
            status="success" if any_success else "error",
            content=json.dumps(results, ensure_ascii=False),
        )
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/tools/test_web_fetch_tool.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add matmaster/tools/builtin/web_fetch_tool.py
git commit -m "feat: add WebFetchTool (HTML/PDF fetch with cache)"
```

---

## Chunk 3: Registration Integration

### Task 5: Wire up exports, registration, and TOML config

**Files:**
- Modify: `matmaster/tools/builtin/__init__.py`
- Modify: `matmaster/core/exp.py:300-366`
- Modify: `matmaster/exps/direct.toml:19-34`
- Modify: `matmaster/exps/explore.toml:35-42`

- [ ] **Step 1: Update `matmaster/tools/builtin/__init__.py`**

Add after existing imports:

```python
from matmaster.tools.builtin.web_fetch_tool import WebFetchTool
from matmaster.tools.builtin.web_search_tool import WebSearchTool
```

Add to `__all__`:

```python
    "WebFetchTool",
    "WebSearchTool",
```

- [ ] **Step 2: Update `matmaster/core/exp.py` `_init_builtin_tools`**

In the import block at L316-330, add:

```python
            WebFetchTool,
            WebSearchTool,
```

In the `native_tools` list at L337-352, add after TaskCompleteTool:

```python
            # Web tools: control-plane HTTP, no session dependency
            WebSearchTool(),
            WebFetchTool(workdir=ctx.workdir),
```

Update the docstring at L303-307 to reflect new tool count (14 native).

- [ ] **Step 3: Update `matmaster/exps/direct.toml`**

Add to the `[tools].builtin` list before the closing `]`:

```toml
    "web_search",
    "web_fetch",
```

- [ ] **Step 4: Update `matmaster/exps/explore.toml`**

Add to the `[tools].builtin` list:

```toml
    "web_search",
    "web_fetch",
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/matmaster/tools/test_web_search_tool.py tests/matmaster/tools/test_web_fetch_tool.py -v`
Expected: ALL PASS

Run: `uv run pytest tests/matmaster/ -v --timeout=30`
Expected: ALL PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add matmaster/tools/builtin/__init__.py matmaster/core/exp.py matmaster/exps/direct.toml matmaster/exps/explore.toml
git commit -m "feat: register web_search and web_fetch in builtin tools"
```
