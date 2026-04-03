"""Web-oriented GPT-style tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_markdown

from ..base import BaseTool
from ..models import FetchedDocument, SearchResult, ToolResult

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - optional import
    fitz = None


MAX_FETCH_BYTES = 10 * 1024 * 1024
SEARCH_API_ENDPOINT = "https://www.searchapi.io/api/v1/search"


def _normalize_scheme(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "http":
        return parsed._replace(scheme="https").geturl()
    return url


def _extract_markdown(document: FetchedDocument) -> str:
    content_type = document.content_type.lower()
    if "application/pdf" in content_type:
        if fitz is None:
            raise RuntimeError("PyMuPDF is required to extract PDF content.")
        pdf = fitz.open(stream=document.raw_bytes, filetype="pdf")
        try:
            return "\n\n".join(page.get_text().strip() for page in pdf)
        finally:
            pdf.close()

    if "html" in content_type or document.text.lstrip().startswith("<"):
        soup = BeautifulSoup(document.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "aside", "noscript"]):
            tag.decompose()
        return html_to_markdown(str(soup), heading_style="ATX", strip=["img", "svg"]).strip()

    return document.text.strip()


def _default_fetcher(url: str) -> FetchedDocument:
    normalized = _normalize_scheme(url)
    current_url = normalized
    original_host = urlparse(normalized).netloc

    with httpx.Client(timeout=60, follow_redirects=False) as client:
        for _ in range(10):
            response = client.get(current_url)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location", "").strip()
                if not location:
                    response.raise_for_status()
                redirect_url = urljoin(current_url, location)
                redirect_host = urlparse(redirect_url).netloc
                if redirect_host != original_host:
                    return FetchedDocument(
                        requested_url=normalized,
                        final_url=current_url,
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        text="",
                        raw_bytes=b"",
                        redirect_url=redirect_url,
                    )
                current_url = redirect_url
                continue

            response.raise_for_status()
            raw_bytes = response.content[:MAX_FETCH_BYTES]
            return FetchedDocument(
                requested_url=normalized,
                final_url=str(response.url),
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                text=response.text,
                raw_bytes=raw_bytes,
            )

    raise RuntimeError("too many redirects")


def _search_api_backend(query: str) -> list[SearchResult]:
    api_key = os.environ.get("SEARCHAPI_API_KEY") or os.environ.get("SEARCHAPI_KEY")
    if not api_key:
        raise RuntimeError("SEARCHAPI_API_KEY or SEARCHAPI_KEY is required for WebSearch")

    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "hl": "en",
        "gl": "us",
    }
    with httpx.Client(timeout=20) as client:
        response = client.get(SEARCH_API_ENDPOINT, params=params)
        response.raise_for_status()
        payload = response.json()

    results: list[SearchResult] = []
    for item in payload.get("organic_results", []):
        link = str(item.get("link") or "").strip()
        if not link:
            continue
        results.append(
            SearchResult(
                title=str(item.get("title") or "").strip(),
                link=link,
                snippet=str(item.get("snippet") or "").strip(),
            )
        )
    return results


class WebFetchTool(BaseTool):
    """Fetch a web page and optionally summarize it for the caller."""

    name: ClassVar[str] = "WebFetch"
    description: ClassVar[str] = (
        "Fetches a web page and returns readable content. "
        "If a prompt-aware summarizer is configured, the prompt is applied to the page content."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "A fully-qualified URL to fetch."},
            "prompt": {
                "type": "string",
                "description": "What to extract or answer from the fetched page.",
            },
        },
        "required": ["url", "prompt"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        url = arguments["url"].strip()
        prompt = arguments["prompt"].strip()
        if not url:
            return ToolResult.error("Error: url is required.")
        if not prompt:
            return ToolResult.error("Error: prompt is required.")

        cached = self.context.get_cached_web_document(url)
        if cached is None:
            fetcher = self.context.web_fetcher or _default_fetcher
            try:
                document = fetcher(url)
            except Exception as exc:
                return ToolResult.error(f"Error: failed to fetch {url}: {exc}")
            self.context.cache_web_document(url, document)
        else:
            document = cached

        if document.redirect_url:
            return ToolResult.error(
                "Error: cross-host redirects are not followed automatically. "
                f"Call WebFetch again with {document.redirect_url}.",
                redirect_url=document.redirect_url,
            )

        try:
            markdown = _extract_markdown(document)
        except Exception as exc:
            return ToolResult.error(f"Error: failed to extract content from {url}: {exc}")

        prompt_applied = False
        content = markdown
        if self.context.web_fetch_summarizer is not None:
            content = self.context.web_fetch_summarizer(prompt, markdown)
            prompt_applied = True

        return ToolResult.ok(
            content,
            requested_url=document.requested_url,
            final_url=document.final_url,
            prompt_applied=prompt_applied,
            content_type=document.content_type,
        )


class WebSearchTool(BaseTool):
    """Search the web through an injected backend or SearchApi.io."""

    name: ClassVar[str] = "WebSearch"
    description: ClassVar[str] = (
        "Searches the web using a single query string. "
        "Supports optional allowed_domains and blocked_domains filters."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allowlist of result domains.",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional denylist of result domains.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        query = arguments["query"].strip()
        if len(query) < 2:
            return ToolResult.error("Error: query must be at least 2 characters long.")

        allowed_domains = set(arguments.get("allowed_domains") or [])
        blocked_domains = set(arguments.get("blocked_domains") or [])
        if allowed_domains and blocked_domains:
            return ToolResult.error(
                "Error: allowed_domains and blocked_domains are mutually exclusive."
            )

        backend = self.context.web_search_backend or _search_api_backend
        try:
            results = backend(query)
        except Exception as exc:
            return ToolResult.error(f"Error: web search failed: {exc}")

        filtered: list[SearchResult] = []
        for result in results:
            domain = urlparse(result.link).netloc
            if allowed_domains and domain not in allowed_domains:
                continue
            if blocked_domains and domain in blocked_domains:
                continue
            filtered.append(result)

        if not filtered:
            return ToolResult.ok("No search results matched the requested filters.", results=[])

        content = "\n".join(
            f"- [{result.title}]({result.link}): {result.snippet}"
            for result in filtered
        )
        return ToolResult.ok(
            content,
            results=[result.__dict__ for result in filtered],
            query=query,
        )
