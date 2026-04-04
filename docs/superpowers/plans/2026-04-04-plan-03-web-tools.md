# Web Tools (WebSearch + WebFetch) — Plan 03

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement WebSearch (SearchApi.io) and WebFetch (HTML/PDF extraction with disk cache) tools. Both are session-independent, EXTERNAL_SERVICE plane.

**Architecture:** Pure HTTP tools via `httpx.Client`. No session dependency. WebFetch has disk cache at `{workdir}/.web_cache/`. WebSearch maps CC's domain filtering to query modifiers.

**Tech Stack:** Python 3.10+, httpx, BeautifulSoup (bs4), markdownify, PyMuPDF (fitz, optional)

**Spec:** `docs/superpowers/specs/2026-04-04-builtin-tools-design.md` — Section 4

**Depends on:** Plan 00 (infrastructure)

---

## CC Source Reference

### WebSearch
- **Name:** `WebSearch` (`tools/WebSearchTool/prompt.ts`)
- **Schema** (`WebSearchTool.ts:25-37`): `query: string`, `allowed_domains?: string[]`, `blocked_domains?: string[]`
- **MatMaster adaptation:** SearchApi.io backend instead of Anthropic web search API. Domain filters via query modifiers.

### WebFetch
- **Name:** `WebFetch` (`tools/WebFetchTool/prompt.ts:1`)
- **Description:** Multi-line about fetching, markdown conversion, prompt processing, caching
- **Schema** (`WebFetchTool.ts:24-29`): `url: string (url validated)`, `prompt: string`
- **MatMaster adaptation:** `prompt` is recorded but not used for LLM extraction. Single URL (not batch).

---

## Task 1: WebSearchTool

**Files:**
- Create: `matmaster/tools/builtin/web_search_tool.py`
- Test: `tests/matmaster/tools/builtin/test_web_search_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/matmaster/tools/builtin/test_web_search_tool.py"""
import asyncio
import json
import os
import pytest
from unittest.mock import patch, MagicMock
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
        # We test the internal query modification, not the actual API call
        args = {"query": "python async", "allowed_domains": ["docs.python.org"]}
        # This will fail due to missing API key, but we can check the logic
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
                params = call_args.kwargs.get("params") or call_args[1].get("params", {})
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
                params = call_args.kwargs.get("params") or call_args[1].get("params", {})
                assert "-site:example.com" in params.get("q", "")


class TestDomainMutualExclusion:
    def test_allowed_and_blocked_rejects(self):
        tool = WebSearchTool()
        with patch.dict(os.environ, {"SEARCHAPI_API_KEY": "fake"}):
            result = asyncio.run(tool.execute({
                "query": "test",
                "allowed_domains": ["a.com"],
                "blocked_domains": ["b.com"],
            }))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "both" in result.content.lower()

    def test_short_query_rejected(self):
        tool = WebSearchTool()
        with patch.dict(os.environ, {"SEARCHAPI_API_KEY": "fake"}):
            result = asyncio.run(tool.execute({"query": "x"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
```

- [ ] **Step 2: Implement `web_search_tool.py`**

```python
"""matmaster/tools/builtin/web_search_tool.py

WebSearchTool — SearchApi.io backed web search.

CC Reference: tools/WebSearchTool/ (prompt.ts, WebSearchTool.ts)
CC name: WebSearch
"""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar

import httpx

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

SEARCH_API_ENDPOINT = "https://www.searchapi.io/api/v1/search"


def _resolve_api_key() -> str:
    for var in ("SEARCHAPI_API_KEY", "SEARCHAPI_KEY"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return ""


class WebSearchTool(BuiltinTool):
    """Search the web via SearchApi.io.

    CC name: WebSearch (WebSearchTool)
    """

    name: ClassVar[str] = "WebSearch"
    description: ClassVar[str] = (
        "Search the web using a search query and return results."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to use",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only include search results from these domains",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Never include search results from these domains",
            },
        },
        "required": ["query"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="web", mode="counted", max_concurrent=3),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"web.search"})
    effect_level: ClassVar[str] = "external_effect"
    plane: ClassVar[ToolPlane] = ToolPlane.EXTERNAL_SERVICE
    stop_mode: ClassVar[str] = "best_effort"

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        query = (arguments.get("query") or "").strip()
        if len(query) < 2:
            return ToolResult(status="error", content="Error: query must be at least 2 characters.")

        api_key = _resolve_api_key()
        if not api_key:
            return ToolResult(
                status="error",
                content="Error: Missing SearchApi key. Set SEARCHAPI_API_KEY.",
            )

        # Domain filtering via query modifiers (CC pattern)
        allowed = arguments.get("allowed_domains") or []
        blocked = arguments.get("blocked_domains") or []
        # CC rejects simultaneous allowed + blocked (WebSearchTool.ts validateInput)
        if allowed and blocked:
            return ToolResult(
                status="error",
                content="Error: Cannot specify both allowed_domains and blocked_domains.",
            )
        if allowed:
            query += " " + " OR ".join(f"site:{d}" for d in allowed)
        for d in blocked:
            query += f" -site:{d}"

        params: dict[str, Any] = {
            "engine": "google",
            "q": query,
            "api_key": api_key,
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
            return ToolResult(status="error", content=f"Error: {type(exc).__name__}: {exc}")

        organic = payload.get("organic_results", [])
        results = []
        for item in organic[:10]:
            if not isinstance(item, dict):
                continue
            link = str(item.get("link") or "").strip()
            if not link:
                continue
            results.append({
                "title": str(item.get("title") or "").strip(),
                "link": link,
                "snippet": str(item.get("snippet") or "").strip(),
            })

        return ToolResult(
            status="success",
            content=json.dumps({"results": results}, ensure_ascii=False),
        )
```

