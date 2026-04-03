"""WebSearch tool -- CC-style web search with domain filtering.

Differences from matmaster mm_web_search:
- Adds allowed_domains / blocked_domains (mutually exclusive)
- CC uses Anthropic API's web_search beta tool internally
- MM uses SearXNG / custom search backend
- Description dynamically injects current year/month
"""

from __future__ import annotations

import datetime
from typing import Any, ClassVar

from .base import BuiltinTool, ToolResult


class WebSearchTool(BuiltinTool):
    """Search the web with optional domain filtering."""

    name: ClassVar[str] = "WebSearch"

    @property  # type: ignore[override]
    def description(self) -> str:  # type: ignore[override]
        """Dynamic description with current year/month injected."""
        now = datetime.datetime.now()
        month = now.strftime("%B")
        year = now.year
        return (
            "Search the web for up-to-date information.\n\n"
            "Usage:\n"
            "- Returns search results with links as markdown hyperlinks\n"
            "- Use for information beyond the model's knowledge cutoff\n"
            "- Domain filtering: use allowed_domains OR blocked_domains (not both)\n"
            "- After answering, include a 'Sources:' section with relevant URLs\n\n"
            f"IMPORTANT: Current month is {month} {year}. "
            f"Use {year} when searching for recent information."
        )

    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 2,
                "description": "The search query to use",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only include results from these domains",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Never include results from these domains",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        search_backend: Any | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._search_backend = search_backend

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        query: str = arguments.get("query", "")
        allowed: list[str] | None = arguments.get("allowed_domains")
        blocked: list[str] | None = arguments.get("blocked_domains")

        if not query or len(query) < 2:
            return "Error: query must be at least 2 characters"

        if allowed and blocked:
            return "Error: allowed_domains and blocked_domains are mutually exclusive"

        # Apply domain filtering to query if using simple search
        effective_query = query
        if allowed:
            site_filter = " OR ".join(f"site:{d}" for d in allowed)
            effective_query = f"({site_filter}) {query}"

        # Use injected search backend if available
        if self._search_backend is not None:
            try:
                results = self._search_backend.search(
                    effective_query,
                    blocked_domains=blocked,
                )
                return self._format_results(results, query)
            except Exception as e:
                return f"Error: search failed: {e}"

        # Fallback: try SearXNG via HTTP
        return self._searxng_search(effective_query, blocked)

    def _searxng_search(
        self, query: str, blocked: list[str] | None
    ) -> str | ToolResult:
        """Search via local SearXNG instance."""
        import urllib.request
        import urllib.parse
        import json

        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "categories": "general",
        })
        url = f"http://localhost:8080/search?{params}"

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            return f"Error: search backend unavailable: {e}"

        results = data.get("results", [])

        # Apply blocked domains filter
        if blocked:
            results = [
                r for r in results
                if not any(d in r.get("url", "") for d in blocked)
            ]

        if not results:
            return f"No results found for: {query}"

        return self._format_results(results[:8], query)

    @staticmethod
    def _format_results(results: list[dict[str, Any]], query: str) -> str:
        """Format search results as markdown."""
        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            snippet = r.get("content", r.get("snippet", ""))
            lines.append(f"{i}. [{title}]({url})")
            if snippet:
                lines.append(f"   {snippet[:200]}")
            lines.append("")
        return "\n".join(lines)
