"""Exp -- config-driven assembly layer.

Exp is a concrete class that transforms an ExpConfig + AgentRunContext into an
AgentRuntime (build_runtime) and executes the agent loop (run_stream). The
AgentRuntime bundles the kernel, the kernel-facing AgentKernelRuntime
(AgentKernelSpec + AgentKernelResources), and a cleanup callable.

The ``AgentRunContext`` it consumes keeps physical facts under
``ctx.environment`` (ExecutionEnvironment) and per-run runtime ingredients
under ``ctx.request`` (AgentRunRequest).

Lifecycle:
1. build_runtime(ctx) -- one-shot assembly: config + ctx -> tools, prompt,
   kernel_spec + kernel_resources -> AgentRuntime
2. run_stream(ctx, task, ...) -- thin driver over runtime_scope()

The run lifecycle (build_runtime -> cancel-token injection -> cleanup) lives in
runtime_scope(), a reusable async context manager shared by both run_stream()
and devshell, so neither hand-copies build/inject/cleanup.

Cleanup: Exp owns capability resource cleanup via _cleanup_callbacks.
runtime_scope() wraps the run in try/finally to guarantee cleanup even when the
kernel raises.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matmaster.config.exp import ExpConfig
from matmaster.context.ports import SkillResolver
from matmaster.context.system_prompt import SystemPromptBuilder
from matmaster.core.hooks import HookExecutor
from matmaster.core.path_access import derive_path_access_roots
from matmaster.core.run_context import AgentRunContext
from matmaster.skills.settings import (
    disabled_skill_names_from_remote_settings as _disabled_skill_names_from_remote_settings,
)
from matmaster.skills.settings import (
    disabled_skill_names_from_settings as _disabled_skill_names_from_settings,
)
from matmaster.skills.settings import local_user_skills_root as _local_user_skills_root
from matmaster.skills.settings import remote_skill_roots as _remote_skill_roots
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.cancellation import CancellationToken
from matmaster.types.run_metadata import RunIdentity
from matmaster.types.runtime import (
    AgentKernelResources,
    AgentKernelRuntime,
    AgentKernelSpec,
    AgentRuntime,
)
from matmaster.types.runtime_ports import KernelRuntimePorts

if TYPE_CHECKING:
    from matmaster.types.messages import Message


_LOGGER = logging.getLogger(__name__)


# Builtin tools whose execution reaches outside the workspace.
# Presence of any of these names in the configured builtin list activates
# the EXTERNAL_SERVICE plane in RuntimeTopology.
_EXTERNAL_EFFECT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "WebSearch",
        "WebFetch",
        "PaperSearch",
        "Bohrium",
        # Aliases surfaced by evaluation tooling.
        "mm_web_search",
        "web_fetch",
    }
)

# Builtin tool names that require an active session for execution.
_SESSION_REQUIRING_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "execute_bash",
        "Bash",
        "read_file",
        "Read",
        "write_file",
        "Write",
        "edit_file",
        "Edit",
        "glob",
        "Glob",
        "grep",
        "Grep",
    }
)


def _resolve_skill_config_dir(raw_dir: str) -> Path:
    """Map legacy ``matmaster_config`` references onto this repo's ``config`` dir."""
    candidate = Path(raw_dir)
    if candidate.exists():
        return candidate
    if raw_dir == "matmaster_config":
        compat = Path("config")
        if compat.exists():
            return compat
    return candidate


def _deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


