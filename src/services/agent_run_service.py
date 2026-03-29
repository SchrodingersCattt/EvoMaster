"""Agent execution service: new matmaster pipeline orchestration.

Rewritten per D-12: run_agent_sync() is a thin orchestration layer using:
  Playground.prepare() -> get_chat_events_table() -> EventRouter bootstrap ->
  Bohrium -> WorkspaceHandler attachment -> Exp.assemble() -> ChatHistory ->
  Kernel.run() -> post-processing

Method signature (12 parameters) unchanged -- zero caller modifications.
"""

import asyncio
import gc
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from matmaster.config.exp import ExpConfig
from matmaster.core.bus import MessageBus
from matmaster.core.playground import PlaygroundManager
from matmaster.hooks import (
    AssistantStateHook,
    OutputProcessorHook,
    SkillHitHook,
)
from matmaster.integration import (
    EventRouter,
    PersistenceHandler,
    SSEHandler,
    WorkspaceHandler,
)
from matmaster.integration.bohrium_setup import BohriumSetupService, SkillSyncSpec
from matmaster.types.context import WorkspaceArchivalConfig
from matmaster.types.events import (
    BohriumNodeEvent,
    CancelledEvent,
    ErrorEvent,
    ResponseEvent,
    StreamClosedEvent,
)
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


def _build_workspace_upload_fn(
    archival_config: WorkspaceArchivalConfig | None,
) -> Callable[..., Any] | None:
    """Build workspace upload closure when archival is enabled.

    Lazy-imports oss_io to avoid hard oss2 dependency when archival
    is disabled.
    """
    if not archival_config or not archival_config.enabled:
        return None
    oss_prefix = (archival_config.oss_prefix or '').strip('/')

    def _do_upload(session_id: str, task_id: str, workspace_path: Path) -> None:
        from src.dao.oss_io import upload_dir_to_oss

        key_prefix = '/'.join(part for part in (oss_prefix, session_id) if part)
        upload_dir_to_oss(workspace_path, key_prefix)

    return _do_upload


def _derive_skill_sync_spec(
    exp_config: ExpConfig,
    playground: Any,
    *,
    project_root: Path,
) -> SkillSyncSpec | None:
    """Build SkillSyncSpec from Exp skills config and optional mat_master.skill_evolution.

    ``remote_project_root`` is fixed to ``/personal/workspace/.evomaster``.
    When ``exp_config.skills`` is disabled or has no ``skills_root``, returns
    ``None`` (no fallback to unrelated default evomaster paths).
    """
    skills = exp_config.skills
    if not skills.enabled:
        return None
    # skills_root can be str | list[str]
    roots_raw = skills.skills_root
    if isinstance(roots_raw, list):
        rel_list = [r.strip() for r in roots_raw if r and r.strip()]
    else:
        s = (roots_raw or '').strip()
        rel_list = [s] if s else []
    if not rel_list:
        return None
    resolved_roots: list[str] = []
    for root_rel in rel_list:
        p = Path(root_rel)
        p = p.resolve() if p.is_absolute() else (project_root / root_rel).resolve()
        if p.is_dir():
            resolved_roots.append(str(p))
    if not resolved_roots:
        return None

    local_user: str | None = None
    remote_user: str | None = None
    if hasattr(playground, 'config'):
        try:
            cfg = playground.config.model_dump()
        except Exception:
            cfg = {}
    else:
        cfg = {}
    evo = (cfg.get('mat_master') or {}).get('skill_evolution') or {}
    loc_raw = evo.get('local_user_skills_root')
    rem_raw = evo.get('remote_user_skills_root')
    if loc_raw and rem_raw:
        local_user = str(Path(str(loc_raw)).expanduser().resolve())
        remote_user = str(rem_raw).strip()

    return SkillSyncSpec(
        project_skill_roots=resolved_roots,
        local_user_skills_root=local_user,
        remote_user_skills_root=remote_user,
        remote_project_root='/personal/workspace/.evomaster',
    )


