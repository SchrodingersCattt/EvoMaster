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
from matmaster.types.runtime import AgentRuntime, AgentRuntimeSpec, CompactionConfig, KernelResult

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
        if builtin_cfg and ctx.session is not None:
            self._init_builtin_tools(ctx, registry)

        # 2. Skills/MCP: runtime-injected (must be before system prompt)
        if skills or self._config.skills.enabled:
            self._init_skill_tools(ctx, registry, skills_config=skills)
        if mcp:
            self._init_mcp_tools(ctx, registry, mcp)

        # 3. System prompt via ContextBuilder
        builder = ContextBuilder()
        system_prompt = builder.build(
            ctx, registry,
            system_prompt=self._config.system_prompt,
            identity=self._config.developer_instructions,
            skill_registry=getattr(self, "_skill_registry", None),
        )

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
    ) -> KernelResult:
        """build_runtime -> kernel.run -> cleanup."""
        runtime = self.build_runtime(ctx, bus=bus, skills=skills, mcp=mcp)
        try:
            result = runtime.kernel.run(
                runtime.spec, task, history=history, stop_event=stop_event
            )
            return result.result
        finally:
            runtime.cleanup()

    # ── Capability initialization helpers ────────────────

    def _init_builtin_tools(
        self, ctx: PlaygroundContext, registry: ToolRegistry
    ) -> None:
        """Register builtin tools: native (source='builtin') + evo adapter (source='builtin_evo').

        Native tools (12): BashTool, ListDirTool, ReadTool, WriteTool, EditTool,
        GlobTool, GrepTool, TaskCreate/Get/List/Update/Complete.
        Evo adapter (1): MonitorJobTool (science-specific, retained).
        """
        if ctx.session is None:
            self.logger.warning(
                "No session in PlaygroundContext, skipping builtin tools"
            )
            return

        # 1. Native builtin tools (source="builtin")
        from matmaster.tools.builtin import (
            BashTool,
            EditTool,
            GlobTool,
            GrepTool,
            ListDirTool,
            ReadTool,
            ReadTracker,
            TaskCompleteTool,
            TaskCreateTool,
            TaskGetTool,
            TaskListTool,
            TaskUpdateTool,
            WriteTool,
        )

        # Create ReadTracker shared instance for Read-Before-Modify protocol
        tracker = ReadTracker()
        self._register_cleanup(tracker.clear)

        native_tools = [
            BashTool(session=ctx.session, workdir=ctx.workdir),
            ListDirTool(session=ctx.session, workdir=ctx.workdir),
            ReadTool(session=ctx.session, workdir=ctx.workdir, tracker=tracker),
            WriteTool(session=ctx.session, workdir=ctx.workdir, tracker=tracker),
            EditTool(session=ctx.session, workdir=ctx.workdir, tracker=tracker),
            GlobTool(session=ctx.session, workdir=ctx.workdir),
            GrepTool(session=ctx.session, workdir=ctx.workdir),
            TaskCreateTool(workdir=ctx.workdir),
            TaskGetTool(workdir=ctx.workdir),
            TaskListTool(workdir=ctx.workdir),
            TaskUpdateTool(workdir=ctx.workdir),
            TaskCompleteTool(workdir=ctx.workdir),
        ]
        for tool in native_tools:
            registry.register(tool, source="builtin")

        # 2. Evo adapter tools (source="builtin_evo")
        #    MonitorJobTool retained (science-specific, no native migration planned)
        from evomaster.agent.tools.builtin.monitor_job import MonitorJobTool

        adapted = EvoToolAdapter(MonitorJobTool(), ctx.session)
        registry.register(adapted, source="builtin_evo")

        self.logger.debug(
            "Registered %d native + 1 evo-adapted builtin tools",
            len(native_tools),
        )

    def _init_skill_tools(
        self,
        ctx: PlaygroundContext,
        registry: ToolRegistry,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize skill tools with lazy MCP schema injection."""
        skills_cfg = self._config.skills
        if not skills_cfg.enabled:
            return

        import json as _json
        from pathlib import Path

        from evomaster.agent.tools.skill import SkillTool
        from evomaster.skills.base import SkillRegistry

        from matmaster.tools.lazy_mcp import LazyMCPConnector, LazyMCPTool
        from matmaster.tools.schema_cache import ToolSchemaCache

        skill_registry = SkillRegistry(Path(skills_cfg.skills_root))
        schema_cache = ToolSchemaCache(Path(skills_cfg.cache_dir))

        # MCP runtime config: ALWAYS self-load from config_dir.
        # Independent of skills_config -- MCP runtime config (path_adaptor,
        # calculation_executors) is a separate concern from skill routing.
        from matmaster.config.loader import _load_raw

        mcp_runtime_path = Path(skills_cfg.config_dir) / skills_cfg.mcp_runtime_file
        if mcp_runtime_path.exists():
            mcp_config = _load_raw(mcp_runtime_path)
        else:
            raise FileNotFoundError(
                f"MCP runtime config not found: {mcp_runtime_path}. "
                f"Required when skills.enabled=true."
            )

        mcp_config_file = mcp_config.get("config_file", skills_cfg.mcp_config_file)
        config_path = Path(mcp_config_file)
        if not config_path.is_absolute():
            config_path = Path(skills_cfg.config_dir) / config_path

        if mcp_config.get("path_adaptor") == "calculation":
            try:
                from evomaster.adaptors.calculation import resolve_mcp_config_path

                config_path = resolve_mcp_config_path(config_path)
            except ImportError:
                pass

        # Load server connection config from JSON
        server_config: dict = {}
        if config_path.exists():
            try:
                raw = _json.loads(config_path.read_text(encoding="utf-8"))
                server_config = raw.get("mcpServers", {})
            except Exception as e:
                self.logger.warning("Failed to load MCP server config: %s", e)

        connector = LazyMCPConnector(
            mcp_server_config=server_config,
            mcp_config=mcp_config,
            session=ctx.session,
        )
        self._register_cleanup(connector.cleanup)

        def on_skill_hit(mcp_server: str) -> None:
            schemas = schema_cache.load(mcp_server)
            if not schemas:
                self.logger.warning(
                    "No cached schema for MCP server '%s', tools not injected",
                    mcp_server,
                )
                return
            for tool_schema in schemas:
                original_name = tool_schema["name"]
                prefixed_name = f"{mcp_server}_{original_name}"
                if prefixed_name in registry:
                    continue
                lazy_tool = LazyMCPTool(
                    server_name=mcp_server,
                    tool_name=prefixed_name,
                    remote_tool_name=original_name,
                    description=tool_schema.get("description", ""),
                    input_schema=tool_schema.get("input_schema", {}),
                    connector=connector,
                )
                registry.register(lazy_tool, source="mcp")

        skill_tool = SkillTool(skill_registry, on_skill_hit=on_skill_hit)
        adapted = EvoToolAdapter(skill_tool, ctx.session)
        registry.register(adapted, source="skill")

        self._skill_registry = skill_registry

    def _init_mcp_tools(
        self,
        ctx: PlaygroundContext,
        registry: ToolRegistry,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize MCP tools (stub -- factory mechanism refined later)."""

