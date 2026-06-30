"""DevRunner -- per-run assembly mirroring AgentRunService pattern."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matmaster.bohrium.runtime import try_attach_local_bohrium_runtime_from_env
from matmaster.config.exp import ExpConfig, ExpToolsConfig
from matmaster.context.sources.turn_input import TurnInput
from matmaster.core.exp import Exp
from matmaster.core.playground import ExecutionEnvironment
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.devshell.config import DevConfig
from matmaster.devshell.stream_hook import DevStreamHook
from matmaster.types.cancellation import CancellationToken
from matmaster.types.events import BusEvent
from matmaster.types.messages import Message, UserMessage
from matmaster.types.run_metadata import RunMetadata
from matmaster.types.runtime import AgentKernelTurnRequest

if TYPE_CHECKING:
    from matmaster.devshell.event_observer import DevEventObserver
    from matmaster.types.stream_drain import DrainResult

logger = logging.getLogger(__name__)


def make_dev_subagent_provider_factory(llm_config):
    """devshell 用的 subagent provider factory：解析 profile，不包计费。"""
    from matmaster.providers.llm_factory import build_provider_bundle

    def factory(*, profile_key: str):
        return build_provider_bundle(llm_config, model_override=profile_key)

    return factory


def _patch_bohrium_submit(runtime: Any, error_message: str) -> None:
    """Monkey-patch BohriumTool._submit to always return an error (eval-only)."""
    from matmaster.tools.builtin.bohrium_tool.tool import BohriumTool
    from matmaster.tools.tool_result import ToolResult

    catalog = runtime.kernel_runtime.resources.tool_catalog
    if catalog is None:
        return
    registry = getattr(catalog, "_registry", None)
    if registry is None:
        return
    tool = registry.get_raw("Bohrium")
    if tool is None or not isinstance(tool, BohriumTool):
        return
    tool._submit = lambda args: ToolResult(
        status="error",
        content=f"job/create failed: {error_message}",
    )


class DevRunner:
    """Per-run assembly: build_runtime -> kernel.run_stream -> drain -> history.

    Mirrors the split-call pattern of AgentRunService. REPL-agnostic:
    accepts task string, returns DrainResult.
    """

    def __init__(
        self,
        *,
        config: DevConfig,
        workdir: Path,
        llm_provider: Any,
        llm_config: Any = None,
        llm_bundle: Any = None,
        stream_hook: DevStreamHook | None = None,
        exp_config: ExpConfig | None = None,
        exclude_subagents: list[str] | None = None,
        inject_bohrium_failure: str | None = None,
    ) -> None:
        self._config = config
        self._workdir = workdir
        self._llm_provider = llm_provider
        self._llm_config = llm_config
        self._stream_hook = stream_hook or DevStreamHook()
        self._exclude_subagents: frozenset[str] = frozenset(exclude_subagents or ())
        self._inject_bohrium_failure = inject_bohrium_failure

        # Build the physical environment + the per-run request.
        session = self._create_session(config, workdir)
        cache_area = workdir / ".cache"
        cache_area.mkdir(parents=True, exist_ok=True)

        self._environment = ExecutionEnvironment(
            workdir=workdir,
            session_type=config.session.type,
            cache_area=cache_area,
            session=session,
            metadata=RunMetadata(source="devshell"),
        )
        # 模型身份单源于 llm_bundle；不提供 bundle 的（测试）场景一律为 None
        self._request = AgentRunRequest(
            llm_provider=llm_provider,
            llm_config=llm_config,
            llm_model=getattr(llm_bundle, "model", None),
            llm_model_profile=getattr(llm_bundle, "model_profile", None),
            llm_model_route=getattr(llm_bundle, "model_route", None),
            context_limit=getattr(llm_bundle, "context_limit", None),
        )
        try_attach_local_bohrium_runtime_from_env(session)

        # Exp config: use explicit exp override when provided, else derive from DevConfig.
        self._exp_config = (
            exp_config if exp_config is not None else self._build_exp_config(config)
        )
        # Local devshell is not Bohrium SSH; avoid model defaulting to /share from tool hints.
        if config.session.type == "local":
            wd = str(workdir.resolve())
            hint = (
                "\n\n## Local session\n"
                f"- Workspace directory: `{wd}`\n"
                "- `Bash` uses this directory as cwd; file tools resolve relative paths under it.\n"
                "- **Do not** assume `/share/...` exists here; that path is for **Bohrium remote SSH** "
                "project storage, not for typical local runs.\n"
            )
            self._exp_config = self._exp_config.model_copy(
                update={"system_prompt": self._exp_config.system_prompt + hint}
            )

        # Multi-turn history
        self.history: list[Message] = []

    @staticmethod
    def _create_session(config: DevConfig, workdir: Path) -> Any:
        """Create and open a local session."""
        from matmaster.sessions.local import LocalSession

        session = LocalSession(workspace_path=workdir)
        session.open()
        return session

    def build_run_context(
        self,
        *,
        child_event_sink: Any = None,
    ) -> AgentRunContext:
        """Compose the AgentRunContext from the stable environment + request.

        The environment is fixed for the runner; only the request's ports carry
        the per-run child-event sink. Shared by ``run()`` and the REPL's tool
        inspection so neither hand-rolls the composition.
        """
        from matmaster.types.runtime_ports import AgentRunPorts

        subagent_factory = (
            make_dev_subagent_provider_factory(self._llm_config)
            if self._llm_config is not None
            else None
        )
        request = self._request.model_copy(
            update={
                "ports": AgentRunPorts(
                    child_event_forward_sink=child_event_sink,
                    subagent_provider_factory=subagent_factory,
                )
            }
        )
        return AgentRunContext(environment=self._environment, request=request)

    @staticmethod
    def _build_exp_config(config: DevConfig) -> ExpConfig:
        """Convert DevConfig to ExpConfig."""
        from matmaster.config.loader import load_base_system_prompt

        system_prompt = config.agent.system_prompt
        if not system_prompt:
            system_prompt = load_base_system_prompt()

        return ExpConfig(
            name=config.agent.name,
            max_turns=config.agent.max_turns,
            tools=ExpToolsConfig(builtin=config.tools.builtin),
            skills=config.skills,
            compaction=config.compaction,
            developer_instructions=config.agent.identity or "",
            system_prompt=system_prompt,
        )

    def run(
        self,
        task: str,
        *,
        cancel_token: CancellationToken | None = None,
        event_observer: DevEventObserver | None = None,
    ) -> DrainResult:
        """Execute a single agent run.

        Returns DrainResult with terminal data and message transcript.
        Appends run messages to history for multi-turn accumulation.

        Real-time event forwarding: intermediate events are forwarded
        to DevStreamHook and DevEventObserver via the on_event callback
        during drain, replacing the old hook-based streaming path.
        """
        from matmaster.core.stream_drain import drain_run_stream
        from matmaster.types.stream_drain import DrainResult

        exp = Exp(self._exp_config, exclude_subagents=self._exclude_subagents)

        async def _run_once() -> DrainResult:
            # Build on_event callback for real-time forwarding
            def _on_event(event: BusEvent) -> None:
                self._stream_hook.on_event(event)
                if event_observer is not None:
                    event_observer.emit(event)

            # Inject forward sink before build_runtime so child agent spawn
            # closures capture the runtime port.
            ctx = self.build_run_context(child_event_sink=_on_event)

            # Share Exp's runtime lifecycle (build + cancel injection + cleanup)
            # instead of hand-copying it; cleanup is guaranteed by the scope.
            async with exp.runtime_scope(ctx, cancel_token) as runtime:
                if self._inject_bohrium_failure:
                    _patch_bohrium_submit(runtime, self._inject_bohrium_failure)
                return await drain_run_stream(
                    runtime.kernel.run_stream(
                        runtime.kernel_runtime,
                        AgentKernelTurnRequest(
                            user_message_content=task,
                            turn_input=TurnInput.from_values(user_text=task),
                        ),
                        history=self.history,
                        cancel_token=cancel_token,
                    ),
                    on_event=_on_event,
                )

        result = asyncio.run(_run_once())

        # Accumulate history for non-cancelled runs.
        # Message layout: [System, *history, User(task), ...new_messages]
        # We skip System + existing history + User to extract only new messages.
        if result.status != "cancelled":
            skip_count = 1 + len(self.history) + 1  # System + history + User
            new_messages = result.messages[skip_count:]
            self.history.append(UserMessage(content=task))
            self.history.extend(new_messages)

        # Emit RunResultEvent for observer
        if event_observer is not None:
            from matmaster.types.events import RunResultEvent

            event_observer.emit(
                RunResultEvent(
                    source="agent",
                    status=result.status,
                    reason=result.reason,
                    final_content=result.final_content,
                    num_turns=result.num_turns,
                    usage=result.usage,
                    usage_vendor_by_turn=[
                        dict(item)
                        for item in getattr(result, "usage_vendor_by_turn", ())
                    ],
                    finish_detail=getattr(result, "finish_detail", None),
                )
            )

        return result
