"""Built-in tool: SearchApi-backed web search compatible with mat_sn_web-search."""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar

import requests
from pydantic import Field

from evomaster.agent.tools.base import BaseTool, BaseToolParams

SEARCH_API_ENDPOINT = "https://www.searchapi.io/api/v1/search"


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_api_key(session: Any) -> str:
    _ = session
    return _first_non_empty(
        os.environ.get("SEARCHAPI_API_KEY", ""),
        os.environ.get("SEARCHAPI_KEY", ""),
    )


def _normalize_results(payload: dict[str, Any], top_k: int) -> list[dict[str, str]]:
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


class WsbSearchToolParams(BaseToolParams):
    """Search the web via SearchApi and return mat_sn_web-search compatible results."""

    name: ClassVar[str] = "mat_sn_web-search"

    query: str = Field(description="Search query.")
    top_k: int = Field(default=10, description="Maximum number of results to return.")
    gl: str = Field(default="us", description="Country code, e.g. us, cn.")
    hl: str = Field(default="en", description="Language code, e.g. en, zh-cn.")
    page: int = Field(default=1, description="Result page number, starts from 1.")
    location: str = Field(default="", description="Optional canonical location, e.g. New York.")


class WsbSearchTool(BaseTool):
    """Built-in replacement for remote mat_sn_web-search."""

    name: ClassVar[str] = "mat_sn_web-search"
    params_class: ClassVar[type[BaseToolParams]] = WsbSearchToolParams

    def execute(self, session: Any, args_json: str) -> tuple[str, dict]:
        try:
            params = self.parse_params(args_json)
            assert isinstance(params, WsbSearchToolParams)

            query = (params.query or "").strip()
            if not query:
                result = {"status": "error", "results": [], "message": "query is required"}
                obs = json.dumps(result, ensure_ascii=False)
                return obs, {"result": result}

            api_key = _resolve_api_key(session)
            if not api_key:
                result = {
                    "status": "error",
                    "results": [],
                    "message": "Missing SearchApi key. Set SEARCHAPI_API_KEY (or SEARCHAPI_KEY) in environment.",
                }
                obs = json.dumps(result, ensure_ascii=False)
                return obs, {"result": result}

            req_params: dict[str, Any] = {
                "engine": "google",
                "q": query,
                "api_key": api_key,
                "gl": (params.gl or "us").strip(),
                "hl": (params.hl or "en").strip(),
                "page": max(1, int(params.page)),
            }
            location = (params.location or "").strip()
            if location:
                req_params["location"] = location

            response = requests.get(
                SEARCH_API_ENDPOINT,
                params=req_params,
                timeout=20,
                headers={"User-Agent": "matmaster-wsb-search/1.0"},
            )
            response.raise_for_status()
            payload = response.json()

            result = {
                "status": "success",
                "results": _normalize_results(payload, top_k=max(1, int(params.top_k))),
            }
            obs = json.dumps(result, ensure_ascii=False)
            return obs, {"result": result}
        except Exception as exc:
            self.logger.warning("mat_sn_web-search failed: %s", exc)
            result = {"status": "error", "results": [], "message": f"{type(exc).__name__}: {exc}"}
            obs = json.dumps(result, ensure_ascii=False)
            return obs, {"result": result}


def get_wsb_search_tool() -> WsbSearchTool:
    """Return a WsbSearchTool instance for registration."""
    return WsbSearchTool()
