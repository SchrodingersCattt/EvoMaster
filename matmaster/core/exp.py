"""Exp config-driven runtime assembly and execution driver.

This module turns ExpConfig + AgentRunContext into AgentRuntime and exposes the
shared runtime_scope/run_stream lifecycle used by service and devshell paths.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from matmaster.config.exp import ExpConfig
from matmaster.context.assembly import (
    ContextAssembler,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.environment import build_environment_section
from matmaster.context.ports import SkillResolver, UserInstructions
from matmaster.context.sections import ContextView
from matmaster.context.sources.turn_input import TurnInput
from matmaster.context.system_prompt import SystemPromptBuilder
from matmaster.context.turn_intent import TurnIntentResolution, resolve_turn_intent
from matmaster.context.user_turn_context import (
    DEFAULT_TURN_TRANSFORM,
    USER_CONTEXT_RENDER_VERSION,
    USER_TURN_CONTEXT_SCHEMA_VERSION,
)
from matmaster.core.child_llm import _resolve_child_run_ctx
from matmaster.core.hooks import HookExecutor
from matmaster.core.path_access import derive_path_access_roots
from matmaster.core.run_context import AgentRunContext
from matmaster.core.run_identity import build_run_identity
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.cancellation import CancellationToken
from matmaster.types.runtime import (
    AgentKernelResources,
    AgentKernelRuntime,
    AgentKernelSpec,
    AgentKernelTurnRequest,
    AgentRuntime,
)
from matmaster.types.runtime_ports import (
    EmptySessionEventHistory,
    KernelRuntimePorts,
    UserTurnContextWriteRequest,
)

if TYPE_CHECKING:
    from matmaster.skills.registry import SkillRegistryCache
    from matmaster.types.messages import Message


# Builtin tools whose execution reaches outside the workspace.
# Presence of any of these names in the configured builtin list activates
# the EXTERNAL_SERVICE plane in RuntimeTopology.
_EXTERNAL_EFFECT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "WebSearch",
        "WebFetch",
        "PaperSearch",
        "Bohrium",
        "AttachFigure",
    }
)

# Builtin tool names that require an active session for execution.
_SESSION_REQUIRING_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "Bash",
        "AttachFigure",
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
    }
)


@dataclass(frozen=True)
class RootTurnRender:
    rendered_content: str


class Exp:
    """Config-driven assembly layer for AgentRuntime and kernel execution."""

    def __init__(
        self,
        config: ExpConfig,
        *,
        allow_spawn: bool = True,
        exclude_subagents: frozenset[str] | None = None,
        inherited_skill_cache: SkillRegistryCache | None = None,
    ) -> None:
        self._config = config
        self._allow_spawn = allow_spawn
        self._exclude_subagents: frozenset[str] = exclude_subagents or frozenset()
        self._inherited_skill_cache = inherited_skill_cache
        self._cleanup_callbacks: list[Callable[[], Any]] = []
        # Core-layer registry serves SkillTool registration and the
        # registry-wide system prompt prefix. Service-layer resolver state is
        # held separately and feeds active-skill prompt rendering.
        self._skill_registry: Any = None
        self._skill_resolver: SkillResolver | None = None
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def exp_name(self) -> str:
        """From config.name, defaults to 'direct'."""
        return self._config.name

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

    def _make_child_run_factory(
        self,
        ctx: AgentRunContext,
        skill_cache: SkillRegistryCache,
    ) -> Callable[..., AsyncIterator[Any]]:
        """Seam :class:`SubagentOrchestrator` uses to run one child agent.

        Exp owns *assembling* the child runtime -- load its config, construct a
        child Exp with spawn disabled (one-level recursion cap), and drive its
        ``run_stream`` with the parent ``ctx``. The orchestrator owns the spawn
        lifecycle (id, hooks, event retag, drain) around the returned stream.
        """

        def child_run_factory(
            exp_name: str,
            task: str,
            *,
            cancel_token: CancellationToken | None = None,
            spawn_id: str | None = None,
        ) -> AsyncIterator[Any]:
            from matmaster.config.loader import load_exp_config

            child_cfg = load_exp_config(exp_name)
            child_exp = Exp(
                child_cfg,
                allow_spawn=False,
                inherited_skill_cache=skill_cache,
            )
            child_ctx = _resolve_child_run_ctx(ctx, child_cfg)
            return child_exp.run_stream(
                child_ctx,
                task,
                cancel_token=cancel_token,
                spawn_id=spawn_id,
            )

        return child_run_factory

    @staticmethod
    def _derive_active_planes(
        *,
        has_session: bool,
        builtin_cfg: list[str],
        skills_enabled: bool,
        excluded_builtin: frozenset[str] = frozenset(),
    ) -> frozenset:
        """Derive active tool planes from runtime capabilities.

        Always activates CONTROL_PLANE. Activates SESSION_SHELL and
        SESSION_FS when a session is present. Activates EXTERNAL_SERVICE
        when skills are enabled or an external-effect builtin survives the
        exclusion list — the same filter _init_builtin_tools applies, so the
        declared planes match the registered tool catalog.
        """
        from matmaster.types.topology import ToolPlane

        planes: set[ToolPlane] = {ToolPlane.CONTROL_PLANE}
        if has_session:
            planes |= {ToolPlane.SESSION_SHELL, ToolPlane.SESSION_FS}
        cfg_set = set(builtin_cfg)
        allow_all = "*" in cfg_set
        has_external_effect_tool = any(
            (allow_all or name in cfg_set) and name not in excluded_builtin
            for name in _EXTERNAL_EFFECT_TOOL_NAMES
        )
        if skills_enabled or has_external_effect_tool:
            planes.add(ToolPlane.EXTERNAL_SERVICE)
        return frozenset(planes)

    async def build_runtime(
        self,
        ctx: AgentRunContext,
        *,
        skills: dict[str, Any] | None = None,
        spawn_id: str | None = None,
    ) -> AgentRuntime:
        """One-shot assembly: tools -> prompt -> context assembly -> kernel.

        Constructs FullToolRunner + ToolCatalog + RuntimeTopology as the
        default execution path, then bundles kernel_spec + kernel_resources
        into an AgentKernelRuntime (see the §5.4 six-step ordering).
        """
        env = ctx.environment
        request = ctx.request
        from matmaster.skills.registry import SkillRegistryCache

        skill_cache = self._inherited_skill_cache or SkillRegistryCache()

        # Discard any registry from a prior run so a turn that turns skills off
        # cannot expose stale state to the prompt builder.
        self._skill_registry = None
        from matmaster.context.skill_resolver import SkillRegistryResolver
        from matmaster.core.runtime_context_assembly import empty_skill_resolver

        self._skill_resolver = empty_skill_resolver

        registry = ToolRegistry()
        builtin_cfg = self._config.tools.builtin
        excluded_builtin = set(self._config.tools.excluded_builtin)
        path_access_roots = derive_path_access_roots(env)
        if builtin_cfg:
            self._init_builtin_tools(
                ctx,
                registry,
                builtin_cfg,
                excluded_builtin=excluded_builtin,
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
            excluded_builtin=frozenset(excluded_builtin),
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
            self._init_skill_tools(
                ctx,
                registry,
                skills_config=skills,
                catalog=catalog,
                skill_cache=skill_cache,
            )
        self._skill_resolver = SkillRegistryResolver(self._skill_registry)
        compaction = self._config.compaction
        if request.context_limit is not None:
            compaction = compaction.model_copy(
                update={"context_limit": request.context_limit}
            )

        # When allow_spawn is False (child Exp), spawn_fn is None, which causes
        # AgentTool to set exposed_to_model=False (hidden from LLM but still
        # in catalog).
        if self._config.tools.allows_builtin("Agent"):
            from matmaster.config.loader import list_model_visible_exps
            from matmaster.tools.builtin import AgentTool

            spawn_fn = None
            available_exps = None
            if self._allow_spawn:
                from matmaster.core.subagent_orchestrator import SubagentOrchestrator

                orchestrator = SubagentOrchestrator(
                    child_run_factory=self._make_child_run_factory(ctx, skill_cache),
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
            environment_context=build_environment_section(
                execution_workdir=env.execution_workdir,
                now=datetime.now(ZoneInfo("Asia/Shanghai")),
            ),
        )

        from matmaster.core.runtime_context_assembly import (
            build_runtime_context_assembly,
        )
        from matmaster.types.tool_runner_state import ToolRunnerState

        runtime_context = build_runtime_context_assembly(
            llm_provider=request.llm_provider,
            compaction=compaction,
            ctx=ctx,
            skill_resolver=self._skill_resolver,
            spawn_id=spawn_id,
            logger=self.logger,
        )

        structural_validation = StructuralValidation()
        capability_policy = DefaultCapabilityPolicy()
        scheduler = ToolScheduler()
        runner_state = ToolRunnerState()
        run_identity = build_run_identity(ctx, spawn_id=spawn_id)
        figure_upload_config = request.ports.figure_upload.config
        if figure_upload_config is not None:
            runner_state.set("figure_upload_config", figure_upload_config)
        tool_timeout_observer = request.ports.tool_timeout_observer
        if tool_timeout_observer is not None:
            from matmaster.core.tool_timeout_observer import (
                install_tool_timeout_observer_hooks,
            )

            install_tool_timeout_observer_hooks(
                hook_executor=hook_executor,
                observer=tool_timeout_observer,
                run_identity=run_identity,
                logger=self.logger,
            )
        submit_approval_gate = request.ports.submit_approval_gate
        if submit_approval_gate is not None and spawn_id is None:
            from matmaster.core.submit_review_support import install_submit_review_hooks

            install_submit_review_hooks(
                runner_state=runner_state,
                hook_executor=hook_executor,
                run_identity=run_identity,
                submit_approval_gate=submit_approval_gate,
            )
        self._register_cleanup(runner_state.clear)

        full_runner = FullToolRunner(
            catalog=catalog,
            structural_validation=structural_validation,
            capability_policy=capability_policy,
            scheduler=scheduler,
            topology=topology,
            hook_executor=hook_executor,
            state=runner_state,
            bohrium_node_acquirer=request.ports.bohrium_node_acquirer,
        )

        checkpoint_sink_factory = request.ports.compaction.checkpoint_sink_factory
        checkpoint_sink = None
        if callable(checkpoint_sink_factory):
            checkpoint_sink = checkpoint_sink_factory(spawn_id=spawn_id)
        pre_compaction_barrier = request.ports.compaction.pre_compaction_barrier

        kernel_spec = AgentKernelSpec(
            system_prompt=system_prompt,
            max_turns=self._config.max_turns,
            compaction=compaction,
            run_identity=run_identity,
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

    @asynccontextmanager
    async def runtime_scope(
        self,
        ctx: AgentRunContext,
        cancel_token: CancellationToken | None = None,
        *,
        skills: dict[str, Any] | None = None,
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
                spawn_id=spawn_id,
            )
            kernel_runtime = runtime.kernel_runtime
            if ctx.environment.session is not None:
                ctx.environment.session._cancel_token = cancel_token

            # Inject cancel_token into tools for cancel propagation.
            catalog = kernel_runtime.resources.tool_catalog
            if cancel_token is not None and catalog is not None:
                catalog.inject_cancel_token(cancel_token)

            provider_scope = getattr(ctx.request.llm_provider, "billing_scope", None)
            if callable(provider_scope):
                with provider_scope(spawn_id=spawn_id):
                    yield runtime
            else:
                yield runtime
        finally:
            await self._run_cleanup_callbacks()

    async def run_stream(
        self,
        ctx: AgentRunContext,
        task: str | None = None,
        *,
        history: list[Message] | None = None,
        cancel_token: CancellationToken | None = None,
        skills: dict[str, Any] | None = None,
        spawn_id: str | None = None,
    ) -> AsyncIterator[Any]:
        """Thin driver over :meth:`runtime_scope`.

        Async generator that yields BusEvent from the kernel generator.
        Cleanup is guaranteed by the scope's try/finally on normal completion,
        break, and exception.
        """
        resolution: TurnIntentResolution | None = None
        user_instructions: UserInstructions | None = None
        if spawn_id is None:
            if ctx.request.turn_input is None:
                raise RuntimeError(
                    "AgentRunRequest.turn_input is required for root run"
                )
            events_port = (
                ctx.request.ports.compaction.history or EmptySessionEventHistory()
            )
            user_instructions = (
                ctx.request.user_instructions or UserInstructions.empty()
            )
            resolution = await resolve_turn_intent(
                events_port=events_port,
                instructions_hash=user_instructions.hash,
                session_id=ctx.environment.session_id,
                spawn_id=None,
            )
            ctx = ctx.model_copy(
                update={
                    "request": ctx.request.model_copy(
                        update={"active_skills": resolution.active_skills}
                    )
                }
            )
        elif task is None:
            raise RuntimeError("task is required for spawn run")
        else:
            ctx = ctx.model_copy(
                update={
                    "request": ctx.request.model_copy(
                        update={
                            "turn_input": TurnInput.from_values(user_text=task),
                        }
                    )
                }
            )

        async with self.runtime_scope(
            ctx,
            cancel_token,
            skills=skills,
            spawn_id=spawn_id,
        ) as runtime:
            if spawn_id is None:
                if resolution is None or user_instructions is None:
                    raise RuntimeError("root turn resolution is missing")
                if runtime.context_runtime is None:
                    raise RuntimeError("context runtime is unavailable for root run")
                turn = await self._render_and_persist_root_turn(
                    ctx=ctx,
                    intent=resolution.intent,
                    assembler=runtime.context_runtime.assembler,
                    user_instructions=user_instructions,
                )
                task = turn.rendered_content
                turn_request = AgentKernelTurnRequest(
                    user_message_content=task,
                    turn_input=ctx.request.turn_input,
                )
                kernel_runtime = replace(
                    runtime.kernel_runtime,
                    spec=replace(
                        runtime.kernel_runtime.spec,
                        prompt_submit_rewrite_enabled=False,
                    ),
                )
            else:
                kernel_runtime = runtime.kernel_runtime
                turn_request = AgentKernelTurnRequest(
                    user_message_content=task,
                    turn_input=ctx.request.turn_input,
                )
            async for event in runtime.kernel.run_stream(
                kernel_runtime,
                turn_request,
                history=history,
                cancel_token=cancel_token,
            ):
                yield event

    async def _render_and_persist_root_turn(
        self,
        *,
        ctx: AgentRunContext,
        intent: ContextAssemblyIntent,
        assembler: ContextAssembler,
        user_instructions: UserInstructions,
    ) -> RootTurnRender:
        if ctx.request.turn_input is None:
            raise RuntimeError("AgentRunRequest.turn_input is required for root run")
        assembly = await assembler.assemble_turn(
            intent=intent,
            request=TurnAssemblyRequest(
                session_id=ctx.environment.session_id,
                spawn_id=None,
                turn_input=ctx.request.turn_input,
                user_instructions=user_instructions,
            ),
        )
        message = assembly.user_turn_context.to_message(ContextView.RUNTIME)
        await self._write_user_turn_context_if_configured(
            ctx=ctx,
            intent=intent,
            message=message,
            user_instructions=user_instructions,
        )
        return RootTurnRender(rendered_content=message.content)

    async def _write_user_turn_context_if_configured(
        self,
        *,
        ctx: AgentRunContext,
        intent: ContextAssemblyIntent,
        message: Message,
        user_instructions: UserInstructions,
    ) -> None:
        writer = ctx.request.ports.user_turn_context_writer
        if writer is None:
            return
        await writer(
            UserTurnContextWriteRequest(
                session_id=ctx.environment.session_id,
                task_id=ctx.environment.metadata.task_id,
                invocation_id=ctx.request.invocation_id,
                spawn_id=None,
                kind="anchor" if intent.is_anchor_turn else "continuation",
                message=message,
                user_instructions_hash=(
                    user_instructions.hash if intent.is_anchor_turn else None
                ),
                transform=DEFAULT_TURN_TRANSFORM,
                render_version=USER_CONTEXT_RENDER_VERSION,
                schema_version=USER_TURN_CONTEXT_SCHEMA_VERSION,
            )
        )

    def _init_builtin_tools(
        self,
        ctx: AgentRunContext,
        registry: ToolRegistry,
        builtin_cfg: list[str],
        *,
        excluded_builtin: set[str] | frozenset[str] = frozenset(),
        spawn_id: str | None = None,
        path_access_roots: tuple[Any, ...] = (),
    ) -> None:
        """Register builtin tools filtered by *builtin_cfg*.

        When ``builtin_cfg`` contains ``"*"`` every builtin is registered
        (original behaviour).  Otherwise only tools whose ``name`` appears
        in the list are registered, cutting prompt-token overhead.

        Tools are split into two categories:
        - Session-requiring: BashTool, AttachFigure, ReadTool, WriteTool,
          EditTool, GlobTool, GrepTool (need ctx.environment.session for
          execution)
        - Sessionless: TodoWriteTool, WebSearchTool, WebFetchTool
          (operate without a session; AgentTool is registered separately
          in build_runtime)

        When ctx.environment.session is None, only sessionless tools are
        registered.
        """
        allow_all = "*" in builtin_cfg
        allowed: set[str] | None = None if allow_all else set(builtin_cfg)

        def _want(name: str) -> bool:
            return (allowed is None or name in allowed) and name not in excluded_builtin

        from matmaster.tools.builtin import (
            AskQuestionTool,
            AttachFigure,
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
        bohrium_allow_local_paths = env.metadata.source == "devshell"
        bohrium_workdir = (
            exec_wd if env.session_type in {"ssh", "bohrium-deferred"} else env.workdir
        )
        search_path_roots = tuple(
            root.root
            for root in path_access_roots
            if "search" in getattr(root, "permissions", frozenset())
        )

        session_tools: list[Any] = []
        if has_session:
            session_tools = [
                BashTool(session=env.session, workdir=exec_wd),
                AttachFigure(session=env.session, workdir=exec_wd),
                ReadTool(
                    session=env.session,
                    workdir=exec_wd,
                    vision_enabled=ctx.request.supports_vision,
                    vision_detail=ctx.request.vision_detail,
                ),
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
            BohriumTool(
                session=env.session,
                workdir=bohrium_workdir,
                job_ledger=ctx.request.ports.bohrium_job_ledger,
                node_acquirer=ctx.request.ports.bohrium_node_acquirer,
                session_id=ctx.environment.session_id,
                invocation_id=ctx.request.invocation_id,
                allow_local_paths=bohrium_allow_local_paths,
                default_max_runtime_seconds=(
                    ctx.request.bohrium_job_max_runtime_seconds
                ),
                default_max_wait_time_seconds=(
                    ctx.request.bohrium_job_max_wait_time_seconds
                ),
            ),
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
        *,
        skill_cache: SkillRegistryCache,
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

        from matmaster.core.skill_registry_cache import build_cached_skill_registry
        from matmaster.tools.builtin.skill_tool import SkillTool
        from matmaster.tools.lazy_mcp import LazyMCPConnector, LazyMCPTool
        from matmaster.tools.schema_cache import ToolSchemaCache

        skill_registry = build_cached_skill_registry(
            skills_cfg=skills_cfg,
            session=env.session,
            skill_cache=skill_cache,
        )
        if skill_registry is None:
            self.logger.warning(
                "skills.enabled=true but no skill roots are available, skipping skill init"
            )
            return

        # Core-layer registry is independent of the service-layer resolver
        # registry. Service registry serves ActiveSkill prompt rendering; this
        # registry serves SkillTool registration into ToolCatalog.
        schema_cache = ToolSchemaCache(Path(skills_cfg.cache_dir))

        # MCP runtime config: ALWAYS self-load from config_dir.
        # Independent of skills_config -- MCP runtime config (calculation_preflight,
        # calculation_executors) is a separate concern from skill routing.
        from matmaster.config.loader import load_skill_mcp_runtime

        mcp_config, server_config = load_skill_mcp_runtime(skills_cfg)

        connector = LazyMCPConnector(
            mcp_server_config=server_config,
            mcp_config=mcp_config,
            session=env.session,
            workspace_path=env.execution_workdir,
        )
        self._register_cleanup(connector.cleanup)

        # Sync tools are synchronous operations that should complete quickly,
        # so they get a shorter timeout than the default MCP tool timeout.
        _SYNC_TOOL_TIMEOUT = 30.0
        executors = mcp_config.get("calculation_executors") or {}
        sync_tools_by_server: dict[str, set[str]] = {
            name: set(cfg.get("sync_tools") or [])
            for name, cfg in executors.items()
            if isinstance(cfg, dict) and cfg.get("sync_tools")
        }

        if (
            self._config.tools.allows_builtin("PaperSearch")
            and "PaperSearch" not in registry
        ):
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
                    mcp_config=mcp_config,
                    timeout=tool_timeout,
                )
                if catalog is not None:
                    catalog.register_overlay(lazy_tool, source="mcp")
                else:
                    registry.register(lazy_tool, source="mcp")

        def refresh_skill_registry():
            # Memoized by roots-derived cache key: cold calls hit the cached
            # entry; once a deferred Bohrium session copies its remote skill
            # roots after Node acquisition, the key changes and remote skills
            # override worker-local fallbacks for later activations.
            return build_cached_skill_registry(
                skills_cfg=skills_cfg,
                session=env.session,
                skill_cache=skill_cache,
            )

        skill_tool = SkillTool(
            session=env.session,
            skill_registry=skill_registry,
            on_skill_hit=activate_mcp_server,
            registry_provider=refresh_skill_registry,
        )
        registry.register(skill_tool, source="skill")

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