class Exp:
    """Config-driven assembly layer.

    Instantiated with an ExpConfig. exp_name comes from config.name
    (defaults to 'direct').

    build_runtime() creates resources (ToolRegistry, SystemPromptBuilder, Kernel)
    and assembles kernel_spec + kernel_resources into an AgentKernelRuntime.
    run_stream() delegates to build_runtime then kernel.run_stream with cleanup guarantee.
    """

    def __init__(
        self,
        config: ExpConfig,
        *,
        allow_spawn: bool = True,
        exclude_subagents: frozenset[str] | None = None,
    ) -> None:
        self._config = config
        self._allow_spawn = allow_spawn
        self._exclude_subagents: frozenset[str] = exclude_subagents or frozenset()
        self._cleanup_callbacks: list[Callable[[], Any]] = []
        # Core-layer registry serves SkillTool registration and the
        # registry-wide system prompt prefix. Service-layer resolver state is
        # held separately and feeds active-skill prompt rendering.
        self._skill_registry: Any = None
        self._skill_resolver: SkillResolver | None = None
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
                    "Cleanup callback %s raised, continuing with remaining callbacks",
                    cb,
                    exc_info=True,
                )
        self._cleanup_callbacks.clear()

    # ── Child runtime factory ─────────────────────────────

    def _make_child_run_factory(
        self, ctx: AgentRunContext
    ) -> Callable[..., AsyncIterator[Any]]:
        """Seam :class:`SubagentOrchestrator` uses to run one child agent.

        Exp owns *assembling* the child runtime -- load its config, construct a
        child Exp with spawn disabled (one-level recursion cap), and drive its
        ``run_stream`` with the parent ``ctx``. The orchestrator owns the spawn
        lifecycle (id, hooks, event retag, drain) around the returned stream.
        """
        skill_resolver = self._skill_resolver

        def child_run_factory(
            exp_name: str,
            task: str,
            *,
            cancel_token: CancellationToken | None = None,
            spawn_id: str | None = None,
        ) -> AsyncIterator[Any]:
            from matmaster.config.loader import load_exp_config

            child_exp = Exp(load_exp_config(exp_name), allow_spawn=False)
            return child_exp.run_stream(
                ctx,
                task,
                cancel_token=cancel_token,
                spawn_id=spawn_id,
                skill_resolver=skill_resolver,
            )

        return child_run_factory

    # ── Run identity ─────────────────────────────────────

    @staticmethod
    def _build_run_identity(
        ctx: AgentRunContext,
        *,
        spawn_id: str | None,
    ) -> RunIdentity:
        return RunIdentity(
            task_id=ctx.environment.metadata.task_id,
            session_id=ctx.environment.session_id,
            spawn_id=spawn_id,
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
        cfg_set = set(builtin_cfg)
        if skills_enabled or "*" in cfg_set or cfg_set & _EXTERNAL_EFFECT_TOOL_NAMES:
            planes.add(ToolPlane.EXTERNAL_SERVICE)
        return frozenset(planes)

    # ── Runtime construction ─────────────────────────────

    async def build_runtime(
        self,
        ctx: AgentRunContext,
        *,
        skills: dict[str, Any] | None = None,
        skill_resolver: SkillResolver | None = None,
        spawn_id: str | None = None,
    ) -> AgentRuntime:
        """One-shot assembly: tools -> prompt -> context assembly -> kernel.

        Constructs FullToolRunner + ToolCatalog + RuntimeTopology as the
        default execution path, then bundles kernel_spec + kernel_resources
        into an AgentKernelRuntime (see the §5.4 six-step ordering).
        """
        env = ctx.environment
        request = ctx.request

        # Discard any registry from a prior run so a turn that turns skills off
        # cannot expose stale state to the prompt builder.
        self._skill_registry = None
        from matmaster.context.skill_resolver import SkillRegistryResolver
        from matmaster.core.runtime_context_assembly import empty_skill_resolver

        self._skill_resolver = skill_resolver or empty_skill_resolver

        registry = ToolRegistry()
        builtin_cfg = self._config.tools.builtin
        path_access_roots = derive_path_access_roots(env)
        if builtin_cfg:
            self._init_builtin_tools(
                ctx,
                registry,
                builtin_cfg,
                spawn_id=spawn_id,
                path_access_roots=path_access_roots,
            )

        from matmaster.core.capability_policy import DefaultCapabilityPolicy
        from matmaster.core.structural_validation import StructuralValidation
        from matmaster.core.tool_runner import FullToolRunner
        from matmaster.core.tool_scheduler import ToolScheduler
        from matmaster.tools.tool_catalog import ToolCatalog
        from matmaster.tools.tool_compiler import ToolCompiler
        from matmaster.types.topology import RuntimeTopology, SessionCapabilities

        session_caps = SessionCapabilities()
        if env.session is not None and hasattr(env.session, "capabilities"):
            caps = env.session.capabilities
            if isinstance(caps, SessionCapabilities):
                session_caps = caps

        active_planes = self._derive_active_planes(
            has_session=env.session is not None,
            builtin_cfg=self._config.tools.builtin,
            skills_enabled=self._config.skills.enabled,
        )

        topology = RuntimeTopology(
            session_kind=env.session_type or "local",
            control_root=str(env.workdir),
            workspace_root=str(env.execution_workdir),
            active_planes=active_planes,
            session_capabilities=session_caps,
            path_access_roots=path_access_roots,
        )

        compiler = ToolCompiler()
        catalog = ToolCatalog(registry, compiler=compiler, topology=topology)
        hook_executor = HookExecutor()

        if skills or self._config.skills.enabled:
            self._init_skill_tools(ctx, registry, skills_config=skills, catalog=catalog)
        if skill_resolver is None:
            self._skill_resolver = SkillRegistryResolver(self._skill_registry)

        # When allow_spawn is False (child Exp), spawn_fn is None, which causes
        # AgentTool to set exposed_to_model=False (hidden from LLM but still
        # in catalog).
        if "Agent" in builtin_cfg or "*" in builtin_cfg:
            from matmaster.config.loader import list_model_visible_exps
            from matmaster.tools.builtin import AgentTool

            spawn_fn = None
            available_exps = None
            if self._allow_spawn:
                from matmaster.core.subagent_orchestrator import SubagentOrchestrator

                orchestrator = SubagentOrchestrator(
                    child_run_factory=self._make_child_run_factory(ctx),
                    child_event_sink=request.ports.child_event_forward_sink,
                    hook_executor=hook_executor,
                    parent_session_id=env.session_id,
                )
                spawn_fn = orchestrator.make_spawn_fn()
                available_exps = list_model_visible_exps()
                if self._exclude_subagents:
                    available_exps = [
                        e
                        for e in available_exps
                        if e.name not in self._exclude_subagents
                    ]
            agent_tool = AgentTool(
                session=env.session,
                workdir=(
                    Path(env.execution_workdir)
                    if env.session is not None
                    else env.workdir
                ),
                spawn_fn=spawn_fn,
                available_exps=available_exps,
            )
            registry.register(agent_tool, source="builtin")

        system_prompt_builder = SystemPromptBuilder()
        system_prompt = system_prompt_builder.build_system_prompt(
            registry,
            system_prompt=self._config.system_prompt,
            identity=self._config.developer_instructions,
            skill_registry=self._skill_registry,
        )

        from matmaster.core.runtime_context_assembly import (
            build_runtime_context_assembly,
        )
        from matmaster.tools.builtin.bohrium_tool.registry import JobRegistry
        from matmaster.types.tool_runner_state import ToolRunnerState

        runtime_context = build_runtime_context_assembly(
            llm_provider=request.llm_provider,
            compaction=self._config.compaction,
            ctx=ctx,
            skill_resolver=self._skill_resolver,
            spawn_id=spawn_id,
            logger=self.logger,
        )

        structural_validation = StructuralValidation()
        capability_policy = DefaultCapabilityPolicy()
        scheduler = ToolScheduler()
        runner_state = ToolRunnerState()
        bohrium_registry = JobRegistry.rebuild_from_events(
            request.bohrium_rebuild_events
        )
        runner_state.set("bohrium_job_registry", bohrium_registry)
        figure_upload_config = request.ports.figure_upload.config
        if figure_upload_config is not None:
            runner_state.set("figure_upload_config", figure_upload_config)
        self._register_cleanup(runner_state.clear)

        full_runner = FullToolRunner(
            catalog=catalog,
            structural_validation=structural_validation,
            capability_policy=capability_policy,
            scheduler=scheduler,
            topology=topology,
            hook_executor=hook_executor,
            state=runner_state,
        )

        checkpoint_sink_factory = request.ports.compaction.checkpoint_sink_factory
        checkpoint_sink = None
        if callable(checkpoint_sink_factory):
            checkpoint_sink = checkpoint_sink_factory(spawn_id=spawn_id)
        pre_compaction_barrier = request.ports.compaction.pre_compaction_barrier

        kernel_spec = AgentKernelSpec(
            system_prompt=system_prompt,
            max_turns=self._config.max_turns,
            compaction=self._config.compaction,
            run_identity=self._build_run_identity(ctx, spawn_id=spawn_id),
            turn_input=request.turn_input,
            llm_model=request.llm_model,
            llm_model_profile=request.llm_model_profile,
            llm_model_route=request.llm_model_route,
        )
        kernel_resources = AgentKernelResources(
            llm_provider=request.llm_provider,
            runtime_ports=KernelRuntimePorts(
                checkpoint_sink=checkpoint_sink,
                pre_compaction_barrier=pre_compaction_barrier,
                interrupt_checker=request.ports.interrupt_checker,
            ),
            tool_runner=full_runner,
            tool_catalog=catalog,
            runtime_topology=topology,
            hook_executor=hook_executor,
            compactor=runtime_context.compactor,
            capability_policy=capability_policy,
            structural_validation=structural_validation,
        )
        kernel_runtime = AgentKernelRuntime(
            spec=kernel_spec,
            resources=kernel_resources,
        )

        from matmaster.core.agent import AgentKernel

        kernel = AgentKernel()

        return AgentRuntime(
            kernel=kernel,
            kernel_runtime=kernel_runtime,
            cleanup=self._run_cleanup_callbacks,
            context_runtime=runtime_context.context_runtime,
        )

    # ── Runtime scope + run_stream ───────────────────────

    @asynccontextmanager
    async def runtime_scope(
        self,
        ctx: AgentRunContext,
        cancel_token: CancellationToken | None = None,
        *,
        skills: dict[str, Any] | None = None,
        skill_resolver: SkillResolver | None = None,
        spawn_id: str | None = None,
    ) -> AsyncIterator[AgentRuntime]:
        """Reusable run lifecycle: build_runtime -> cancel-token injection
        -> (caller drives kernel.run_stream) -> guaranteed cleanup.

        Yields the built AgentRuntime; the caller drives
        ``runtime.kernel.run_stream(...)`` itself. The try/finally guarantees
        capability cleanup on normal completion, break, and exception, so every
        driver (service run_stream, devshell) shares one lifecycle instead of
        hand-copying build/inject/cleanup.
        """
        try:
            runtime = await self.build_runtime(
                ctx,
                skills=skills,
                skill_resolver=skill_resolver,
                spawn_id=spawn_id,
            )
            kernel_runtime = runtime.kernel_runtime
            if ctx.environment.session is not None:
                ctx.environment.session._cancel_token = cancel_token

            # Inject cancel_token into tools for cancel propagation.
            catalog = kernel_runtime.resources.tool_catalog
            if cancel_token is not None and catalog is not None:
                catalog.inject_cancel_token(cancel_token)

            yield runtime
        finally:
            await self._run_cleanup_callbacks()

    async def run_stream(
        self,
        ctx: AgentRunContext,
        task: str,
        *,
        history: list[Message] | None = None,
        cancel_token: CancellationToken | None = None,
        skills: dict[str, Any] | None = None,
        skill_resolver: SkillResolver | None = None,
        spawn_id: str | None = None,
    ) -> AsyncIterator[Any]:
        """Thin driver over :meth:`runtime_scope`.

        Async generator that yields BusEvent from the kernel generator.
        Cleanup is guaranteed by the scope's try/finally on normal completion,
        break, and exception.
        """
        async with self.runtime_scope(
            ctx,
            cancel_token,
            skills=skills,
            skill_resolver=skill_resolver,
            spawn_id=spawn_id,
        ) as runtime:
            async for event in runtime.kernel.run_stream(
                runtime.kernel_runtime,
                task,
                history=history,
                cancel_token=cancel_token,
            ):
                yield event

    # ── Capability initialization helpers ────────────────

    def _init_builtin_tools(
        self,
        ctx: AgentRunContext,
        registry: ToolRegistry,
        builtin_cfg: list[str],
        *,
        spawn_id: str | None = None,
        path_access_roots: tuple[Any, ...] = (),
    ) -> None:
        """Register builtin tools filtered by *builtin_cfg*.

        When ``builtin_cfg`` contains ``"*"`` every builtin is registered
        (original behaviour).  Otherwise only tools whose ``name`` appears
        in the list are registered, cutting prompt-token overhead.

        Tools are split into two categories:
        - Session-requiring: BashTool, ReadTool, WriteTool, EditTool,
          GlobTool, GrepTool (need ctx.environment.session for execution)
        - Sessionless: TodoWriteTool, WebSearchTool, WebFetchTool
          (operate without a session; AgentTool is registered separately
          in build_runtime)

        When ctx.environment.session is None, only sessionless tools are
        registered.
        """
        allow_all = "*" in builtin_cfg
        allowed: set[str] | None = None if allow_all else set(builtin_cfg)

        def _want(name: str) -> bool:
            return allowed is None or name in allowed

        from matmaster.tools.builtin import (
            AskQuestionTool,
            BashTool,
            BohriumTool,
            EditTool,
            GlobTool,
            GrepTool,
            ReadTool,
            TodoWriteTool,
            WebFetchTool,
            WebSearchTool,
            WriteTool,
        )

        env = ctx.environment
        exec_wd = Path(env.execution_workdir)
        has_session = env.session is not None
        search_path_roots = tuple(
            root.root
            for root in path_access_roots
            if "search" in getattr(root, "permissions", frozenset())
        )

        session_tools: list[Any] = []
        if has_session:
            session_tools = [
                BashTool(session=env.session, workdir=exec_wd),
                ReadTool(session=env.session, workdir=exec_wd),
                WriteTool(session=env.session, workdir=exec_wd),
                EditTool(session=env.session, workdir=exec_wd),
                GlobTool(
                    session=env.session,
                    workdir=exec_wd,
                    path_access_roots=search_path_roots,
                ),
                GrepTool(
                    session=env.session,
                    workdir=exec_wd,
                    path_access_roots=search_path_roots,
                ),
            ]
        elif allow_all or (
            allowed is not None and allowed & _SESSION_REQUIRING_TOOL_NAMES
        ):
            self.logger.debug(
                "No session in ExecutionEnvironment, skipping session-requiring tools"
            )

        sessionless_tools: list[Any] = [
            TodoWriteTool(workdir=env.workdir),
            WebSearchTool(),
            WebFetchTool(workdir=env.workdir),
            BohriumTool(session=env.session, workdir=env.workdir),
        ]

        interaction_bridge = (
            ctx.request.interaction_bridge if spawn_id is None else None
        )
        control_tools: list[Any] = [
            AskQuestionTool(
                session=env.session,
                workdir=exec_wd if env.session is not None else env.workdir,
                bridge=interaction_bridge,
            ),
        ]

        registered: list[Any] = []
        for tool in session_tools + sessionless_tools + control_tools:
            if _want(tool.name):
                registry.register(tool, source="builtin")
                registered.append(tool)

        self.logger.debug(
            "Registered %d builtin tools (cfg=%s, session=%s)",
            len(registered),
            builtin_cfg,
            "present" if has_session else "absent",
        )

    def _init_skill_tools(
        self,
        ctx: AgentRunContext,
        registry: ToolRegistry,
        skills_config: dict[str, Any] | None = None,
        catalog: Any | None = None,
    ) -> None:
        """Initialize skill tools with lazy MCP schema injection.

        When catalog is provided, on_skill_hit uses catalog.register_overlay()
        for version-bumped tool injection. Falls back to registry.register()
        when catalog is None.
        """
        skills_cfg = self._config.skills
        if not skills_cfg.enabled:
            return

        env = ctx.environment

        from matmaster.skills.registry import SkillRegistry
        from matmaster.tools.builtin.skill_tool import LegacyUseSkillTool, SkillTool
        from matmaster.tools.lazy_mcp import LazyMCPConnector, LazyMCPTool
        from matmaster.tools.schema_cache import ToolSchemaCache

        # Build root list from str | list[str]
        roots_raw = skills_cfg.skills_root
        if isinstance(roots_raw, list):
            roots = [Path(r) for r in roots_raw if r]
        else:
            roots = [Path(roots_raw)] if roots_raw else []
        local_user_skills_root = _local_user_skills_root(env.session)
        if local_user_skills_root is not None:
            roots.append(local_user_skills_root)
        remote_roots = _remote_skill_roots(env.session)
        if not roots and not remote_roots:
            self.logger.warning(
                "skills.enabled=true but no skill roots are available, skipping skill init"
            )
            return

        # Core-layer registry is independent of the service-layer resolver
        # registry. Service registry serves ActiveSkill prompt rendering; this
        # registry serves SkillTool registration into ToolCatalog.
        skill_registry = SkillRegistry(
            roots,
            remote_session=env.session if remote_roots else None,
            remote_roots=remote_roots,
        )
        disabled_skill_names = set(skills_cfg.disabled_skill_names)
        for root in roots:
            disabled_skill_names.update(_disabled_skill_names_from_settings(root))
        if remote_roots and env.session is not None:
            for remote_root in remote_roots:
                disabled_skill_names.update(
                    _disabled_skill_names_from_remote_settings(env.session, remote_root)
                )
        if disabled_skill_names:
            skill_registry.remove_skills(disabled_skill_names)
        schema_cache = ToolSchemaCache(Path(skills_cfg.cache_dir))

        # MCP runtime config: ALWAYS self-load from config_dir.
        # Independent of skills_config -- MCP runtime config (calculation_preflight,
        # calculation_executors) is a separate concern from skill routing.
        from matmaster.config.loader import _load_raw

        resolved_config_dir = _resolve_skill_config_dir(skills_cfg.config_dir)
        mcp_runtime_path = resolved_config_dir / skills_cfg.mcp_runtime_file
        if mcp_runtime_path.exists():
            mcp_config = _load_raw(mcp_runtime_path)
        else:
            raise FileNotFoundError(
                f"MCP runtime config not found: {mcp_runtime_path}. "
                f"Required when skills.enabled=true."
            )
        runtime_patch = skills_cfg.mcp_runtime_patch or {}
        if isinstance(runtime_patch, dict) and runtime_patch:
            mcp_config = _deep_merge_dict(mcp_config, runtime_patch)

        mcp_config_file = mcp_config.get("config_file", skills_cfg.mcp_config_file)
        config_path = Path(mcp_config_file)
        if not config_path.is_absolute():
            config_path = resolved_config_dir / config_path

        if mcp_config.get("calculation_preflight") == "calculation":
            try:
                from matmaster.mcp.calculation.config_env import resolve_mcp_config_path

                config_path = resolve_mcp_config_path(config_path)
            except ImportError:
                self.logger.warning(
                    "calculation_preflight=calculation but "
                    "matmaster.mcp.calculation.config_env is unavailable; "
                    "using config_path as-is: %s",
                    config_path,
                )

        # Load server connection config from JSON
        server_config: dict = {}
        if config_path.exists():
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8"))
                server_config = raw.get("mcpServers", {})
            except Exception as e:
                self.logger.warning("Failed to load MCP server config: %s", e)

        connector = LazyMCPConnector(
            mcp_server_config=server_config,
            mcp_config=mcp_config,
            session=env.session,
            workspace_path=env.execution_workdir,
        )
        self._register_cleanup(connector.cleanup)

        # Extract sync_tools mapping from calculation_executors config.
        # Sync tools are synchronous operations that should complete quickly,
        # so they get a shorter timeout than the default MCP tool timeout.
        _SYNC_TOOL_TIMEOUT = 30.0
        executors = mcp_config.get("calculation_executors") or {}
        sync_tools_by_server: dict[str, set[str]] = {
            name: set(cfg.get("sync_tools") or [])
            for name, cfg in executors.items()
            if isinstance(cfg, dict) and cfg.get("sync_tools")
        }

        builtin_cfg = self._config.tools.builtin or []
        allow_builtin_all = "*" in builtin_cfg
        allowed_builtin = set(builtin_cfg) if not allow_builtin_all else None
        if (
            allow_builtin_all
            or (allowed_builtin is not None and "PaperSearch" in allowed_builtin)
        ) and "PaperSearch" not in registry:
            from matmaster.tools.builtin.paper_search_tool import PaperSearchTool

            paper_tool = PaperSearchTool(
                connector=connector,
                mcp_config=mcp_config,
            )
            if catalog is not None:
                catalog.register_overlay(paper_tool, source="builtin")
            else:
                registry.register(paper_tool, source="builtin")

        def activate_mcp_server(mcp_server: str) -> None:
            schemas = schema_cache.load(mcp_server)
            if not schemas:
                self.logger.warning(
                    "No cached schema for MCP server '%s', tools not injected",
                    mcp_server,
                )
                return
            include_only = mcp_config.get("tool_include_only") or {}
            allowed = include_only.get(mcp_server)
            if (
                allowed is not None
                and isinstance(allowed, (list, tuple))
                and len(allowed) == 0
            ):
                schemas = []
            elif isinstance(allowed, (list, tuple)) and allowed:
                allow_set = set(allowed)
                schemas = [tool for tool in schemas if tool.get("name") in allow_set]
            tool_timeouts = mcp_config.get("tool_timeouts", {})
            server_timeout = (
                float(tool_timeouts.get(mcp_server))
                if isinstance(tool_timeouts, dict)
                and tool_timeouts.get(mcp_server) is not None
                else None
            )
            sync_tools = sync_tools_by_server.get(mcp_server, set())
            for tool_schema in schemas:
                original_name = tool_schema["name"]
                prefixed_name = f"{mcp_server}_{original_name}"
                if prefixed_name in registry:
                    continue
                is_sync = original_name in sync_tools
                tool_timeout = _SYNC_TOOL_TIMEOUT if is_sync else server_timeout
                lazy_tool = LazyMCPTool(
                    server_name=mcp_server,
                    tool_name=prefixed_name,
                    remote_tool_name=original_name,
                    description=tool_schema.get("description", ""),
                    input_schema=tool_schema.get("input_schema", {}),
                    connector=connector,
                    timeout=tool_timeout,
                )
                if catalog is not None:
                    catalog.register_overlay(lazy_tool, source="mcp")
                else:
                    registry.register(lazy_tool, source="mcp")

        skill_tool = SkillTool(
            session=env.session,
            skill_registry=skill_registry,
            on_skill_hit=activate_mcp_server,
        )
        registry.register(skill_tool, source="skill")
        registry.register(
            LegacyUseSkillTool(
                session=env.session,
                skill_registry=skill_registry,
                on_skill_hit=activate_mcp_server,
            ),
            source="skill",
        )

        # Replay skills activated on past turns of this session.
        # activate_mcp_server reads only from the on-disk schema cache (no MCP IO),
        # is idempotent (skips tools already in registry), and warns +
        # skips on cache miss.
        replay_skills = ctx.request.active_skills
        if isinstance(replay_skills, (set, frozenset, list, tuple)):
            for skill_name in replay_skills:
                if not isinstance(skill_name, str) or not skill_name:
                    continue
                try:
                    skill = skill_registry.get_skill(skill_name)
                except Exception:
                    self.logger.warning(
                        "Replay of skill '%s' raised, skipping",
                        skill_name,
                        exc_info=True,
                    )
                    continue
                mcp_server = getattr(
                    getattr(skill, "meta_info", None), "mcp_server", None
                )
                if not isinstance(mcp_server, str) or not mcp_server:
                    continue
                try:
                    activate_mcp_server(mcp_server)
                except Exception:
                    self.logger.warning(
                        "Replay of MCP server for skill '%s' raised, skipping",
                        skill_name,
                        exc_info=True,
                    )

        self._skill_registry = skill_registry
