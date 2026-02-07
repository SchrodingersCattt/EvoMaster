"""
Mat Master memory: HTTP service client, format_short_term_memory, store/inject helpers.

Usage:
- memory_write / memory_retrieve / memory_list: low-level API
- format_short_term_memory: retrieve and format for prompt injection
- store_tool_result_in_memory: after tool call, persist selected tool results
- get_memory_block_for_prompt: async helper for prompt injection
- get_memory_writer_instruction: build instruction for a memory-writer agent (e.g. plan phase)
"""

from .constant import (
    MEMORY_SERVICE_URL,
    MEMORY_TOOLS_STORE_RESULTS,
    MEMORY_WRITER_AGENT_NAME,
)
from .inject import get_memory_block_for_prompt
from .schema import MemoryWriterSchema
from .service import (
    format_short_term_memory,
    memory_list,
    memory_retrieve,
    memory_write,
)
from .store import store_tool_result_in_memory
from .utils import get_memory_writer_instruction

__all__ = [
    "MEMORY_SERVICE_URL",
    "MEMORY_TOOLS_STORE_RESULTS",
    "MEMORY_WRITER_AGENT_NAME",
    "MemoryWriterSchema",
    "format_short_term_memory",
    "get_memory_block_for_prompt",
    "get_memory_writer_instruction",
    "memory_list",
    "memory_retrieve",
    "memory_write",
    "store_tool_result_in_memory",
]
