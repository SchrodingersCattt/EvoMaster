"""DirectExp -- direct execution mode assembly.

Assembles: builtin tools -> skill/MCP tools -> ToolRegistry -> ContextBuilder
-> EventEmitterHook -> AgentRuntimeSpec.

This is the standard assembly path for non-planner experiments.

Phase 6 migration: Builtin tools (BashTool, EditorTool, MonitorJobTool) are
constructed inside assemble() from ctx.session, not passed via constructor.
session, config_dir, and builtin_tools constructor parameters removed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from matmaster.core.context_builder import ContextBuilder
from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter
from matmaster.core.exp import Exp
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.core.bus import MessageBus
from matmaster.core.hooks import EventEmitterHook
from matmaster.types.context import PlaygroundContext
from matmaster.types.guards import Guard
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.runtime import AgentRuntimeSpec

logger = logging.getLogger(__name__)


class DirectExp(Exp):
    """Direct execution mode -- single agent, direct tool use.

    Assembles a complete AgentRuntimeSpec from guards and LLM provider.
    Creates ToolRegistry with builtin tools from ctx.session, builds system
    prompt via ContextBuilder, and wires EventEmitterHook for bus event delivery.

    Phase 6: builtin tools constructed in assemble() from ctx.session.
    Constructor no longer accepts session, config_dir, or builtin_tools.
    """

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        guards: list[Guard] | None = None,
        max_turns: int = 100,
        bus: MessageBus | None = None,
        mcp_config: dict[str, Any] | None = None,
        skill_config: dict[str, Any] | None = None,
        skill_registry_factory: Callable[[], Any] | None = None,
        mcp_manager_factory: Callable[[PlaygroundContext], Any] | None = None,
    ) -> None:
        super().__init__({"name": "Direct"})
        self._llm_provider = llm_provider
        self._guards = list(guards) if guards else []
        self._max_turns = max_turns
        self._bus = bus  # If None, assemble() creates a new one
        self._mcp_config = mcp_config
        self._skill_config = skill_config
        self._skill_registry_factory = skill_registry_factory
        self._mcp_manager_factory = mcp_manager_factory

    def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
        """Assemble AgentRuntimeSpec for direct execution mode.

        Steps:
        1. Build ToolRegistry from builtin tools
        2. Register skill tools (if skill_config enabled)
        3. Register MCP tools (if mcp_config enabled)
        4. Build system prompt via ContextBuilder
        5. Build hooks (MessageBus + EventEmitterHook)
        6. Return complete AgentRuntimeSpec
        """
        # 1. Build ToolRegistry and register builtin tools from ctx.session
        registry = ToolRegistry()
        self._init_builtin_tools(ctx, registry)

        # 2. Register skill tools (Exp-owned)
        owned_skill_registry = self._init_skill_tools(ctx, registry)

        # 3. Register MCP tools (Exp-owned)
        self._init_mcp_tools(ctx, registry)

        # 4. Build system prompt (skill_registry from Exp, not ctx)
        builder = ContextBuilder()
        system_prompt = builder.build(
            ctx,
            registry,
            mode="direct",
            skill_registry=owned_skill_registry,
        )

        # 5. Build hooks (MessageBus + EventEmitterHook + external hooks)
        bus = self._bus or MessageBus()
        emitter_hook = EventEmitterHook(bus, source=self.exp_name)
        external_hooks = kwargs.get("hooks") or []
        all_hooks = [emitter_hook, *external_hooks]

        # 6. Assemble AgentRuntimeSpec
        return AgentRuntimeSpec(
            llm_provider=self._llm_provider,
            tool_registry=registry,
            guards=self._guards,
            max_turns=self._max_turns,
            hooks=all_hooks,
            system_prompt=system_prompt,
            mode="direct",
        )

    # ------------------------------------------------------------------
    # Capability initialization helpers
    # ------------------------------------------------------------------

    def _init_builtin_tools(self, ctx: PlaygroundContext, registry: ToolRegistry) -> None:
        """Construct and register builtin tools using ctx.session (per D-06, D-07, D-08).

        Builtin tools: BashTool, EditorTool, MonitorJobTool.
        Each is wrapped with EvoToolAdapter for matmaster Tool Protocol.
        """
        if ctx.session is None:
            logger.warning("No session in PlaygroundContext, skipping builtin tools")
            return

        from evomaster.agent.tools.builtin.bash import BashTool
        from evomaster.agent.tools.builtin.editor import EditorTool
        from evomaster.agent.tools.builtin.monitor_job import MonitorJobTool

        for evo_tool in [BashTool(), EditorTool(), MonitorJobTool()]:
            adapted = EvoToolAdapter(evo_tool, ctx.session)
            registry.register(adapted, source="builtin")

        logger.debug("Registered 3 builtin tools via EvoToolAdapter")

    def _init_skill_tools(self, ctx: PlaygroundContext, registry: ToolRegistry) -> Any | None:
        """Initialize skill tools if skill_config is enabled.

        Returns the owned skill_registry (for ContextBuilder) or None.
        """
        if not self._skill_config or not self._skill_config.get("enabled"):
            return None

        if self._skill_registry_factory is None:
            logger.debug("Skill enabled but no skill_registry_factory provided, skipping")
            return None

        skill_reg = self._skill_registry_factory()
        if skill_reg is None:
            return None

        # Create EvoMaster SkillTool, wrap with adapter, register
        from evomaster.agent.tools.skill import SkillTool

        evo_skill_tool = SkillTool(skill_reg)
        adapted = EvoToolAdapter(evo_skill_tool, ctx.session)
        registry.register(adapted, source="skill")
        logger.debug("Registered skill tool via EvoToolAdapter")
        return skill_reg

    def _init_mcp_tools(self, ctx: PlaygroundContext, registry: ToolRegistry) -> None:
        """Initialize MCP tools if mcp_config is enabled.

        Uses mcp_manager_factory to create a manager, extracts tools from
        the manager's tool registry, wraps each with EvoToolAdapter, and
        registers them into the matmaster ToolRegistry.

        Registers a cleanup callback for the MCP manager.
        """
        if not self._mcp_config or not self._mcp_config.get("enabled"):
            return

        if self._mcp_manager_factory is None:
            logger.debug("MCP enabled but no mcp_manager_factory provided, skipping")
            return

        manager = self._mcp_manager_factory(ctx)
        if manager is None:
            return

        # Register cleanup for MCP manager
        cleanup_method = getattr(manager, "cleanup", None)
        if cleanup_method is not None:
            self._register_cleanup(cleanup_method)
        else:
            logger.debug("MCP manager has no cleanup method")

        # Extract tools from the EvoMaster ToolRegistry inside the manager
        evo_registry = manager.get_tool_registry()
        if evo_registry is None:
            return

        evo_tools = evo_registry.get_all_tools()
        for evo_tool in evo_tools:
            adapted = EvoToolAdapter(evo_tool, ctx.session)
            registry.register(adapted, source="mcp")

        logger.debug("Registered %d MCP tools via EvoToolAdapter", len(evo_tools))
