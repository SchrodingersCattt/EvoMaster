"""DevRunner -- per-run assembly mirroring AgentRunService pattern."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matmaster.config.exp import ExpConfig, ExpToolsConfig
from matmaster.core.exp import Exp
from matmaster.devshell.config import DevConfig
from matmaster.devshell.stream_hook import DevStreamHook
from matmaster.types.cancellation import CancellationToken
from matmaster.types.context import PlaygroundContext
from matmaster.types.messages import Message, UserMessage

if TYPE_CHECKING:
    from matmaster.core.stream_drain import DrainResult
    from matmaster.devshell.event_observer import DevEventObserver

logger = logging.getLogger(__name__)


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
        resolved_route: Any = None,
        stream_hook: DevStreamHook | None = None,
        exp_config: ExpConfig | None = None,
    ) -> None:
        self._config = config
        self._workdir = workdir
        self._llm_provider = llm_provider
        self._llm_config = llm_config
        self._resolved_route = resolved_route
        self._stream_hook = stream_hook or DevStreamHook()

        # Build PlaygroundContext
        session = self._create_session(config, workdir)
        cache_area = workdir / ".cache"
        cache_area.mkdir(parents=True, exist_ok=True)

        self._pg_ctx = PlaygroundContext(
            workdir=workdir,
            session_type=config.session.type,
            cache_area=cache_area,
            session=session,
            llm_provider=llm_provider,
            config_dir=None,
            llm_config=llm_config,
            run_meta={"source": "devshell"},
        )

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
        from matmaster.core.stream_drain import DrainResult, drain_run_stream

        exp = Exp(self._exp_config)

        async def _run_once() -> DrainResult:
            try:
                runtime = await exp.build_runtime(self._pg_ctx)
                spec = runtime.spec

                # Devshell bypasses Exp.run_stream(), so it must inject
                # cancellation into the session and tool catalog itself.
                if cancel_token is not None:
                    catalog = getattr(spec, "tool_catalog", None)
                    if catalog is not None:
                        catalog.inject_cancel_token(cancel_token)
                    session = self._pg_ctx.session
                    if session is not None:
                        session._cancel_token = cancel_token

                # Build on_event callback for real-time forwarding
                def _on_event(event: Any) -> None:
                    self._stream_hook.on_event(event)
                    if event_observer is not None:
                        event_observer.emit(event)

                return await drain_run_stream(
                    runtime.kernel.run_stream(
                        spec, task, history=self.history, cancel_token=cancel_token
                    ),
                    on_event=_on_event,
                )
            finally:
                await exp._run_cleanup_callbacks()

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
                        dict(item) for item in getattr(result, "usage_vendor_by_turn", ())
                    ],
                )
            )

        return result
