"""DevRunner -- per-run assembly mirroring AgentRunService pattern."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from matmaster.core.bus import MessageBus
from matmaster.config.exp import ExpConfig, ExpToolsConfig
from matmaster.core.exp import Exp
from matmaster.devshell.config import DevConfig
from matmaster.devshell.stream_hook import DevStreamHook
from matmaster.types.context import PlaygroundContext
from matmaster.types.messages import Message, SystemMessage, UserMessage
from matmaster.types.runtime import KernelRunResult

logger = logging.getLogger(__name__)


class DevRunner:
    """Per-run assembly: build_runtime -> inject hooks -> kernel.run -> history.

    Mirrors the split-call pattern of AgentRunService. REPL-agnostic:
    accepts task string, returns KernelRunResult.
    """

    def __init__(
        self,
        *,
        config: DevConfig,
        workdir: Path,
        llm_provider: Any,
        stream_hook: DevStreamHook | None = None,
    ) -> None:
        self._config = config
        self._workdir = workdir
        self._llm_provider = llm_provider
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
            llm_config=None,
            run_meta={"source": "devshell"},
        )

        # Exp config dict
        self._exp_config = self._build_exp_config(config)

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
            developer_instructions=config.agent.identity or "",
            system_prompt=system_prompt,
        )

    def run(
        self,
        task: str,
        *,
        stop_event: threading.Event | None = None,
        bus: MessageBus | None = None,
    ) -> KernelRunResult:
        """Execute a single agent run.

        Returns KernelRunResult with event and message transcript.
        Appends run messages to history for multi-turn accumulation.
        """
        exp = Exp(self._exp_config)
        runtime = exp.build_runtime(self._pg_ctx, bus=bus)

        # Inject DevStreamHook (same pattern as AgentRunService)
        spec = runtime.spec.model_copy(
            update={"hooks": [*runtime.spec.hooks, self._stream_hook]}
        )

        try:
            result = runtime.kernel.run(
                spec, task, history=self.history, stop_event=stop_event
            )
            # Accumulate history for non-cancelled runs.
            # Message layout: [System, *history, User(task), ...new_messages]
            # We skip System + existing history + User to extract only new messages.
            if result.result.status != "cancelled":
                skip_count = 1 + len(self.history) + 1  # System + history + User
                new_messages = result.messages[skip_count:]
                self.history.append(UserMessage(content=task))
                self.history.extend(new_messages)
            return result
        finally:
            runtime.cleanup()
