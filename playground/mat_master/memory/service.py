"""
HTTP client for the remote MatMaster memory service (FastAPI).

Provides: memory_write, memory_retrieve, memory_list, format_short_term_memory (all async).
MemoryService wraps these for the agent: get_tools() returns mem_save / mem_recall (use existing port).
Base URL from memory.constant.MEMORY_SERVICE_URL; override via base_url.
Timeouts: connect 3s, read 10s.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, ClassVar, Optional

import aiohttp
from pydantic import Field

from evomaster.agent.tools.base import BaseTool, BaseToolParams

from .constant import MEMORY_SERVICE_URL

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 3
_READ_TIMEOUT = 10
_MEMORY_PATH = "/api/v1/memory"


def _base(base_url: Optional[str] = None) -> str:
    url = (base_url or MEMORY_SERVICE_URL).strip()
    if not url.startswith("http"):
        url = f"http://{url}"
    return url.rstrip("/")


async def memory_write(
    session_id: str,
    text: str,
    metadata: Optional[dict[str, Any]] = None,
    base_url: Optional[str] = None,
) -> None:
    """Write one insight to the memory service for the given session."""
    payload = {
        "session_id": session_id,
        "text": text,
        "metadata": metadata or {},
    }
    try:
        timeout = aiohttp.ClientTimeout(
            connect=_CONNECT_TIMEOUT,
            total=_CONNECT_TIMEOUT + _READ_TIMEOUT,
        )
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
                f"{_base(base_url)}{_MEMORY_PATH}/write",
                json=payload,
            ) as r:
                r.raise_for_status()
    except Exception as e:
        logger.warning("memory_write failed: %s (memory service unavailable; agent continues without persisting this write)", e)


async def memory_retrieve(
    session_id: str,
    query: str,
    limit: int = 10,
    base_url: Optional[str] = None,
) -> list[str]:
    """Retrieve relevant memory texts for the session and query. Returns list of text snippets."""
    payload = {
        "session_id": session_id,
        "query_text": query,
        "n_results": limit,
    }
    try:
        timeout = aiohttp.ClientTimeout(
            connect=_CONNECT_TIMEOUT,
            total=_CONNECT_TIMEOUT + _READ_TIMEOUT,
        )
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
                f"{_base(base_url)}{_MEMORY_PATH}/retrieve",
                json=payload,
            ) as r:
                r.raise_for_status()
                data = await r.json()
        docs = data.get("data") or []
        if not isinstance(docs, list):
            return []
        return [
            (
                (d.get("document") or d.get("text") or "")
                if isinstance(d, dict)
                else str(d)
            )
            for d in docs
        ]
    except Exception as e:
        logger.warning(
            "memory_retrieve failed: %s (memory service unavailable; agent continues without session memory)", e
        )
        return []


async def memory_list(
    session_id: Optional[str] = None,
    limit: Optional[int] = None,
    base_url: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List stored documents (POST body). Server returns {data: [{id, document, metadata}, ...]}."""
    payload: dict[str, Any] = {}
    if session_id is not None:
        payload["session_id"] = session_id
    if limit is not None:
        payload["limit"] = limit
    try:
        timeout = aiohttp.ClientTimeout(
            connect=_CONNECT_TIMEOUT,
            total=_CONNECT_TIMEOUT + _READ_TIMEOUT,
        )
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
                f"{_base(base_url)}{_MEMORY_PATH}/list",
                json=payload,
            ) as r:
                r.raise_for_status()
                data = await r.json()
        raw = data.get("data")
        if not isinstance(raw, list):
            return []
        return [
            (
                {
                    "id": d.get("id"),
                    "document": d.get("document", d.get("text", "")),
                    "metadata": d.get("metadata", {}),
                }
                if isinstance(d, dict)
                else d
            )
            for d in raw
        ]
    except Exception as e:
        logger.warning("memory_list failed: %s", e)
        return []


