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
            logger.warning("Failed to write cache entry for %s", url, exc_info=True)
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
        "application/octet-stream" in content_type and raw_bytes[:5] == b"%PDF-"
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
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
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
        content = _extract_content(response.text, content_type, response.content)

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
                "description": ("List of URLs to fetch and extract text from."),
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
            return ToolResult(status="error", content="Error: url list is empty.")

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
            futures = {pool.submit(_fetch_single_url, u, self._cache): u for u in urls}
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
