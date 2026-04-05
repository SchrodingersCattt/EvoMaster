"""ToolCatalog -- facade over ToolRegistry with compiled tool instances."""

from __future__ import annotations

from typing import Any

from matmaster.tools.tool_compiler import ToolCompiler
from matmaster.tools.tool_registry import Tool, ToolRegistry
from matmaster.types.cancellation import CancellationToken
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ToolInstance
from matmaster.types.topology import RuntimeTopology


class ToolCatalog:
    """Facade over ToolRegistry with versioned overlay registration."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        compiler: ToolCompiler | None = None,
        topology: RuntimeTopology | None = None,
    ) -> None:
        self._registry = registry
        self._compiler = compiler or ToolCompiler()
        self._topology = topology or RuntimeTopology(
            session_kind="local",
            control_root="/tmp/control",
            workspace_root="/tmp/workspace",
        )
        self._compiled_tools: dict[str, ToolInstance] = {}
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def register_overlay(self, tool: Tool, *, source: str = "mcp") -> None:
        self._registry.register(tool, source=source)
        self._compiled_tools[tool.name] = self._compiler.compile(
            tool,
            self._topology,
            source=source,
        )
        self._version += 1

    def get_tool(self, tool_name: str) -> ToolInstance | None:
        cached = self._compiled_tools.get(tool_name)
        if cached is not None:
            return cached

        raw_tool = self._registry.get_raw(tool_name)
        if raw_tool is None:
            return None

        source = self._registry.get_source(tool_name)
        compiled = self._compiler.compile(
            raw_tool,
            self._topology,
            source=source,
        )
        self._compiled_tools[tool_name] = compiled
        return compiled

    def build_definitions(
        self,
        ctx: ToolDescriptionContext | None = None,
    ) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []

        for name in sorted(tool.name for tool in self._registry.all_tools):
            inst = self.get_tool(name)
            if inst is None or not inst.tool_spec.exposed_to_model:
                continue

            raw_tool = self._registry.get_raw(name)
            prompt_exposure = getattr(raw_tool, "prompt_exposure", "system_prompt")

            if ctx is not None:
                prompt = getattr(raw_tool, "prompt", None) if raw_tool else None
                if (
                    prompt_exposure == "tool_description"
                    and callable(prompt)
                    and (prompt_value := prompt(ctx))
                ):
                    description = prompt_value
                else:
                    describe = getattr(raw_tool, "describe", None) if raw_tool else None
                    if callable(describe):
                        description = describe(ctx)
                    else:
                        description = inst.tool_spec.description
            else:
                description = inst.tool_spec.description

            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": inst.tool_spec.tool_name,
                        "description": description,
                        "parameters": inst.tool_spec.args_schema,
                    },
                }
            )

        return definitions

    def collect_prompts(self, ctx: ToolDescriptionContext | None = None) -> str:
        parts: list[str] = []

        for name in sorted(tool.name for tool in self._registry.all_tools):
            inst = self.get_tool(name)
            if inst is None or not inst.tool_spec.exposed_to_model:
                continue

            raw_tool = self._registry.get_raw(name)
            prompt_exposure = getattr(raw_tool, "prompt_exposure", "system_prompt")
            if prompt_exposure != "system_prompt":
                continue
            prompt = getattr(raw_tool, "prompt", None) if raw_tool else None
            if not callable(prompt):
                continue

            value = prompt(ctx)
            if value:
                parts.append(value)

        return "\n\n".join(parts)

    def inject_cancel_token(self, cancel_token: CancellationToken) -> None:
        for tool in self._registry.all_tools:
            tool._cancel_token = cancel_token  # type: ignore[attr-defined]

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, name: str) -> bool:
        return name in self._registry
