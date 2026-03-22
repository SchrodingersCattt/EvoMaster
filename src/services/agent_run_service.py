"""Agent execution service: new matmaster pipeline orchestration.

Rewritten per D-12: run_agent_sync() is a thin orchestration layer using:
  Playground.prepare() -> Bohrium -> Exp.assemble() -> ChatHistory ->
  EventRouter -> Kernel.run() -> post-processing

Method signature (12 parameters) unchanged -- zero caller modifications.
"""

import asyncio
import gc
import logging
import os
import threading
import time
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from matmaster.bus.queue import MessageBus
from matmaster.engine.agent import AgentKernel
from matmaster.hooks import (
    AssistantStateHook,
    ConfirmationHook,
    OutputProcessorHook,
    SkillHitHook,
)
from matmaster.integration import (
    EventRouter,
    PersistenceHandler,
    SSEHandler,
    WorkspaceHandler,
)
from matmaster.integration.bohrium_setup import BohriumSetupService
from matmaster.playground.playground import Playground
from matmaster.types.events import CancelledEvent, ErrorEvent
from src.dao.chat_events_table import get_chat_events_table
from src.dao.redis_dao import get_redis_dao
from src.services.chat_history import ChatHistoryConverter
from src.services.quota_service import use_quota
from src.services.sessions_service import get_sessions_service

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Thread pool concurrency: default 2 workers, override with CHAT_AGENT_MAX_WORKERS
_AGENT_MAX_WORKERS = int(os.environ.get('CHAT_AGENT_MAX_WORKERS', '2'))
if _AGENT_MAX_WORKERS < 1:
    _AGENT_MAX_WORKERS = 1

# Multi-turn dialog: max events from DB to avoid context overflow
_DIALOG_HISTORY_MAX_EVENTS = int(
    os.environ.get('CHAT_DIALOG_HISTORY_MAX_EVENTS', '500')
)

_project_root = Path(__file__).resolve().parent.parent.parent
RUN_ID_WEB = 'mat_master_web'


@runtime_checkable
class ReplyQueueLike(Protocol):
    """Confirmation reply queue abstraction: put content/cancel, blocking get."""

    def put_content(self, content: str) -> None: ...

    def put_cancel(self) -> None: ...

    def get(self, timeout: float | None = None) -> str | None:
        """Blocking get. Returns None for cancel; raises queue.Empty on timeout."""
        ...


