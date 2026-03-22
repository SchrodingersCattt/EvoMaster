"""Exp -- config-driven assembly layer.

Exp is a concrete class that transforms a config dict + PlaygroundContext
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

from matmaster.core.bus import MessageBus
from matmaster.core.context_builder import ContextBuilder
from matmaster.core.hooks import EventEmitterHook
from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import FinishEvent
from matmaster.types.runtime import AgentRuntime, AgentRuntimeSpec

if TYPE_CHECKING:
    from matmaster.core.agent import AgentKernel
    from matmaster.types.messages import Message


class Exp:
    """Config-driven assembly layer.

    Instantiated with a plain dict config. exp_name comes from
    config['name'] (defaults to 'unnamed').

    assemble() is a pure data transform: config + ctx -> AgentRuntimeSpec.
    build_runtime() creates resources (ToolRegistry, ContextBuilder, Kernel).
    run() delegates to build_runtime then kernel.run with cleanup guarantee.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._cleanup_callbacks: list[Callable[[], None]] = []
        self.logger = logging.getLogger(self.__class__.__name__)

    # ── Properties ───────────────────────────────────────

    @property
    def exp_name(self) -> str:
        """From config['name'], defaults to 'unnamed'."""
        return self._config.get("name", "unnamed")

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
        """Data transform: config + ctx -> AgentRuntimeSpec.

        Maps config fields to AgentRuntimeSpec fields. Stores prompt
        templates, MCP/skill config in spec.meta. Does NOT create
        ToolRegistry or build system_prompt (that is build_runtime's job).
        """
        max_turns = self._config.get("max_turns", 100)
        guards = list(self._config.get("guards", []))
        mode = self._config.get("mode", "direct")

        # Build meta bag from config keys that need downstream processing
        meta: dict[str, Any] = {}
        for key in ("prompt_template", "skills", "mcp"):
            if key in self._config:
                meta[key] = self._config[key]

        return AgentRuntimeSpec(
            llm_provider=ctx.llm_provider,
            max_turns=max_turns,
            guards=guards,
            mode=mode,
            meta=meta,
        )

    # ── Phase 2: build_runtime ───────────────────────────

    def build_runtime(
        self,
        ctx: PlaygroundContext,
        *,
        bus: MessageBus | None = None,
    ) -> AgentRuntime:
        """Resource creation: assemble -> tools -> MCP -> prompt -> kernel.

        Steps:
        1. Call assemble() to get base spec
        2. Create ToolRegistry + register builtin tools from ctx.session
        3. Init skill tools if config.skills.enabled (stub)
        4. Init MCP tools if config.mcp.servers (stub)
        5. Build system_prompt via ContextBuilder (needs ToolRegistry)
        6. Create EventEmitterHook if bus provided
        7. Update spec via model_copy with runtime-built fields
        8. Create AgentKernel
        9. Return AgentRuntime(kernel, spec, cleanup=self._run_cleanup_callbacks)
        """
        # 1. Assemble base spec
        spec = self.assemble(ctx)

        # 2. Create ToolRegistry and register builtin tools
        registry = ToolRegistry()
        builtin_cfg = self._config.get("tools", {}).get("builtin", [])
        if "*" in builtin_cfg and ctx.session is not None:
            self._init_builtin_tools(ctx, registry)

        # 3. Skill tools (stub -- factory mechanism refined later)
        self._init_skill_tools(ctx, registry)

        # 4. MCP tools (stub -- factory mechanism refined later)
        self._init_mcp_tools(ctx, registry)

        # 5. Build system_prompt via ContextBuilder
        builder = ContextBuilder()
        system_prompt = builder.build(ctx, registry, mode=spec.mode)

        # 6. Hooks: EventEmitterHook if bus provided
        hooks = list(spec.hooks)
        if bus is not None:
            emitter_hook = EventEmitterHook(bus, source=self.exp_name)
            hooks.append(emitter_hook)

        # 7. Update spec with runtime-built fields
        spec = spec.model_copy(
            update={
                "tool_registry": registry,
                "system_prompt": system_prompt,
                "hooks": hooks,
            }
        )

        # 8. Create AgentKernel
        from matmaster.core.agent import AgentKernel  # lazy import to avoid circular

        kernel = AgentKernel()

        # 9. Return AgentRuntime bundle
        return AgentRuntime(
            kernel=kernel,
            spec=spec,
            cleanup=self._run_cleanup_callbacks,
        )

    # ── Phase 3: run ─────────────────────────────────────

    def run(
        self,
        ctx: PlaygroundContext,
        task: str,
        *,
        bus: MessageBus | None = None,
        history: list[Message] | None = None,
        stop_event: threading.Event | None = None,
    ) -> FinishEvent:
        """build_runtime -> kernel.run -> cleanup.

        Args:
            ctx: Playground environment context.
            task: The user's current task/prompt.
            bus: Optional MessageBus for event delivery.
            history: Optional multi-turn conversation history.
            stop_event: External cancellation signal.

        Cleanup callbacks registered during build_runtime() are guaranteed
        to run in the finally block, even when kernel.run() raises.
        """
        runtime = self.build_runtime(ctx, bus=bus)
        try:
            return runtime.kernel.run(
                runtime.spec, task, history=history, stop_event=stop_event
            )
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
        self, ctx: PlaygroundContext, registry: ToolRegistry
    ) -> None:
        """Initialize skill tools (stub -- factory mechanism refined later)."""

    def _init_mcp_tools(
        self, ctx: PlaygroundContext, registry: ToolRegistry
    ) -> None:
        """Initialize MCP tools (stub -- factory mechanism refined later)."""

    # ── Utilities ────────────────────────────────────────

    @staticmethod
    def _load_file_content(path: str | None) -> str:
        """Read file content or return empty string if path is None/missing."""
        if path is None:
            return ""
        try:
            from pathlib import Path

            return Path(path).read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return ""
