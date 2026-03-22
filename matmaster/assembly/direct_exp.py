"""DirectExp -- direct execution mode assembly.

Assembles: builtin tools -> ToolRegistry -> ContextBuilder -> EventEmitterHook -> AgentRuntimeSpec.
This is the standard assembly path for non-planner experiments.
"""

from __future__ import annotations

from typing import Any

from matmaster.assembly.context_builder import ContextBuilder
from matmaster.assembly.exp import Exp
from matmaster.assembly.tool_registry import Tool, ToolRegistry
from matmaster.bus.queue import MessageBus
from matmaster.engine.hooks import EventEmitterHook
from matmaster.types.context import PlaygroundContext
from matmaster.types.guards import Guard
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.runtime import AgentRuntimeSpec


class DirectExp(Exp):
    """Direct execution mode -- single agent, direct tool use.

    Assembles a complete AgentRuntimeSpec from builtin tools, guards,
    and LLM provider. Creates ToolRegistry, builds system prompt via
    ContextBuilder, and wires EventEmitterHook for bus event delivery.
    """

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        builtin_tools: list[Tool] | None = None,
        guards: list[Guard] | None = None,
        max_turns: int = 100,
        bus: MessageBus | None = None,
    ) -> None:
        super().__init__()
        self._llm_provider = llm_provider
        self._builtin_tools = builtin_tools or []
        self._guards = list(guards) if guards else []
        self._max_turns = max_turns
        self._bus = bus  # If None, assemble() creates a new one

    def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
        """Assemble AgentRuntimeSpec for direct execution mode.

        Steps:
        1. Build ToolRegistry from builtin tools
        2. Build system prompt via ContextBuilder
        3. Build hooks (MessageBus + EventEmitterHook)
        4. Return complete AgentRuntimeSpec
        """
        # 1. Build ToolRegistry
        registry = ToolRegistry()
        for tool in self._builtin_tools:
            registry.register(tool, source="builtin")
        # Future: MCP tools from ctx.mcp_manager (Phase 4)
        # Future: Skill tools from ctx.skill_registry (Phase 4)

        # 2. Build system prompt
        builder = ContextBuilder()
        system_prompt = builder.build(
            ctx,
            registry,
            mode="direct",
            skill_registry=ctx.skill_registry,
        )

        # 3. Build hooks (MessageBus + EventEmitterHook)
        bus = self._bus or MessageBus()
        emitter_hook = EventEmitterHook(bus, source=self.exp_name)

        # 4. Assemble AgentRuntimeSpec
        return AgentRuntimeSpec(
            llm_provider=self._llm_provider,
            tool_registry=registry,
            guards=self._guards,
            max_turns=self._max_turns,
            hooks=[emitter_hook],
            system_prompt=system_prompt,
            mode="direct",
        )