- [ ] **Step 3: Run tests and commit**

```bash
uv run pytest tests/matmaster/tools/builtin/test_web_search_tool.py -v
git add matmaster/tools/builtin/web_search_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_web_search_tool.py
git commit -m "feat(tools): add WebSearchTool with SearchApi.io and domain filtering"
```

---

## Task 2: WebFetchTool

**Files:**
- Create: `matmaster/tools/builtin/web_fetch_tool.py`
- Test: `tests/matmaster/tools/builtin/test_web_fetch_tool.py`
- Modify: `matmaster/tools/builtin/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
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
        with patch("matmaster.tools.builtin.web_fetch_tool.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.text = "<html><body>Content</body></html>"
            mock_resp.content = b"<html><body>Content</body></html>"
            mock_resp.status_code = 200
            mock_resp.headers = {"content-type": "text/html"}
            mock_resp.raise_for_status.return_value = None
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
```

- [ ] **Step 2: Implement `web_fetch_tool.py`**

```python
"""matmaster/tools/builtin/web_fetch_tool.py

WebFetchTool — fetch and extract content from web pages.

CC Reference: tools/WebFetchTool/ (prompt.ts, WebFetchTool.ts, utils.ts)
CC name: WebFetch
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
import threading
import time as _time
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote, unquote, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

try:
    import fitz  # PyMuPDF
except Exception:
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
}

_NOISE_PATTERN = re.compile(r"cookie|banner|sidebar|menu", re.I)


# ── Disk Cache ───────────────────────────────────────────

class _WebpageDiskCache:
    TTL: int = 900
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
        entry = {"url": url, "content": content, "fetched_at": _time.time()}
        target = self._dir / f"{self._key(url)}.json"
        try:
            fd = tempfile.NamedTemporaryFile(
                mode="w", dir=str(self._dir), suffix=".tmp",
                delete=False, encoding="utf-8",
            )
            try:
                json.dump(entry, fd, ensure_ascii=False)
                fd.flush()
            finally:
                fd.close()
            Path(fd.name).replace(target)
        except Exception:
            logger.warning("Failed to write cache for %s", url, exc_info=True)
            return
        self._maybe_evict()

    def _maybe_evict(self) -> None:
        with self._evict_lock:
            try:
                entries = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
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

def _extract_content(text: str, content_type: str, raw_bytes: bytes) -> str:
    is_pdf = "application/pdf" in content_type or (
        "application/octet-stream" in content_type and raw_bytes[:5] == b"%PDF-"
    )
    if is_pdf and raw_bytes:
        if fitz is None:
            raise RuntimeError("PyMuPDF (fitz) is not available; cannot extract PDF.")
        doc = fitz.open(stream=raw_bytes, filetype="pdf")
        content = "".join(page.get_text() for page in doc)
        doc.close()
    elif "text/html" in content_type or "application/xhtml" in content_type:
        try:
            soup = BeautifulSoup(text, "lxml")
        except Exception:
            soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "aside", "noscript", "iframe"]):
            tag.decompose()
        for tag in soup.find_all(attrs={"class": _NOISE_PATTERN}):
            tag.decompose()
        for tag in soup.find_all(attrs={"id": _NOISE_PATTERN}):
            tag.decompose()
        try:
            import markdownify as _md
            content = _md.markdownify(str(soup), heading_style="ATX", strip=["img", "svg"])
            content = re.sub(r"\n{3,}", "\n\n", content)
        except Exception:
            lines = (line.strip() for line in soup.get_text().splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            content = " ".join(chunk for chunk in chunks if chunk)
    else:
        content = text

    content = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\uFFFD]", "", content)
    if len(content) > _MAX_CONTENT_LENGTH:
        content = content[:_MAX_CONTENT_LENGTH]
    return content


_MAX_HTTP_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    # Upgrade http to https (CC utils.ts:374-379)
    scheme = "https" if parsed.scheme == "http" else parsed.scheme
    encoded_path = quote(unquote(parsed.path), safe="/")
    return urlunparse(parsed._replace(scheme=scheme, path=encoded_path))


def _validate_url(url: str) -> str | None:
    """Return error message if URL is invalid, None if OK."""
    if len(url) > 2000:
        return "URL too long (max 2000 characters)"
    parsed = urlparse(url)
    if not parsed.scheme or parsed.scheme not in ("http", "https"):
        return "URL must use http or https scheme"
    if not parsed.netloc or "." not in parsed.netloc:
        return "Invalid URL: hostname must contain a dot"
    if parsed.username or parsed.password:
        return "URLs with embedded credentials are not allowed"
    return None


def _is_private_host(hostname: str) -> bool:
    """Check if hostname resolves to a private/loopback/link-local address."""
    import ipaddress
    import socket
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        pass
    try:
        resolved = socket.getaddrinfo(hostname, None)[0][4][0]
        addr = ipaddress.ip_address(resolved)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except Exception:
        return False


# ── Tool class ───────────────────────────────────────────

class WebFetchTool(BuiltinTool):
    """Fetch and extract text content from web pages.

    CC name: WebFetch (WebFetchTool)
    """

    name: ClassVar[str] = "WebFetch"
    description: ClassVar[str] = (
        "- Fetches content from a specified URL and processes it\n"
        "- Takes a URL and an optional prompt as input\n"
        "- Fetches the URL content, converts HTML to markdown\n"
        "- Returns the extracted content\n"
        "- Use this tool when you need to retrieve and analyze web content\n"
        "- Includes a 15-minute cache for repeated accesses"
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch content from",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt to apply to the fetched content",
            },
        },
        "required": ["url"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="web", mode="counted", max_concurrent=3),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"web.fetch"})
    effect_level: ClassVar[str] = "external_effect"
    max_result_chars: ClassVar[int] = 100_000
    plane: ClassVar[ToolPlane] = ToolPlane.EXTERNAL_SERVICE
    stop_mode: ClassVar[str] = "best_effort"

    def __init__(self, *, workdir: Path | None = None, **kwargs) -> None:
        super().__init__(workdir=workdir, **kwargs)
        cache_dir = Path(workdir) / ".web_cache" if workdir else None
        self._cache = _WebpageDiskCache(cache_dir) if cache_dir else None

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        url = (arguments.get("url") or "").strip()
        prompt_text = (arguments.get("prompt") or "").strip()
        if not url:
            return ToolResult(status="error", content="Error: url is required.")

        # URL validation (CC WebFetchTool.ts validateInput + utils.ts validateURL)
        url_error = _validate_url(url)
        if url_error:
            return ToolResult(status="error", content=f"Error: {url_error}")

        url = _normalize_url(url)

        # SSRF protection: reject private/loopback addresses
        parsed = urlparse(url)
        if _is_private_host(parsed.hostname or ""):
            return ToolResult(
                status="error",
                content="Error: Requests to private/internal addresses are not allowed.",
            )

        # Cache check
        if self._cache is not None:
            cached = self._cache.get(url)
            if cached is not None:
                return ToolResult(
                    status="success", content=cached,
                    payload={"prompt": prompt_text} if prompt_text else {},
                )

        # Fetch — same-host redirects only (CC utils.ts isPermittedRedirect)
        try:
            original_host = (parsed.hostname or "").lstrip("www.")
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                response = client.get(url, headers=BROWSER_HEADERS)

                # Handle redirects: only follow same-host
                redirect_count = 0
                while response.is_redirect and redirect_count < 5:
                    redirect_url = str(response.next_request.url) if response.next_request else ""
                    redirect_host = urlparse(redirect_url).hostname or ""
                    if redirect_host.lstrip("www.") != original_host:
                        return ToolResult(
                            status="success",
                            content=(
                                f"REDIRECT DETECTED: {url} redirects to {redirect_url}\n"
                                "The redirect crosses domains. Re-fetch the target URL if needed."
                            ),
                            payload={"prompt": prompt_text} if prompt_text else {},
                        )
                    response = client.get(redirect_url, headers=BROWSER_HEADERS)
                    redirect_count += 1

                if response.status_code in (403, 429):
                    _time.sleep(1.5)
                    response = client.get(url, headers=_ALTERNATE_UA_HEADERS)
                    if response.status_code in (403, 429):
                        return ToolResult(
                            status="error",
                            content=f"Error: HTTP {response.status_code} after retry.",
                        )
                response.raise_for_status()
        except Exception as exc:
            return ToolResult(status="error", content=f"Error: {type(exc).__name__}: {exc}")

        # Response size check (CC utils.ts MAX_HTTP_CONTENT_LENGTH)
        content_length = int(response.headers.get("content-length", 0))
        if content_length > _MAX_HTTP_CONTENT_LENGTH or len(response.content) > _MAX_HTTP_CONTENT_LENGTH:
            return ToolResult(
                status="error",
                content="Error: Response too large (>10MB).",
            )

        content_type = response.headers.get("content-type", "").lower()
        content = _extract_content(response.text, content_type, response.content)

        if self._cache is not None:
            self._cache.put(url, content)

        payload = {"prompt": prompt_text} if prompt_text else {}
        return ToolResult(status="success", content=content, payload=payload)
```

- [ ] **Step 3: Run tests and commit**

```bash
uv run pytest tests/matmaster/tools/builtin/test_web_fetch_tool.py -v
git add matmaster/tools/builtin/web_fetch_tool.py matmaster/tools/builtin/__init__.py tests/matmaster/tools/builtin/test_web_fetch_tool.py
git commit -m "feat(tools): add WebFetchTool with HTML/PDF extraction and disk cache"
```
