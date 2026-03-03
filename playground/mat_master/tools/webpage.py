"""Built-in tool: fetch and extract text content from web pages.

Replaces the remote mat_doc MCP `extract_info_from_webpage` tool with a
local implementation so that webpage fetching never depends on an external
server connection.
"""
from __future__ import annotations

import json
import logging
import re
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, ClassVar

import requests
from bs4 import BeautifulSoup
from pydantic import Field

from evomaster.agent.tools.base import BaseTool, BaseToolParams

_MAX_CONTENT_LENGTH = 50_000
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}
logger = logging.getLogger(__name__)


def _fetch_webpage_content(url: str) -> str:
    """Fetch and extract plain text from a URL.

    Handles HTML pages (via BeautifulSoup) and PDF responses (via PyMuPDF).
    Output is cleaned and truncated to `_MAX_CONTENT_LENGTH` characters.
    """
    logger.info("Fetching content from URL: %s", url)
    response = requests.get(url, headers=_HEADERS, timeout=15)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    is_pdf = "application/pdf" in content_type or (
        "application/octet-stream" in content_type and url.lower().endswith(".pdf")
    )

    if is_pdf:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=response.content, filetype="pdf")
        text = "".join(page.get_text() for page in doc)
        doc.close()
        content = text
    else:
        raw = response.text
        if raw.strip().startswith("<"):
            try:
                soup = BeautifulSoup(raw, "lxml")
            except Exception:
                soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            lines = (line.strip() for line in soup.get_text().splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            content = " ".join(chunk for chunk in chunks if chunk)
        else:
            content = raw

    content = re.sub(r"\s+", " ", content)
    content = re.sub(r"[^\x20-\x7E\x0A\x0D]", "", content)
    if len(content) > _MAX_CONTENT_LENGTH:
        content = content[:_MAX_CONTENT_LENGTH]
        logger.warning("Webpage content truncated to %d chars", _MAX_CONTENT_LENGTH)
    return content


class ExtractWebpageToolParams(BaseToolParams):
    """Fetch and extract plain-text content from one or more web page URLs.

    Handles both HTML pages and URLs that return a PDF. Results are returned
    keyed by URL; all URLs are processed concurrently.
    """

    name: ClassVar[str] = "extract_info_from_webpage"

    url: list[str] = Field(
        description="List of web page URLs to fetch and extract text from.",
    )


class ExtractWebpageTool(BaseTool):
    """Built-in tool: extract plain-text content from web pages."""

    name: ClassVar[str] = "extract_info_from_webpage"
    params_class: ClassVar[type[BaseToolParams]] = ExtractWebpageToolParams

    def execute(self, session: Any, args_json: str) -> tuple[str, dict]:
        try:
            params = self.parse_params(args_json)
            assert isinstance(params, ExtractWebpageToolParams)
            urls = params.url

            if not urls:
                result = {
                    "message": "url list is empty",
                    "total_processing_time_seconds": 0.0,
                    "time_saving_json_seconds": 0.0,
                }
                return json.dumps(result), {"result": result}

            start = _time.time()
            results: dict[str, Any] = {}

            def _process(u: str):
                t0 = _time.time()
                try:
                    content = _fetch_webpage_content(u)
                    return u, content, _time.time() - t0, None
                except Exception as exc:
                    logger.error("Failed to fetch %s: %s", u, exc, exc_info=True)
                    return u, None, _time.time() - t0, str(exc)

            workers = min(30, len(urls))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_process, u): u for u in urls}
                for future in as_completed(futures):
                    u, content, elapsed, err = future.result()
                    if err is not None:
                        results[f"webpage_detailed_contents from {u}"] = {
                            "error": err,
                            "processing_time_seconds": round(elapsed, 3),
                        }
                    else:
                        results[f"webpage_detailed_contents from {u}"] = {
                            "content": content,
                            "processing_time_seconds": round(elapsed, 3),
                        }

            total = round(_time.time() - start, 3)
            results["total_processing_time_seconds"] = total
            results["time_saving_json_seconds"] = 0.0

            obs = json.dumps(results, ensure_ascii=False)
            return obs, {"result": results}
        except Exception as exc:
            self.logger.warning("extract_info_from_webpage failed: %s", exc)
            return f"Error: {exc}", {"error": str(exc)}


def get_extract_webpage_tool() -> ExtractWebpageTool:
    """Return a single ExtractWebpageTool instance for registration."""
    return ExtractWebpageTool()
