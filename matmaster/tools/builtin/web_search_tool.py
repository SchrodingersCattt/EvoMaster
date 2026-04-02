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


def _normalize_results(payload: dict[str, Any], top_k: int) -> list[dict[str, str]]:
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

    name: ClassVar[str] = "mm_web_search"
    description: ClassVar[str] = (
        "Search the web (SearchApi.io); use when you need title/link/snippet results.\n\n"
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
            return ToolResult(status="error", content="Error: query is required.")

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
