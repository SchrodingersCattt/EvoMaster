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