async def format_short_term_memory(
    query_text: str,
    session_id: str,
    base_url: Optional[str] = None,
    limit: int = 10,
) -> str:
    """
    Retrieve session memory for the given query and format as a single block
    for injection into prompts. Returns empty string if no memory or on error.
    """
    texts = await memory_retrieve(
        session_id=session_id,
        query=query_text or "general",
        limit=limit,
        base_url=base_url,
    )
    if not texts:
        return ""
    lines = [f"- {t.strip()}" for t in texts if (t and isinstance(t, str))]
    if not lines:
        return ""
    return "Session Memory (relevant):\n" + "\n".join(lines)


# --- MemoryService (HTTP, existing port) + mem_save / mem_recall tools ---


class MemoryService:
    """Uses the remote MatMaster memory service (existing port). session_id derived from run_dir."""

    def __init__(self, run_dir: Optional[Path] = None, base_url: Optional[str] = None):
        self.run_dir = Path(run_dir) if run_dir else None
        self.base_url = (base_url or MEMORY_SERVICE_URL).strip()

    def _session_id(self) -> str:
        if self.run_dir is not None:
            return str(self.run_dir.resolve())
        return "default"

    def get_tools(self):
        """Return mem_save and mem_recall tools (call HTTP API on existing port)."""
        return [
            _MemSaveTool(self),
            _MemRecallTool(self),
        ]


class _MemSaveToolParams(BaseToolParams):
    """Save a text snippet to session memory (remote service)."""

    name: ClassVar[str] = "mem_save"

    text: str = Field(description="The text or insight to save (e.g. key finding, parameter, plan step).")
    metadata_note: Optional[str] = Field(
        default=None,
        description="Optional short note or tag for this entry (e.g. 'structure_choice', 'convergence').",
    )


class _MemSaveTool(BaseTool):
    name: ClassVar[str] = "mem_save"
    params_class: ClassVar[type[BaseToolParams]] = _MemSaveToolParams

    def __init__(self, service: MemoryService):
        super().__init__()
        self.service = service

    def execute(self, session: Any, args_json: str) -> tuple[str, dict[str, Any]]:
        try:
            params = self.parse_params(args_json)
            assert isinstance(params, _MemSaveToolParams)
            session_id = self.service._session_id()
            metadata = {"note": params.metadata_note} if params.metadata_note else None
            asyncio.run(
                memory_write(
                    session_id=session_id,
                    text=params.text,
                    metadata=metadata,
                    base_url=self.service.base_url or None,
                )
            )
            return "Saved to memory.", {}
        except Exception as e:
            self.logger.warning("mem_save failed: %s", e)
            return f"Error: {e}", {"error": str(e)}


class _MemRecallToolParams(BaseToolParams):
    """Recall relevant snippets from session memory (remote service)."""

    name: ClassVar[str] = "mem_recall"

    query: Optional[str] = Field(
        default=None,
        description="Optional search phrase; if omitted, returns the most recent entries.",
    )
    limit: int = Field(default=10, description="Maximum number of snippets to return (default 10).")


class _MemRecallTool(BaseTool):
    name: ClassVar[str] = "mem_recall"
    params_class: ClassVar[type[BaseToolParams]] = _MemRecallToolParams

    def __init__(self, service: MemoryService):
        super().__init__()
        self.service = service

    def execute(self, session: Any, args_json: str) -> tuple[str, dict[str, Any]]:
        try:
            params = self.parse_params(args_json)
            assert isinstance(params, _MemRecallToolParams)
            session_id = self.service._session_id()
            texts = asyncio.run(
                memory_retrieve(
                    session_id=session_id,
                    query=params.query or "general",
                    limit=params.limit,
                    base_url=self.service.base_url or None,
                )
            )
            if not texts:
                return "No memory entries found.", {"count": 0}
            block = "\n".join(f"- {t}" for t in texts)
            return f"Recall ({len(texts)}):\n{block}", {"count": len(texts)}
        except Exception as e:
            self.logger.warning("mem_recall failed: %s", e)
            return f"Error: {e}", {"error": str(e)}


def get_memory_tools(service: MemoryService):
    """Return mem_save and mem_recall tools for the given MemoryService (HTTP)."""
    return service.get_tools()
