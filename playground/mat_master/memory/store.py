"""
After-tool callback logic: store selected tool results into session memory.

Used for tools like get_structure_info so structure/file metadata is retrievable
for later plans and parameter decisions. EvoMaster: call store_tool_result_in_memory
after each tool execution when session_id is available (e.g. run_dir or task_id).

Tool names may be prefixed by MCP server;
we strip the first "mat_*_" segment and check the remainder against MEMORY_TOOLS_STORE_RESULTS.
"""

import json
import logging
import re
from typing import Any, Optional, Union

from .constant import MEMORY_TOOLS_STORE_RESULTS
from .service import memory_write

logger = logging.getLogger(__name__)

MAX_STORED_RESULT_CHARS = 12000
MAX_ARGS_CHARS = 800

# MCP tools are registered as mat_<server>_<original_name> (server name may be any case)
_CANONICAL_TOOL_NAME_RE = re.compile(r"^mat_[a-z0-9_]+_(.+)$", re.IGNORECASE)


def _canonical_tool_name(registered_name: str) -> str:
    """Return original tool name; if registered_name is mat_xxx_toolname, return toolname (stripped)."""
    name = (registered_name or "").strip()
    m = _CANONICAL_TOOL_NAME_RE.match(name)
    return m.group(1).strip() if m else name


def _format_tool_result_summary(
    tool_name: str,
    args: dict,
    tool_response: Union[dict, str, Any],
) -> str:
    """Build a short summary string for storage."""
    args_str = json.dumps(args, ensure_ascii=False)[:MAX_ARGS_CHARS]
    if isinstance(tool_response, str):
        result_str = tool_response[:MAX_STORED_RESULT_CHARS]
    elif isinstance(tool_response, dict):
        result_str = json.dumps(tool_response, ensure_ascii=False)[:MAX_STORED_RESULT_CHARS]
    else:
        result_str = str(tool_response)[:MAX_STORED_RESULT_CHARS]
    return f"Tool {tool_name} | args: {args_str} | result: {result_str}"


async def store_tool_result_in_memory(
    session_id: str,
    tool_name: str,
    args: dict,
    tool_response: Union[dict, str, Any],
    base_url: Optional[str] = None,
) -> None:
    """If tool is in MEMORY_TOOLS_STORE_RESULTS, write a summary to the memory service.
    tool_name can be registered name or original name.
    """
    canonical = _canonical_tool_name(tool_name)
    if canonical not in MEMORY_TOOLS_STORE_RESULTS:
        return
    if not session_id:
        logger.debug("store_tool_result_in_memory: no session_id, skip")
        return
    summary = _format_tool_result_summary(tool_name, args, tool_response)
    await memory_write(
        session_id=session_id,
        text=summary,
        metadata={"tool": tool_name, "source": "tool_result"},
        base_url=base_url,
    )
    logger.info("store_tool_result_in_memory session_id=%s tool=%s stored", session_id, tool_name)
