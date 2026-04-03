"""ToolSearch tool -- CC-style deferred tool discovery.

When tool count is large (especially with MCP tools), most are deferred
(model sees only the name, not the schema). ToolSearch lets the model
search by keyword or select by name to get full schema definitions.
"""

from __future__ import annotations

from typing import Any, Callable, ClassVar

from .base import BuiltinTool, ToolResult


class ToolSearchTool(BuiltinTool):
    """Discover and activate deferred tools by keyword or name."""

    name: ClassVar[str] = "ToolSearch"
    description: ClassVar[str] = (
        "Fetches full schema definitions for deferred tools so they can be called.\n\n"
        "Deferred tools are visible by name only (no schema). "
        "Use this tool to search and activate them.\n\n"
        "Query forms:\n"
        '- "select:Read,Edit,Grep" -- fetch exact tools by name\n'
        '- "notebook jupyter" -- keyword search, up to max_results matches\n'
        '- "+slack send" -- require "slack" in name, rank by remaining terms'
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Query to find deferred tools. "
                    'Use "select:<tool_name>" for direct selection, '
                    "or keywords to search."
                ),
            },
            "max_results": {
                "type": "number",
                "default": 5,
                "description": "Maximum number of results to return (default: 5)",
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
        tool_registry: Any | None = None,
        deferred_tools: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """
        Args:
            tool_registry: A ToolRegistry or ToolCatalog instance for looking up tools.
            deferred_tools: Map of tool_name -> {description, json_schema} for deferred tools.
        """
        super().__init__(session=session, workdir=workdir)
        self._registry = tool_registry
        self._deferred = deferred_tools or {}

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        query: str = arguments.get("query", "")
        max_results: int = int(arguments.get("max_results", 5))

        if not query:
            return "Error: query is required"

        # Parse query mode
        if query.startswith("select:"):
            return self._select_tools(query[7:])
        elif query.startswith("+"):
            return self._prefix_search(query[1:].strip(), max_results)
        else:
            return self._keyword_search(query, max_results)

    def _select_tools(self, names_str: str) -> str | ToolResult:
        """Exact selection: select:Read,Edit,Grep"""
        names = [n.strip() for n in names_str.split(",") if n.strip()]
        found: list[dict[str, Any]] = []
        not_found: list[str] = []

        for name in names:
            defn = self._get_tool_definition(name)
            if defn is not None:
                found.append(defn)
            else:
                not_found.append(name)

        return self._format_results(found, not_found)

    def _keyword_search(self, query: str, max_results: int) -> str | ToolResult:
        """Keyword search across all deferred tool names and descriptions."""
        keywords = query.lower().split()
        scored: list[tuple[float, str, dict[str, Any]]] = []

        for name, meta in self._deferred.items():
            desc = meta.get("description", "").lower()
            name_lower = name.lower()
            score = sum(
                2.0 if kw in name_lower else (1.0 if kw in desc else 0.0)
                for kw in keywords
            )
            if score > 0:
                defn = self._get_tool_definition(name)
                if defn is not None:
                    scored.append((score, name, defn))

        scored.sort(key=lambda x: -x[0])
        found = [defn for _, _, defn in scored[:max_results]]
        return self._format_results(found, [])

    def _prefix_search(
        self, query: str, max_results: int
    ) -> str | ToolResult:
        """+prefix keyword: require prefix in name, rank by keywords."""
        parts = query.split(None, 1)
        prefix = parts[0].lower()
        keywords = parts[1].lower().split() if len(parts) > 1 else []

        scored: list[tuple[float, str, dict[str, Any]]] = []

        for name, meta in self._deferred.items():
            if prefix not in name.lower():
                continue
            desc = meta.get("description", "").lower()
            score = sum(
                2.0 if kw in name.lower() else (1.0 if kw in desc else 0.0)
                for kw in keywords
            ) if keywords else 1.0

            defn = self._get_tool_definition(name)
            if defn is not None:
                scored.append((score, name, defn))

        scored.sort(key=lambda x: -x[0])
        found = [defn for _, _, defn in scored[:max_results]]
        return self._format_results(found, [])

    def _get_tool_definition(self, name: str) -> dict[str, Any] | None:
        """Look up full tool definition by name."""
        # Try deferred store first
        if name in self._deferred:
            meta = self._deferred[name]
            return {
                "name": name,
                "description": meta.get("description", ""),
                "parameters": meta.get("json_schema", meta.get("parameters", {})),
            }

        # Try registry
        if self._registry is not None:
            if hasattr(self._registry, "get_tool"):
                tool_instance = self._registry.get_tool(name)
                if tool_instance is not None:
                    spec = getattr(tool_instance, "tool_spec", None)
                    if spec:
                        return {
                            "name": spec.tool_name,
                            "description": spec.description,
                            "parameters": spec.args_schema,
                        }
            # Fallback to raw registry
            if hasattr(self._registry, "_tools"):
                tool = self._registry._tools.get(name)
                if tool is not None:
                    return {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.json_schema,
                    }
        return None

    @staticmethod
    def _format_results(
        found: list[dict[str, Any]], not_found: list[str]
    ) -> str | ToolResult:
        """Format tool definitions as structured output."""
        if not found and not not_found:
            return "No matching tools found."

        parts: list[str] = []
        for defn in found:
            parts.append(
                f"Tool: {defn['name']}\n"
                f"Description: {defn['description'][:200]}\n"
                f"Parameters: {defn['parameters']}\n"
            )

        if not_found:
            parts.append(f"Not found: {', '.join(not_found)}")

        return ToolResult.ok(
            "\n---\n".join(parts),
            found_count=len(found),
            not_found=not_found,
        )
