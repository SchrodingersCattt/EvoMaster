"""Exp -- config-driven assembly layer.

Exp is a concrete class that transforms an ExpConfig + PlaygroundContext
into an AgentRuntimeSpec (assemble), builds runtime resources (build_runtime),
and executes the agent loop (run).

Three-phase lifecycle:
1. assemble(ctx) -- pure data transform: config + ctx -> AgentRuntimeSpec
2. build_runtime(ctx, bus) -- resource creation: tools, prompt, kernel -> AgentRuntime
3. run(ctx, task, ...) -- build_runtime -> kernel.run -> cleanup

Cleanup: Exp owns capability resource cleanup via _cleanup_callbacks.
run() wraps kernel.run in try/finally to guarantee cleanup even when the
kernel raises.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from matmaster.config.exp import ExpConfig
from matmaster.core.bus import MessageBus
from matmaster.core.context_builder import ContextBuilder
from matmaster.core.hooks import EventEmitterHook
from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import RunResultEvent
from matmaster.types.runtime import AgentRuntime, AgentRuntimeSpec, CompactionConfig

if TYPE_CHECKING:
    from matmaster.core.agent import AgentKernel
    from matmaster.types.messages import Message


class Exp:
    """Config-driven assembly layer.

    Instantiated with an ExpConfig. exp_name comes from config.name
    (defaults to 'direct').

    assemble() is a pure data transform: config + ctx -> AgentRuntimeSpec.
    build_runtime() creates resources (ToolRegistry, ContextBuilder, Kernel).
    run() delegates to build_runtime then kernel.run with cleanup guarantee.
    """

    def __init__(self, config: ExpConfig) -> None:
        self._config = config
        self._cleanup_callbacks: list[Callable[[], None]] = []
        self.logger = logging.getLogger(self.__class__.__name__)

    # ── Properties ───────────────────────────────────────

    @property
    def exp_name(self) -> str:
        """From config.name, defaults to 'direct'."""
        return self._config.name

    # ── Cleanup infrastructure ───────────────────────────

    def _register_cleanup(self, callback: Callable[[], None]) -> None:
        """Register a cleanup callback to run after kernel execution."""
        self._cleanup_callbacks.append(callback)

    def _run_cleanup_callbacks(self) -> None:
        """Execute all registered cleanup callbacks then clear the list.

        Each callback runs independently; exceptions are logged but do not
        prevent subsequent callbacks from executing.
        """
        for cb in self._cleanup_callbacks:
            try:
                cb()
            except Exception:
                self.logger.warning(
                    "Cleanup callback %s raised, continuing with remaining callbacks",
                    cb,
                    exc_info=True,
                )
        self._cleanup_callbacks.clear()

    # ── Phase 1: assemble ────────────────────────────────

    def assemble(self, ctx: PlaygroundContext) -> AgentRuntimeSpec:
        """Data transform: config + ctx -> AgentRuntimeSpec."""
        return AgentRuntimeSpec(
            llm_provider=ctx.llm_provider,
            max_turns=self._config.max_turns,
            guards=[],  # Guard instantiation deferred to build_runtime
            mode=self._config.mode,
            compaction=CompactionConfig(),
            meta={},
        )

    # ── Phase 2: build_runtime ───────────────────────────

    def build_runtime(
        self,
        ctx: PlaygroundContext,
        *,
        bus: MessageBus | None = None,
        skills: dict[str, Any] | None = None,
        mcp: dict[str, Any] | None = None,
    ) -> AgentRuntime:
        """Resource creation: assemble -> tools -> prompt -> kernel."""
        spec = self.assemble(ctx)

        # 1. Register ALL tools before building system prompt
        registry = ToolRegistry()
        builtin_cfg = self._config.tools.builtin
        if "*" in builtin_cfg and ctx.session is not None:
            self._init_builtin_tools(ctx, registry)

        # 2. Skills/MCP: runtime-injected (must be before system prompt)
        if skills:
            self._init_skill_tools(ctx, registry, skills)
        if mcp:
            self._init_mcp_tools(ctx, registry, mcp)

        # 3. System prompt via ContextBuilder
        builder = ContextBuilder()
        identity = self._config.developer_instructions or None
        system_prompt = builder.build(ctx, registry, mode=spec.mode, identity=identity)

        # 4. Hooks
        hooks = list(spec.hooks)
        if bus is not None:
            emitter_hook = EventEmitterHook(bus, source=self.exp_name)
            hooks.append(emitter_hook)

        # 5. Compaction: unchanged, managed by separate process
        compactor = None
        if spec.compaction.enabled and spec.llm_provider is not None:
            from matmaster.core.context_compactor import ContextCompactor

            summary_provider = spec.llm_provider
            if spec.compaction.compaction_llm:
                resolved = self._resolve_compaction_llm(
                    spec.compaction.compaction_llm, ctx
                )
                if resolved:
                    from matmaster.providers.openai_provider import OpenAIProvider

                    summary_provider = OpenAIProvider(**resolved)
                else:
                    self.logger.warning(
                        "compaction_llm key=%r not found, falling back to main provider",
                        spec.compaction.compaction_llm,
                    )

            compactor = ContextCompactor(
                config=spec.compaction,
                summary_provider=summary_provider,
                bus=bus,
            )

        spec = spec.model_copy(
            update={
                "tool_registry": registry,
                "system_prompt": system_prompt,
                "hooks": hooks,
                "compactor": compactor,
            }
        )

        from matmaster.core.agent import AgentKernel

        kernel = AgentKernel()

        return AgentRuntime(
            kernel=kernel,
            spec=spec,
            cleanup=self._run_cleanup_callbacks,
        )

    def _resolve_compaction_llm(
        self, key: str, ctx: PlaygroundContext
    ) -> dict[str, Any] | None:
        """Resolve compaction LLM profile from PlaygroundContext.llm_config."""
        llm_config = getattr(ctx, "llm_config", None)
        if llm_config is None:
            return None
        try:
            profile = llm_config.get_profile(key)
        except KeyError:
            return None
        return {
            "model": profile.model,
            "api_key": profile.api_key,
            "base_url": profile.base_url,
            "temperature": profile.effective_temperature(),
            "max_tokens": profile.max_tokens,
            "timeout": profile.timeout,
        }

    # ── Phase 3: run ─────────────────────────────────────

    def run(
        self,
        ctx: PlaygroundContext,
        task: str,
        *,
        bus: MessageBus | None = None,
        history: list[Message] | None = None,
        stop_event: threading.Event | None = None,
        skills: dict[str, Any] | None = None,
        mcp: dict[str, Any] | None = None,
    ) -> RunResultEvent:
        """build_runtime -> kernel.run -> cleanup."""
        runtime = self.build_runtime(ctx, bus=bus, skills=skills, mcp=mcp)
        try:
            result = runtime.kernel.run(
                runtime.spec, task, history=history, stop_event=stop_event
            )
            return result.event
        finally:
            runtime.cleanup()

    # ── Capability initialization helpers ────────────────

    def _init_builtin_tools(
        self, ctx: PlaygroundContext, registry: ToolRegistry
    ) -> None:
        """Construct and register builtin tools using ctx.session.

        Builtin tools: BashTool, EditorTool, MonitorJobTool.
        Each is wrapped with EvoToolAdapter for matmaster Tool Protocol.
        """
        if ctx.session is None:
            self.logger.warning(
                "No session in PlaygroundContext, skipping builtin tools"
            )
            return

        from evomaster.agent.tools.builtin.bash import BashTool
        from evomaster.agent.tools.builtin.editor import EditorTool
        from evomaster.agent.tools.builtin.monitor_job import MonitorJobTool

        for evo_tool in [BashTool(), EditorTool(), MonitorJobTool()]:
            adapted = EvoToolAdapter(evo_tool, ctx.session)
            registry.register(adapted, source="builtin")

        self.logger.debug("Registered 3 builtin tools via EvoToolAdapter")

    def _init_skill_tools(
        self,
        ctx: PlaygroundContext,
        registry: ToolRegistry,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize skill tools (stub -- factory mechanism refined later)."""

    def _init_mcp_tools(
        self,
        ctx: PlaygroundContext,
        registry: ToolRegistry,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize MCP tools (stub -- factory mechanism refined later)."""

