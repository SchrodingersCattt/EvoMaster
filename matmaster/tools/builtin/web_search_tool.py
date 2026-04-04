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