class AgentRunService:
    """Agent execution service: pipeline orchestration via matmaster components."""

    def __init__(self, sessions_service=None):
        self._sessions_service = sessions_service or get_sessions_service()
        self._executor = ThreadPoolExecutor(max_workers=_AGENT_MAX_WORKERS)
        self._playgrounds: dict[str, Any] = {}
        self._playground_init_done = threading.Event()

    def init_playground_sync(self) -> None:
        """Validate config YAML existence at startup -- no dynamic module import (D-04)."""
        import yaml

        for pg_type in ("mat_master", "minimal"):
            config_path = _project_root / "configs" / pg_type / "config.yaml"
            if not config_path.exists():
                logger.warning("Config not found: %s", config_path)
                continue
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            if not isinstance(cfg, dict) or "agents" not in cfg:
                logger.warning("Config missing 'agents' key: %s", config_path)

        # Deprecation warnings for old modules (per D-02)
        try:
            import evomaster  # noqa: F401

            warnings.warn(
                "evomaster package is deprecated. Use matmaster instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        except ImportError:
            pass

        self._playground_init_done.set()
        logger.info("MatMaster chat: playground config validation complete.")

    def _get_or_create_playground(
        self,
        session_id: str,
        playground_type: str = "mat_master",
    ) -> Playground:
        """Return or create Playground by session_id. Per D-03: x_master raises ValueError."""
        if playground_type == "x_master":
            raise ValueError(
                "x_master playground_type is not supported in the new pipeline"
            )
        if session_id in self._playgrounds:
            return self._playgrounds[session_id]
        config_path = _project_root / "configs" / playground_type / "config.yaml"
        pg = Playground(config_path=config_path)
        self._playgrounds[session_id] = pg
        return pg

    def get_executor(self) -> ThreadPoolExecutor:
        """Return the thread pool for agent execution."""
        return self._executor

    def _build_llm_provider(self, pg_ctx, llm_override, model_override):
        """Build LLMProvider from playground config. Placeholder for provider factory."""
        # TODO: Wire actual LLM config extraction from pg_ctx
        raise NotImplementedError("_build_llm_provider to be wired with config")

    def _get_builtin_tools(self, pg_ctx):
        """Get builtin tools for the playground type. Placeholder."""
        return []

    def run_agent_sync(
        self,
        session_id: str,
        user_prompt: str,
        send_cb: Callable[[dict], Any],
        loop: Optional[asyncio.AbstractEventLoop],
        stop_event: Any,
        mode: str,
        reply_queue: ReplyQueueLike | None,
        task_id: str,
        invocation_id: str | None = None,
        llm_override: str | None = None,
        model_override: str | None = None,
    ) -> None:
        """Execute agent in background thread using new matmaster pipeline.

        Pipeline: Playground.prepare() -> Bohrium -> Exp.assemble() ->
        ChatHistory -> EventRouter -> Kernel.run() -> post-processing.

        Method signature unchanged per D-12: all 12 parameters preserved.
        """
        prompt_preview = (
            (user_prompt[:80] + "...") if len(user_prompt) > 80 else user_prompt
        )
        logger.info(
            "run_agent_sync start: session_id=%s task_id=%s mode=%s prompt_len=%s preview=%s",
            session_id,
            task_id,
            mode,
            len(user_prompt),
            prompt_preview,
        )
        run_started_at = time.monotonic()
        bus = MessageBus()
        router = None
        exp = None
        bohrium_svc = None
        ssh_attached = False

        try:
            # -- Stage 1: Playground --
            if not self._playground_init_done.is_set():
                self.init_playground_sync()
            task_id = task_id or ("ws_" + uuid.uuid4().hex[:16])
            playground = self._get_or_create_playground(session_id)
            run_dir = str(_project_root / "runs" / RUN_ID_WEB)
            pg_ctx = playground.prepare(
                {
                    "run_dir": run_dir,
                    "task_id": task_id,
                }
            )

            # -- Stage 2: Bohrium credentials + SSH --
            bohrium_svc = BohriumSetupService(self._sessions_service, bus)
            run_creds, user_id_for_ak, org_id = bohrium_svc.load_credentials(
                session_id
            )

            # Build a lightweight event_callback bridge for bohrium (legacy API)
            def _bohrium_event_cb(source, event_type, content, **extra):
                """Bridge bohrium events into the MessageBus."""
                try:
                    from matmaster.types.events import BohriumNodeEvent

                    bus.emit(
                        BohriumNodeEvent(
                            source=str(source),
                            payload={
                                "type": event_type,
                                "content": content,
                                **extra,
                            },
                        )
                    )
                except Exception:
                    logger.debug(
                        "bohrium event bridge error type=%s", event_type
                    )

            bohrium_result = bohrium_svc.setup(
                session_id=session_id,
                pg=playground,
                base=getattr(playground, "agent", playground),
                run_creds=run_creds,
                user_id_for_ak=user_id_for_ak,
                org_id=org_id,
                event_callback=_bohrium_event_cb,
                run_started_at=run_started_at,
            )
            ssh_attached = bohrium_result.ssh_attached
            if bohrium_result.abort_result is not None:
                return
            pg_ctx = pg_ctx.with_bohrium(bohrium_result._asdict())

            # -- Stage 3: Exp assembly --
            from matmaster.assembly.direct_exp import DirectExp

            exp = DirectExp(
                llm_provider=self._build_llm_provider(
                    pg_ctx, llm_override, model_override
                ),
                builtin_tools=self._get_builtin_tools(pg_ctx),
                bus=bus,
                session=(
                    playground.session
                    if hasattr(playground, "session")
                    else None
                ),
                config_dir=(
                    playground.config_path.parent
                    if hasattr(playground, "config_path")
                    else None
                ),
                mcp_config=pg_ctx.run_meta.get("mcp_config"),
                skill_config=pg_ctx.run_meta.get("skill_config"),
            )
            spec = exp.assemble(
                pg_ctx,
                hooks=[
                    ConfirmationHook(reply_queue, bus),
                    OutputProcessorHook(bus),
                    SkillHitHook(bus),
                    AssistantStateHook(bus),
                ],
            )

            # -- Stage 4: History --
            events_table = get_chat_events_table()
            raw_events = (
                events_table.get_session_events(
                    session_id, limit=_DIALOG_HISTORY_MAX_EVENTS
                )
                if events_table
                else []
            )
            history = ChatHistoryConverter.events_to_messages(raw_events)

            # -- Stage 5: EventRouter --
            workspace_path = pg_ctx.workdir
            router = EventRouter(
                bus=bus,
                handlers=[
                    PersistenceHandler(
                        events_table,
                        session_id,
                        task_id,
                        invocation_id,
                    ),
                    SSEHandler(
                        send_cb,
                        loop,
                        session_id,
                        task_id,
                        invocation_id,
                        mode,
                    ),
                    WorkspaceHandler(
                        session_id=session_id,
                        task_id=task_id,
                        ssh_attached=ssh_attached,
                        archival_config=pg_ctx.archival,
                        workspace_path=workspace_path,
                    ),
                ],
            )
            router.start()

            # -- Stage 6: Kernel execution --
            kernel = AgentKernel()
            finish_event = kernel.run(
                spec=spec,
                task=user_prompt,
                history=history,
                stop_event=stop_event,
            )

            # -- Post-processing --
            if finish_event.reason == "cancelled":
                bus.emit(
                    CancelledEvent(
                        source="System", reason="Task cancelled by user."
                    )
                )
            else:
                # Quota deduction (per QUAL-05: success only)
                user_id = self._sessions_service.get_session_user_id(
                    session_id
                )
                if user_id:
                    if loop is not None:
                        future = asyncio.run_coroutine_threadsafe(
                            use_quota(user_id), loop
                        )
                        future.result(timeout=10)
                    else:
                        asyncio.run(use_quota(user_id))

        except Exception as exc:
            logger.exception(
                "run_agent_sync error: session_id=%s", session_id
            )
            try:
                bus.emit(ErrorEvent(source="System", message=str(exc)))
            except Exception:
                pass
        finally:
            elapsed = time.monotonic() - run_started_at
            logger.info(
                "run_agent_sync done: session_id=%s elapsed=%.1fs",
                session_id,
                elapsed,
            )
            if router:
                router.stop()
            if exp:
                try:
                    exp._run_cleanup_callbacks()
                except Exception:
                    logger.warning("Exp cleanup error", exc_info=True)
            if bohrium_svc:
                try:
                    bohrium_svc.cleanup(
                        session_id=session_id,
                        event_callback=_bohrium_event_cb,
                        pg_for_run=playground if "playground" in dir() else None,
                        ssh_attached=ssh_attached,
                    )
                except Exception:
                    logger.warning("Bohrium cleanup error", exc_info=True)
            get_redis_dao().delete_stop_requested(session_id, task_id)
            pg = self._playgrounds.pop(session_id, None)
            if pg:
                pg.cleanup()
            gc.collect()


@lru_cache
def get_agent_run_service() -> AgentRunService:
    return AgentRunService(sessions_service=get_sessions_service())


async def init_playground() -> None:
    """Initialize playground at startup (called in lifespan)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_agent_run_service().init_playground_sync)
