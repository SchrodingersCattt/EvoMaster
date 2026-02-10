"""Mat Master 记忆模块常量

- MEMORY_SERVICE_URL: 从环境变量 MEMORY_SERVICE_URL 读取，默认 101.126.90.82:8002
- MEMORY_TOOLS_STORE_RESULTS: 这些工具的结果会写入 session 记忆，供后续检索
"""

import os

MEMORY_WRITER_AGENT_NAME = "memory_writer_agent"

MEMORY_SERVICE_URL = os.getenv("MEMORY_SERVICE_URL", "101.126.90.82:8002")

# Tool names whose results are stored to memory for session "expert intuition":
# structure/metadata, literature/search, and database query results.
MEMORY_TOOLS_STORE_RESULTS = frozenset(
    [
        # Structure and molecule metadata
        "get_structure_info",
        "get_molecule_info",
        # Literature and web search (Science Navigator, etc.)
        "search-papers-enhanced",
        "web-search",
        "extract_info_from_webpage",
        # Dababase
        "fetch_structures_from_database",
    ]
)
