"""WebFetch tool -- CC-style URL fetching with prompt-based extraction.

Differences from matmaster web_fetch:
- Single URL + prompt (CC) vs URL array without prompt (MM)
- HTML-to-markdown conversion
- 15-minute LRU cache
- Prompt processed by a small model to extract relevant info
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, ClassVar

from .base import BuiltinTool, ToolResult

# Simple TTL cache
_URL_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 900  # 15 minutes
_MAX_CONTENT_SIZE = 100_000  # 100KB markdown limit


def _cache_get(url: str) -> str | None:
    entry = _URL_CACHE.get(url)
    if entry is None:
        return None
    ts, content = entry
    if time.time() - ts > _CACHE_TTL:
        del _URL_CACHE[url]
        return None
    return content


def _cache_set(url: str, content: str) -> None:
    # Evict old entries if cache grows too large
    if len(_URL_CACHE) > 100:
        now = time.time()
        expired = [k for k, (ts, _) in _URL_CACHE.items() if now - ts > _CACHE_TTL]
        for k in expired:
            del _URL_CACHE[k]
    _URL_CACHE[url] = (time.time(), content)


class WebFetchTool(BuiltinTool):
    """Fetch URL content and optionally process with a prompt."""

    name: ClassVar[str] = "WebFetch"
    description: ClassVar[str] = (
        "Fetches content from a URL and processes it using a prompt.\n\n"
        "Usage:\n"
        "- URL must be fully-formed and valid\n"
        "- HTTP URLs are automatically upgraded to HTTPS\n"
        "- The prompt describes what information to extract from the page\n"
        "- Read-only, does not modify any files\n"
        "- Includes a 15-minute cache for faster repeated access\n"
        "- For GitHub URLs, prefer using gh CLI via Bash instead\n\n"
        "IMPORTANT: Will FAIL for authenticated/private URLs. "
        "Use specialized MCP tools for authenticated services."
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
                "description": "The prompt to run on the fetched content",
            },
        },
        "required": ["url", "prompt"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        url: str = arguments.get("url", "")
        prompt: str = arguments.get("prompt", "")

        if not url:
            return "Error: url is required"
        if not prompt:
            return "Error: prompt is required"

        # Upgrade HTTP to HTTPS
        if url.startswith("http://"):
            url = "https://" + url[7:]

        # Check cache
        cached = _cache_get(url)
        if cached is not None:
            return ToolResult.ok(
                cached,
                url=url,
                from_cache=True,
                prompt=prompt,
            )

        # Fetch content
        try:
            content = self._fetch_url(url)
        except Exception as e:
            return f"Error fetching {url}: {e}"

        # Convert HTML to markdown
        markdown = self._html_to_markdown(content)

        # Truncate if too large
        if len(markdown) > _MAX_CONTENT_SIZE:
            markdown = markdown[:_MAX_CONTENT_SIZE] + "\n[Content truncated]"

        # Cache the result
        _cache_set(url, markdown)

        # Return content with prompt context
        # In CC, this would be processed by a small model (Haiku).
        # Here we return the raw markdown for the calling model to process.
        return ToolResult.ok(
            markdown,
            url=url,
            prompt=prompt,
            content_length=len(markdown),
        )

    @staticmethod
    def _fetch_url(url: str) -> str:
        """Fetch URL content using urllib (no external dependencies)."""
        import urllib.request
        import urllib.error

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MatMaster/2.0; +https://matmaster.dev)"
            ),
            "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
        }
        req = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"URL error: {e.reason}") from e

    @staticmethod
    def _html_to_markdown(html: str) -> str:
        """Basic HTML-to-markdown conversion.

        Uses html2text if available, otherwise strips tags with regex.
        """
        try:
            import html2text  # type: ignore[import-untyped]

            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            h.body_width = 0  # No wrapping
            return h.handle(html)
        except ImportError:
            pass

        # Fallback: basic tag stripping
        import re

        # Remove script/style blocks
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # Remove tags
        text = re.sub(r"<[^>]+>", "", text)
        # Decode common entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
        # Collapse whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
