"""Exp -- config-driven assembly layer.

Exp is a concrete class that transforms an ExpConfig + PlaygroundContext
into an AgentRuntimeSpec (assemble), builds runtime resources (build_runtime),
and executes the agent loop (run_stream).

Three-phase lifecycle:
1. assemble(ctx) -- pure data transform: config + ctx -> AgentRuntimeSpec
2. build_runtime(ctx) -- resource creation: tools, prompt, kernel -> AgentRuntime
3. run_stream(ctx, task, ...) -- build_runtime -> kernel.run_stream -> cleanup

Cleanup: Exp owns capability resource cleanup via _cleanup_callbacks.
run_stream() wraps kernel.run_stream in try/finally to guarantee cleanup
even when the kernel raises.
"""

from __future__ import annotations

import inspect
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matmaster.config.exp import ExpConfig
from matmaster.core.context_builder import ContextBuilder
from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.context import PlaygroundContext
from matmaster.types.runtime import AgentRuntime, AgentRuntimeSpec

if TYPE_CHECKING:
    from matmaster.types.messages import Message


class Exp:
    """Config-driven assembly layer.

    Instantiated with an ExpConfig. exp_name comes from config.name
    (defaults to 'direct').

    assemble() is a pure data transform: config + ctx -> AgentRuntimeSpec.
    build_runtime() creates resources (ToolRegistry, ContextBuilder, Kernel).
    run_stream() delegates to build_runtime then kernel.run_stream with cleanup guarantee.
    """

    def __init__(self, config: ExpConfig) -> None:
        self._config = config
        self._cleanup_callbacks: list[Callable[[], Any]] = []
        self._skill_registry: Any = None
        self.logger = logging.getLogger(self.__class__.__name__)

    # ── Properties ───────────────────────────────────────

    @property
    def exp_name(self) -> str:
        """From config.name, defaults to 'direct'."""
        return self._config.name

    # ── Cleanup infrastructure ───────────────────────────

    def _register_cleanup(self, callback: Callable[[], Any]) -> None:
        """Register a cleanup callback to run after kernel execution."""
        self._cleanup_callbacks.append(callback)

    async def _run_cleanup_callbacks(self) -> None:
        """Execute all registered cleanup callbacks then clear the list.

        Each callback runs independently; exceptions are logged but do not
        prevent subsequent callbacks from executing.  Supports both sync and
        async callbacks: uses ``iscoroutinefunction`` first, falls back to
        ``isawaitable`` on the result for wrapped/partial callables.
        """
        for cb in self._cleanup_callbacks:
            try:
                if inspect.iscoroutinefunction(cb):
                    await cb()
                else:
                    result = cb()
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                self.logger.warning(
                    'Cleanup callback %s raised, continuing with remaining callbacks',
                    cb,
                    exc_info=True,
                )
        self._cleanup_callbacks.clear()

    # ── Spawn function factory ────────────────────────────

    @staticmethod
    def _make_spawn_fn(
        ctx: PlaygroundContext,
        source_prefix: str,
    ) -> Any:
        """Create async spawn_fn closure capturing parent runtime context.

        The returned async callable creates a child Exp from exp_name,
        runs it via child_exp.run_stream() with the parent's PlaygroundContext,
        drains the event stream, and returns the final content.
        """

        async def spawn_fn(
            exp_name: str,
            task: str,
            stop_event: threading.Event | None = None,
        ) -> str:
            from matmaster.config.loader import load_exp_config
            from matmaster.core.stream_drain import drain_run_stream

            child_config = load_exp_config(exp_name)
            child_exp = Exp(child_config)
            child_source = f'{source_prefix}:{exp_name}'
            child_spawn_id = uuid.uuid4().hex[:16]
            drain = await drain_run_stream(
                child_exp.run_stream(
                    ctx,
                    task,
                    stop_event=stop_event,
                    source_override=child_source,
                    spawn_id=child_spawn_id,
                )
            )
            if drain.status == "completed" and drain.final_content:
                return drain.final_content
            return (
                f"SubAgent finished with status={drain.status}, reason={drain.reason}"
            )

        return spawn_fn

    # ── Phase 1: assemble ────────────────────────────────

    async def assemble(self, ctx: PlaygroundContext) -> AgentRuntimeSpec:
        """Data transform: config + ctx -> AgentRuntimeSpec."""
        return AgentRuntimeSpec(
            llm_provider=ctx.llm_provider,
            max_turns=self._config.max_turns,
            guards=[],  # Guard instantiation deferred to build_runtime
            compaction=self._config.compaction,
            meta={},
        )

    # ── Active planes derivation ────────────────────────

    @staticmethod
    def _derive_active_planes(
        *,
        has_session: bool,
        builtin_cfg: list[str],
        skills_enabled: bool,
    ) -> frozenset:
        """Derive active tool planes from runtime capabilities.

        Always activates CONTROL_PLANE. Activates SESSION_SHELL and
        SESSION_FS when a session is present. Activates EXTERNAL_SERVICE
        when skills are enabled or external-effect builtins are configured.
        """
        from matmaster.types.topology import ToolPlane

        planes: set[ToolPlane] = {ToolPlane.CONTROL_PLANE}
        if has_session:
            planes |= {ToolPlane.SESSION_SHELL, ToolPlane.SESSION_FS}
        if skills_enabled or any(
            name in builtin_cfg or "*" in builtin_cfg
            for name in ("mm_web_search", "web_fetch", "monitor_job")
        ):
            planes.add(ToolPlane.EXTERNAL_SERVICE)
        return frozenset(planes)

    # ── Phase 2: build_runtime ───────────────────────────

    async def build_runtime(
        self,
        ctx: PlaygroundContext,
        *,
        skills: dict[str, Any] | None = None,
        source_override: str | None = None,
        spawn_id: str | None = None,
    ) -> AgentRuntime:
        """Resource creation: assemble -> tools -> prompt -> kernel.

        Phase 34 ESIN-04: Constructs FullToolRunner + ToolCatalog +
        RuntimeTopology as the default execution path.
        """
        spec = await self.assemble(ctx)

        # 1. Register ALL builtin tools
        registry = ToolRegistry()
        builtin_cfg = self._config.tools.builtin
        if builtin_cfg and ctx.session is not None:
            self._init_builtin_tools(ctx, registry, builtin_cfg)

        # 2. Build ToolCatalog wrapping registry (before skill init for overlay)
        from matmaster.core.capability_policy import DefaultCapabilityPolicy
        from matmaster.core.structural_validation import StructuralValidation
        from matmaster.core.tool_runner import FullToolRunner
        from matmaster.core.tool_scheduler import ToolScheduler
        from matmaster.tools.tool_catalog import ToolCatalog
        from matmaster.tools.tool_compiler import ToolCompiler
        from matmaster.types.topology import RuntimeTopology, SessionCapabilities

        session_caps = SessionCapabilities()
        if ctx.session is not None and hasattr(ctx.session, 'capabilities'):
            caps = ctx.session.capabilities
            if isinstance(caps, SessionCapabilities):
                session_caps = caps

        active_planes = self._derive_active_planes(
            has_session=ctx.session is not None,
            builtin_cfg=self._config.tools.builtin,
            skills_enabled=self._config.skills.enabled,
        )

        topology = RuntimeTopology(
            session_kind=getattr(ctx, 'session_type', None) or 'local',
            control_root=str(ctx.workdir),
            workspace_root=str(ctx.execution_workdir),
            active_planes=active_planes,
            session_capabilities=session_caps,
        )

        compiler = ToolCompiler()
        catalog = ToolCatalog(registry, compiler=compiler, topology=topology)

        # 3. Skills: runtime-injected (pass catalog for overlay registration)
        if skills or self._config.skills.enabled:
            self._init_skill_tools(ctx, registry, skills_config=skills, catalog=catalog)

        # 4. SpawnTool: register after skills but before system prompt
        if ("spawn" in builtin_cfg or builtin_cfg == ["*"]) and ctx.session is not None:
            from matmaster.config.loader import list_available_exps
            from matmaster.tools.builtin.spawn_tool import SpawnTool

            spawn_fn = self._make_spawn_fn(ctx, source_prefix='MatMaster')
            spawn_tool = SpawnTool(
                session=ctx.session,
                workdir=Path(ctx.execution_workdir),
                spawn_fn=spawn_fn,
                available_exps=list_available_exps(),
            )
            registry.register(spawn_tool, source='builtin')

        # 5. System prompt via ContextBuilder
        builder = ContextBuilder()
        system_prompt = builder.build(
            ctx,
            registry,
            system_prompt=self._config.system_prompt,
            identity=self._config.developer_instructions,
            skill_registry=self._skill_registry,
        )

        from matmaster.types.tool_desc_ctx import ToolDescriptionContext
        from matmaster.types.tool_runner_state import ToolRunnerState

        desc_ctx = ToolDescriptionContext(
            session_kind=topology.session_kind,
            workspace_root=topology.workspace_root,
            topology=topology,
        )
        tool_prompts = catalog.collect_prompts(desc_ctx)
        if tool_prompts:
            system_prompt = f"{system_prompt}\n\n{tool_prompts}"

        # 6. Hooks (EventEmitterHook retired in Phase 34; generator events replace it)
        hooks = list(spec.hooks)

        # 7. Compaction: event_sink=None, _run_items() injects local sink at runtime
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
                        'compaction_llm key=%r not found, falling back to main provider',
                        spec.compaction.compaction_llm,
                    )

            compactor = ContextCompactor(
                config=spec.compaction,
                summary_provider=summary_provider,
                event_sink=None,  # _run_items() injects a local deque-backed sink
            )

        guards = list(spec.guards)

        # 8. Build FullToolRunner (ESIN-04: default execution path)
        structural_validation = StructuralValidation()
        capability_policy = DefaultCapabilityPolicy()
        scheduler = ToolScheduler()
        runner_state = ToolRunnerState()
        self._register_cleanup(runner_state.clear)

        full_runner = FullToolRunner(
            catalog=catalog,
            structural_validation=structural_validation,
            guard_pipeline=GuardPipeline(guards),
            capability_policy=capability_policy,
            scheduler=scheduler,
            topology=topology,
            state=runner_state,
        )

        # 9. Assemble final spec with all v2 fields
        spec = spec.model_copy(
            update={
                'tool_catalog': catalog,
                'tool_runner': full_runner,
                'runtime_topology': topology,
                'capability_policy': capability_policy,
                'structural_validation': structural_validation,
                'system_prompt': system_prompt,
                'hooks': hooks,
                'compactor': compactor,
                'guards': guards,
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
        llm_config = getattr(ctx, 'llm_config', None)
        if llm_config is None:
            return None
        try:
            profile = llm_config.get_profile(key)
        except KeyError:
            return None
        return {
            'model': profile.model,
            'api_key': profile.api_key,
            'base_url': profile.base_url,
            'temperature': profile.effective_temperature(),
            'max_tokens': profile.max_tokens,
            'timeout': profile.timeout,
        }

    # ── Phase 3: run_stream ────────────────────────────────

    async def run_stream(
        self,
        ctx: PlaygroundContext,
        task: str,
        *,
        history: list[Message] | None = None,
        stop_event: threading.Event | None = None,
        skills: dict[str, Any] | None = None,
        source_override: str | None = None,
        spawn_id: str | None = None,
    ) -> AsyncIterator[Any]:
        """build_runtime -> kernel.run_stream -> cleanup.

        Async generator that yields BusEvent from the kernel generator.
        try/finally ensures cleanup runs on normal completion, break,
        and exception.
        """
        try:
            runtime = await self.build_runtime(
                ctx,
                skills=skills,
                source_override=source_override,
                spawn_id=spawn_id,
            )
            if ctx.session is not None:
                ctx.session._stop_event = stop_event

            # Inject stop_event into tools for cancel propagation
            catalog = getattr(runtime.spec, "tool_catalog", None)
            if stop_event is not None and catalog is not None:
                catalog.inject_stop_event(stop_event)

            async for event in runtime.kernel.run_stream(
                runtime.spec, task, history=history, stop_event=stop_event
            ):
                yield event
        finally:
            await self._run_cleanup_callbacks()

    # ── Capability initialization helpers ────────────────

    def _init_builtin_tools(
        self,
        ctx: PlaygroundContext,
        registry: ToolRegistry,
        builtin_cfg: list[str],
    ) -> None:
        """Register builtin tools filtered by *builtin_cfg*.

        When ``builtin_cfg`` contains ``"*"`` every builtin is registered
        (original behaviour).  Otherwise only tools whose ``name`` appears
        in the list are registered, cutting prompt-token overhead.

        Native tools (14): BashTool, ListDirTool, ReadTool, WriteTool, EditTool,
        GlobTool, GrepTool, TaskCreate/Get/List/Update/Complete,
        WebSearchTool, WebFetchTool.
        Additional (1): MonitorJobTool (science-specific, native BuiltinTool).
        """
        if ctx.session is None:
            self.logger.warning(
                'No session in PlaygroundContext, skipping builtin tools'
            )
            return

        allow_all = '*' in builtin_cfg
        allowed: set[str] | None = None if allow_all else set(builtin_cfg)

        def _want(name: str) -> bool:
            return allowed is None or name in allowed

        # 1. Native builtin tools (source="builtin")
        from matmaster.tools.builtin import (
            BashTool,
            EditTool,
            GlobTool,
            GrepTool,
            ListDirTool,
            ReadTool,
            TaskCompleteTool,
            TaskCreateTool,
            TaskGetTool,
            TaskListTool,
            TaskUpdateTool,
            WebFetchTool,
            WebSearchTool,
            WriteTool,
        )

        exec_wd = Path(ctx.execution_workdir)
        native_tools = [
            BashTool(session=ctx.session, workdir=exec_wd),
            ListDirTool(session=ctx.session, workdir=exec_wd),
            ReadTool(session=ctx.session, workdir=exec_wd),
            WriteTool(session=ctx.session, workdir=exec_wd),
            EditTool(session=ctx.session, workdir=exec_wd),
            GlobTool(session=ctx.session, workdir=exec_wd),
            GrepTool(session=ctx.session, workdir=exec_wd),
            # Task tools stay on the local control-plane workdir (not execution_workdir):
            # .tasks.json must remain local to the session/task ledger.
            TaskCreateTool(workdir=ctx.workdir),
            TaskGetTool(workdir=ctx.workdir),
            TaskListTool(workdir=ctx.workdir),
            TaskUpdateTool(workdir=ctx.workdir),
            TaskCompleteTool(workdir=ctx.workdir),
            # Web tools: control-plane HTTP, no session dependency
            WebSearchTool(),
            WebFetchTool(workdir=ctx.workdir),
        ]
        registered_native: list[Any] = []
        for tool in native_tools:
            if _want(tool.name):
                registry.register(tool, source='builtin')
                registered_native.append(tool)

        # 2. Additional builtin tools (science-specific, per D-10)
        from matmaster.tools.builtin.monitor_job import MonitorJobTool

        additional_builtins = [
            MonitorJobTool(session=ctx.session, workdir=exec_wd),
        ]
        registered_additional: list[Any] = []
        for tool in additional_builtins:
            if _want(tool.name):
                registry.register(tool, source='builtin')
                registered_additional.append(tool)

        self.logger.debug(
            'Registered %d native + %d additional builtin tools (cfg=%s)',
            len(registered_native),
            len(registered_additional),
            builtin_cfg,
        )

    def _init_skill_tools(
        self,
        ctx: PlaygroundContext,
        registry: ToolRegistry,
        skills_config: dict[str, Any] | None = None,
        catalog: Any | None = None,
    ) -> None:
        """Initialize skill tools with lazy MCP schema injection.

        When catalog is provided, on_skill_hit uses catalog.register_overlay()
        for version-bumped tool injection (ESIN-05). Falls back to
        registry.register() when catalog is None (backward compat).
        """
        skills_cfg = self._config.skills
        if not skills_cfg.enabled:
            return

        import json as _json
        from pathlib import Path

        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.lazy_mcp import LazyMCPConnector, LazyMCPTool
        from matmaster.tools.schema_cache import ToolSchemaCache
        from matmaster.tools.skill_tool import SkillTool

        # Build root list from str | list[str]
        roots_raw = skills_cfg.skills_root
        if isinstance(roots_raw, list):
            roots = [Path(r) for r in roots_raw if r]
        else:
            roots = [Path(roots_raw)] if roots_raw else []
        if not roots:
            self.logger.warning(
                'skills.enabled=true but skills_root is empty, skipping skill init'
            )
            return

        skill_registry = SkillRegistry(roots)
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
                f'MCP runtime config not found: {mcp_runtime_path}. '
                f'Required when skills.enabled=true.'
            )

        mcp_config_file = mcp_config.get('config_file', skills_cfg.mcp_config_file)
        config_path = Path(mcp_config_file)
        if not config_path.is_absolute():
            config_path = Path(skills_cfg.config_dir) / config_path

        if mcp_config.get('path_adaptor') == 'calculation':
            try:
                from matmaster.adaptors.calculation import resolve_mcp_config_path

                config_path = resolve_mcp_config_path(config_path)
            except ImportError:
                pass

        # Load server connection config from JSON
        server_config: dict = {}
        if config_path.exists():
            try:
                raw = _json.loads(config_path.read_text(encoding='utf-8'))
                server_config = raw.get('mcpServers', {})
            except Exception as e:
                self.logger.warning('Failed to load MCP server config: %s', e)

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
                original_name = tool_schema['name']
                prefixed_name = f'{mcp_server}_{original_name}'
                if prefixed_name in registry:
                    continue
                lazy_tool = LazyMCPTool(
                    server_name=mcp_server,
                    tool_name=prefixed_name,
                    remote_tool_name=original_name,
                    description=tool_schema.get('description', ''),
                    input_schema=tool_schema.get('input_schema', {}),
                    connector=connector,
                )
                # ESIN-05: Use catalog.register_overlay() for version-bumped injection
                if catalog is not None:
                    catalog.register_overlay(lazy_tool, source='mcp')
                else:
                    registry.register(lazy_tool, source='mcp')

        skill_tool = SkillTool(
            skill_registry, session=ctx.session, on_skill_hit=on_skill_hit
        )
        registry.register(skill_tool, source='skill')

        self._skill_registry = skill_registry
