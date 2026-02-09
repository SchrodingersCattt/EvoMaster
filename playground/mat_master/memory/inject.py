"""
Inject short-term working memory into the prompt for tool-parameter filling.

EvoMaster: call get_memory_block_for_prompt(session_id, query) before building
the next LLM request; if non-empty, prepend the returned block to user content
so the model sees session memory when deciding tool args.
"""

import logging
from typing import Optional

from .service import format_short_term_memory

logger = logging.getLogger(__name__)


async def get_memory_block_for_prompt(
    session_id: Optional[str],
    query: str = "",
    limit: int = 10,
    base_url: Optional[str] = None,
) -> str:
    """
    Retrieve session memory for the given query and return a formatted block
    to prepend to the prompt. Returns empty string if no session_id, no memory, or on error.
    """
    if not session_id:
        return ""
    block = await format_short_term_memory(
        query_text=query or "tool parameters",
        session_id=session_id,
        base_url=base_url,
        limit=limit,
    )
    if block and block.strip():
        logger.info(
            "inject_memory session_id=%s query_len=%d block_len=%d",
            session_id,
            len(query),
            len(block),
        )
    return block or ""