@runtime_checkable
class ReplyQueueLike(Protocol):
    """Confirmation reply queue abstraction: put content/cancel, blocking get.

    .. deprecated::
        Phase 15 introduced ConfirmationHook.resolve()/cancel() as the new API.
        This Protocol is retained only for stream_service.py compatibility.
        Will be removed when stream_service fully migrates to ConfirmationHook.
    """

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
        self._pg_manager = PlaygroundManager(_project_root)

    def init_playground_sync(self) -> None:
        """Validate configs at startup -- delegates to PlaygroundManager + LLM check."""
        self._pg_manager.validate_startup()
        self._validate_llm_configs()

    def _validate_llm_configs(self) -> None:
        """启动时校验 agents.general.llm 与 llm_config.yaml profiles 的一致性。"""
        import yaml

        from matmaster.config.loader import load_llm_config

        cfg_dir = _project_root / 'matmaster_config'
        llm_config_path = cfg_dir / 'llm_config.yaml'
        if not llm_config_path.exists():
            logger.warning('LLM config not found: %s', llm_config_path)
            return
        try:
            llm_cfg = load_llm_config(llm_config_path)
        except Exception:
            logger.exception('Failed to load LLM config: %s', llm_config_path)
            return
        config_path = cfg_dir / 'config.yaml'
        if not config_path.exists():
            return
        with open(config_path) as f:
            main_cfg = yaml.safe_load(f)
        general_llm = (main_cfg or {}).get('agents', {}).get('general', {}).get('llm')
        if general_llm and general_llm not in llm_cfg.profiles:
            logger.error(
                "agents.general.llm='%s' not found in llm_config profiles: %s",
                general_llm,
                list(llm_cfg.profiles),
            )

    def get_executor(self) -> ThreadPoolExecutor:
        """Return the thread pool for agent execution."""
        return self._executor

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
    ) -> tuple[bool | tuple[bool, str], int]:
        """Execute agent in background thread using new matmaster pipeline.

        Pipeline: Playground.prepare() -> get_chat_events_table() ->
        EventRouter bootstrap -> Bohrium -> WorkspaceHandler attachment ->
        Exp.assemble() -> ChatHistory -> Kernel.run() -> post-processing.

        Method signature unchanged per D-12: all 12 parameters preserved.
        Returns ``(run_result, elapsed_ms)`` where ``run_result`` is ``True``
        on success or ``(False, reason)`` on failure/cancel, so Worker can
        derive session status and notifications consistently.
        """

        def _elapsed_ms() -> int:
            return int((time.monotonic() - run_started_at) * 1000)

        prompt_preview = (
            (user_prompt[:80] + '...') if len(user_prompt) > 80 else user_prompt
        )
        logger.info(
            'run_agent_sync start: session_id=%s task_id=%s mode=%s prompt_len=%s preview=%s',
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
            self.init_playground_sync()
            task_id = task_id or ('ws_' + uuid.uuid4().hex[:16])
            playground = self._pg_manager.get_or_create(session_id)
            run_dir = str(_project_root / 'runs' / RUN_ID_WEB)
            pg_ctx = playground.prepare(
                {
                    'run_dir': run_dir,
                    'task_id': task_id,
                }
            )
            try:
                events_table = get_chat_events_table()
            except Exception:
                # EventRouter is not started yet. Keep this failure silent for
                # callers so we do not emit partial SSE/error lifecycle events.
                logger.exception(
                    'run_agent_sync pre-router setup failed: session_id=%s',
                    session_id,
                )
                return None

            # -- Stage 2: EventRouter bootstrap --
            # Handler order: SSEHandler first for lower frontend latency
            # (serial dispatch means SSE send runs before slower DB persistence)
            router = EventRouter(
                bus=bus,
                handlers=[
                    SSEHandler(
                        send_cb,
                        session_id,
                        task_id,
                        invocation_id,
                        mode,
                    ),
                    PersistenceHandler(
                        events_table,
                        session_id,
                        task_id,
                        invocation_id,
                    ),
                ],
            )
            # Dedicated event loop for router's async consume task.
            # Service layer is sync, so we bridge via run_coroutine_threadsafe.
            _router_loop = asyncio.new_event_loop()
            _router_loop_thread = threading.Thread(
                target=_router_loop.run_forever,
                daemon=True,
                name="router-loop",
            )
            _router_loop_thread.start()
            asyncio.run_coroutine_threadsafe(router.start(), _router_loop).result()

            exp_name = mode or 'direct'
            from matmaster.config.loader import load_exp_config

            exp_config = load_exp_config(exp_name)
            skill_sync_spec = _derive_skill_sync_spec(
                exp_config, playground, project_root=_project_root
            )

            # -- Stage 3: Bohrium credentials + SSH --
            bohrium_svc = BohriumSetupService(self._sessions_service, bus)
            run_creds, user_id_for_ak, org_id = bohrium_svc.load_credentials(session_id)

            # Build a lightweight event_callback bridge for bohrium (legacy API).
            # error / stream_closed must be top-level bus events so SSE/Redis see
            # type=error|stream_closed (not nested under bohrium_node).
            def _bohrium_event_cb(source, event_type, content, **extra):
                """Bridge bohrium events into the MessageBus."""
                try:
                    if event_type == 'error':
                        msg = content if isinstance(content, str) else str(content)
                        bus.emit_nowait(ErrorEvent(source=str(source), message=msg))
                        return
                    if event_type == 'stream_closed':
                        body = '' if content is None else str(content)
                        bus.emit_nowait(
                            StreamClosedEvent(
                                source=str(source),
                                content=body,
                                task_completed=False,
                                end_reason='error',
                                treat_as_failure=True,
                            )
                        )
                        return
                    bus.emit_nowait(
                        BohriumNodeEvent(
                            source=str(source),
                            payload={
                                'type': event_type,
                                'content': content,
                                **extra,
                            },
                        )
                    )
                except Exception:
                    logger.debug('bohrium event bridge error type=%s', event_type)

            bohrium_result = bohrium_svc.setup(
                session_id=session_id,
                pg=playground,
                skill_sync_spec=skill_sync_spec,
                run_creds=run_creds,
                user_id_for_ak=user_id_for_ak,
                org_id=org_id,
                event_callback=_bohrium_event_cb,
                run_started_at=run_started_at,
            )
            ssh_attached = bohrium_result.ssh_attached
            if bohrium_result.abort_result is not None:
                # 必须返回 abort_result，供 Worker 识别失败并发「Worker 执行失败」飞书；裸 return None 会被误判为成功。
                return bohrium_result.abort_result
            bohrium_meta = dict(bohrium_result._asdict())
            bohrium_meta.pop('execution_session', None)
            pg_ctx = pg_ctx.with_bohrium(bohrium_meta)
            if bohrium_result.execution_session is not None:
                ew = bohrium_result.execution_workdir or ''
                st = bohrium_result.session_type or 'ssh'
                pg_ctx = pg_ctx.with_execution(
                    session=bohrium_result.execution_session,
                    session_type=st,
                    execution_workdir=ew,
                )
            # Workspace handling depends on the finalized Bohrium/archival context.
            router.add_handler(
                WorkspaceHandler(
                    session_id=session_id,
                    task_id=task_id,
                    ssh_attached=ssh_attached,
                    archival_config=pg_ctx.archival,
                    workspace_path=pg_ctx.workdir,
                    upload_fn=_build_workspace_upload_fn(pg_ctx.archival),
                )
            )

            # -- Stage 4: Exp assembly --
            from matmaster.config.loader import load_llm_config
            from matmaster.core.exp import Exp
            from matmaster.providers.llm_factory import build_provider

            llm_config = load_llm_config(
                playground.config_path.parent / 'llm_config.yaml'
            )

            agents = getattr(playground.config, 'agents', None)
            agent_default_llm = None
            if isinstance(agents, dict):
                general = agents.get('general', {})
                if isinstance(general, dict):
                    agent_default_llm = general.get('llm')

            pg_ctx = pg_ctx.model_copy(
                update={
                    'llm_provider': build_provider(
                        llm_config,
                        model_override=model_override,
                        llm_override=llm_override,
                        default_profile_key=agent_default_llm,
                    ),
                    'llm_config': llm_config,
                }
            )

            exp = Exp(exp_config)

            # -- Stage 4b: Bridge loop for async Exp + Kernel --
            # Single event loop covers build_runtime, kernel.run, and cleanup.
            _loop = asyncio.new_event_loop()
            try:
                runtime = _loop.run_until_complete(
                    exp.build_runtime(
                        pg_ctx,
                        bus=bus,
                        skills=pg_ctx.run_meta.get('skill_config'),
                        mcp=pg_ctx.run_meta.get('mcp_config'),
                    )
                )

                # Add external hooks to spec
                external_hooks = [
                    # TODO: re-enable with confirm_tools once MCP registration lands
                    # confirmation_hook = ConfirmationHook(bus)
                    # stream_svc.set_reply_queue(session_id, ConfirmationHookAdapter(confirmation_hook))
                    # external_hooks.append(confirmation_hook)
                    OutputProcessorHook(bus),
                    SkillHitHook(bus),
                    AssistantStateHook(bus),
                ]
                spec = runtime.spec.model_copy(
                    update={'hooks': [*runtime.spec.hooks, *external_hooks]}
                )

                # Inject stop_event into SpawnTool for cancel propagation (SUBA-05)
                if stop_event is not None and spec.tool_registry is not None:
                    from matmaster.tools.builtin.spawn_tool import SpawnTool

                    for tool in spec.tool_registry.all_tools:
                        if isinstance(tool, SpawnTool):
                            tool._stop_event = stop_event

                # -- Stage 5: History --
                raw_events = (
                    events_table.get_session_events(
                        session_id, limit=_DIALOG_HISTORY_MAX_EVENTS
                    )
                    if events_table
                    else []
                )
                parent_events = ChatHistoryConverter.exclude_spawn_events(raw_events)
                history = ChatHistoryConverter.events_to_messages(
                    ChatHistoryConverter.exclude_task_events(parent_events, task_id)
                )

                # -- Stage 6: Kernel execution --
                kernel_result = _loop.run_until_complete(
                    runtime.kernel.run(
                        spec=spec,
                        task=user_prompt,
                        history=history,
                        stop_event=stop_event,
                    )
                )
            finally:
                _loop.run_until_complete(exp._run_cleanup_callbacks())
                _loop.close()
            run_result_event = kernel_result.result.to_run_result_event()

            # -- Post-processing --
            if run_result_event.reason == 'cancelled':
                bus.emit_nowait(
                    CancelledEvent(source='System', reason='Task cancelled by user.')
                )
                bus.emit_nowait(
                    StreamClosedEvent(
                        source='System',
                        end_reason='cancelled',
                        task_completed=False,
                    )
                )
                return ((False, 'cancelled'), _elapsed_ms())
            else:
                if (
                    run_result_event.reason == 'natural'
                    and run_result_event.final_content
                ):
                    bus.emit_nowait(
                        ResponseEvent(
                            source=run_result_event.source,
                            content=run_result_event.final_content,
                        )
                    )
                bus.emit_nowait(run_result_event)
                bus.emit_nowait(
                    StreamClosedEvent(
                        source='System',
                        task_completed=run_result_event.reason == 'natural',
                        end_reason=run_result_event.reason,
                        treat_as_failure=run_result_event.status == 'failed' or None,
                    )
                )
                # Quota deduction (per QUAL-05: success only)
                if run_result_event.status == 'completed':
                    user_id = self._sessions_service.get_session_user_id(session_id)
                    if user_id:
                        if loop is not None:
                            future = asyncio.run_coroutine_threadsafe(
                                use_quota(user_id), loop
                            )
                            future.result(timeout=10)
                        else:
                            asyncio.run(use_quota(user_id))
                    return (True, _elapsed_ms())
                fail_reason = (
                    run_result_event.reason or run_result_event.status or 'failed'
                )
                if (
                    run_result_event.status == 'cancelled'
                    or run_result_event.reason == 'cancelled'
                ):
                    fail_reason = 'cancelled'
                return ((False, fail_reason), _elapsed_ms())

        except Exception as exc:
            logger.exception('run_agent_sync error: session_id=%s', session_id)
            try:
                bus.emit_nowait(ErrorEvent(source='System', message=str(exc)))
                bus.emit_nowait(
                    StreamClosedEvent(
                        source='System',
                        end_reason='error',
                        task_completed=False,
                        treat_as_failure=True,
                    )
                )
            except Exception:
                pass
            return ((False, str(exc)), _elapsed_ms())
        finally:
            elapsed = time.monotonic() - run_started_at
            logger.info(
                'run_agent_sync done: session_id=%s elapsed=%.1fs',
                session_id,
                elapsed,
            )
            # Cleanup order matters:
            # 1. Bohrium FIRST -- cleanup can still emit events via _bohrium_event_cb
            if bohrium_svc:
                try:
                    bohrium_svc.cleanup(
                        session_id=session_id,
                        event_callback=_bohrium_event_cb,
                        pg_for_run=playground if 'playground' in dir() else None,
                        ssh_attached=ssh_attached,
                    )
                except Exception:
                    logger.warning('Bohrium cleanup error', exc_info=True)
            # 2. Exp cleanup -- handled inside bridge loop (before _loop.close())
            # 3. Router LAST -- drains any final events from bohrium/exp cleanup
            if router:
                try:
                    asyncio.run_coroutine_threadsafe(
                        router.stop(), _router_loop
                    ).result(timeout=10)
                except Exception:
                    logger.warning('router.stop() failed during cleanup', exc_info=True)
            # 4. Shut down the dedicated router loop
            if '_router_loop' in dir():
                _router_loop.call_soon_threadsafe(_router_loop.stop)
                _router_loop_thread.join(timeout=5)
            get_redis_dao().delete_stop_requested(session_id, task_id)
            self._pg_manager.release(session_id)
            gc.collect()


@lru_cache
def get_agent_run_service() -> AgentRunService:
    return AgentRunService(sessions_service=get_sessions_service())


async def init_playground() -> None:
    """Initialize playground at startup (called in lifespan)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_agent_run_service().init_playground_sync)
