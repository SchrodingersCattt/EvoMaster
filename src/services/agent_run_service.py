"""Agent execution service: matmaster pipeline orchestration.

Pipeline: Playground.prepare() -> get_chat_events_table() -> RunEventFanout ->
Bohrium -> WorkspaceHandler -> Exp.run_stream() -> fanout.dispatch() ->
post-processing.
"""

import asyncio
import gc
import logging
import os
import time
import uuid
from collections.abc import Callable
from contextlib import aclosing
from functools import lru_cache
from pathlib import Path
from typing import Any

from matmaster.core.playground import PlaygroundManager
from matmaster.integration.fanout import RunEventFanout
from matmaster.integration.event_payloads import _normalize_public_source
from matmaster.integration.persistence_handler import PersistenceHandler
from matmaster.integration.sse_handler import SSEHandler
from matmaster.integration.workspace_handler import WorkspaceHandler
from matmaster.types.cancellation import CancellationToken
from src.services.agent_run_bohrium import (
    BohriumSetupService,
    derive_skill_sync_spec,
)
from matmaster.types.context import WorkspaceArchivalConfig
from matmaster.types.events import (
    BusEvent,
    CancelledEvent,
    ErrorEvent,
    RunResultEvent,
    StreamClosedEvent,
)
from src.dao.chat_events_table import get_chat_events_table
from src.dao.redis_dao import get_redis_dao
from src.services.chat_history import ChatHistoryConverter
from src.services.quota_service import use_quota
from src.services.sessions_service import get_sessions_service

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Multi-turn dialog: max events from DB to avoid context overflow
_DIALOG_HISTORY_MAX_EVENTS = int(
    os.environ.get('CHAT_DIALOG_HISTORY_MAX_EVENTS', '500')
)

_project_root = Path(__file__).resolve().parent.parent.parent
RUN_ID_WEB = 'mat_master_web'

_MATMASTER_CONFIG_DIR = _project_root / 'config'


def _get_agent_default_llm() -> str | None:
    """Read agents.general.llm from config/config.yaml.

    Returns None if the file or key is missing.
    """
    config_path = _MATMASTER_CONFIG_DIR / 'config.yaml'
    if not config_path.exists():
        return None
    import yaml

    with open(config_path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    agents = raw.get('agents')
    if isinstance(agents, dict):
        general = agents.get('general', {})
        if isinstance(general, dict):
            return general.get('llm')
    return None


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

async def _emit_error_and_close_fanout(
    fanout: RunEventFanout, message: str, source: str = 'System'
) -> None:
    """Dispatch ErrorEvent + StreamClosedEvent(treat_as_failure) pair via fanout."""
    await fanout.dispatch(ErrorEvent(source=source, message=message))
    await fanout.dispatch(
        StreamClosedEvent(
            source=source,
            end_reason='error',
            task_completed=False,
            treat_as_failure=True,
        )
    )


class AgentRunService:
    """Agent execution service: pipeline orchestration via matmaster components."""

    def __init__(self, sessions_service=None):
        self._sessions_service = sessions_service or get_sessions_service()
        self._pg_manager = PlaygroundManager(_project_root)

    def init_playground_sync(self) -> None:
        """Validate configs at startup -- delegates to PlaygroundManager."""
        self._pg_manager.validate_startup()

    async def run_agent(
        self,
        session_id: str,
        user_prompt: str,
        send_cb: Callable[[dict], Any],
        stop_event: CancellationToken,
        mode: str,
        task_id: str,
        invocation_id: str | None = None,
        llm_override: str | None = None,
        model_override: str | None = None,
    ) -> tuple[bool | tuple[bool, str], int]:
        """Execute agent pipeline using generator event stream with fanout dispatch.

        Events flow through RunEventFanout directly to handlers:
        kernel._run_items() -> kernel.run_stream() -> exp.run_stream()
        -> source normalization -> fanout.dispatch()

        SSE handler is awaited first (low latency), persistence runs as
        background tasks, WorkspaceHandler receives events inline.

        Returns ``(run_result, elapsed_ms)`` where ``run_result`` is ``True``
        on success or ``(False, reason)`` on failure/cancel.
        """

        def _elapsed_ms() -> int:
            return int((time.monotonic() - run_started_at) * 1000)

        prompt_preview = (
            (user_prompt[:80] + '...') if len(user_prompt) > 80 else user_prompt
        )
        logger.info(
            'run_agent start: session_id=%s task_id=%s mode=%s prompt_len=%s preview=%s',
            session_id,
            task_id,
            mode,
            len(user_prompt),
            prompt_preview,
        )
        run_started_at = time.monotonic()
        fanout: RunEventFanout | None = None
        exp = None
        bohrium_svc = None
        ssh_attached = False
        playground = None
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
                logger.exception(
                    'run_agent pre-handler setup failed: session_id=%s',
                    session_id,
                )
                return ((False, 'pre_router_setup_failed'), 0)

            # -- Stage 2: RunEventFanout bootstrap --
            # SSE handler first for lower frontend latency,
            # persistence as background tasks.
            fanout = RunEventFanout(
                sse_handler=SSEHandler(
                    send_cb,
                    session_id,
                    task_id,
                    invocation_id,
                    mode,
                ),
                persistence_handler=PersistenceHandler(
                    events_table,
                    session_id,
                    task_id,
                    invocation_id,
                ),
            )

            exp_name = mode or 'direct'
            from matmaster.config.loader import load_exp_config

            exp_config = load_exp_config(exp_name)
            skill_sync_spec = derive_skill_sync_spec(
                exp_config, project_root=_project_root
            )

            # -- Stage 3: Bohrium credentials + SSH --
            # Thread-safe event sink: Bohrium worker-thread callbacks
            # schedule fanout.dispatch() onto the event loop.
            loop = asyncio.get_running_loop()

            def _dispatch_from_thread(event: BusEvent) -> None:
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(fanout.dispatch(event))
                )

            bohrium_svc = BohriumSetupService(
                self._sessions_service,
                event_sink=_dispatch_from_thread,
            )
            bohrium_result = await bohrium_svc.run_setup(
                session_id=session_id,
                playground=playground,
                skill_sync_spec=skill_sync_spec,
                run_started_at=run_started_at,
            )
            ssh_attached = bohrium_result.ssh_attached
            if bohrium_result.abort_result is not None:
                return bohrium_result.abort_result
            bohrium_meta = dict(bohrium_result._asdict())
            bohrium_meta.pop('execution_session', None)
            pg_ctx = pg_ctx.with_bohrium(bohrium_meta)
            if bohrium_result.execution_session is not None:
                execution_workdir = bohrium_result.execution_workdir or ''
                session_type = bohrium_result.session_type or 'ssh'
                pg_ctx = pg_ctx.with_execution(
                    session=bohrium_result.execution_session,
                    session_type=session_type,
                    execution_workdir=execution_workdir,
                )
            # Workspace handling depends on the finalized Bohrium/archival context.
            fanout.add_handler(
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

            llm_config = load_llm_config(_project_root / 'config' / 'llm_config.yaml')

            agent_default_llm = _get_agent_default_llm()

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

            if pg_ctx.session is not None:
                pg_ctx.session._stop_event = stop_event

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

            # -- Stage 6: Generator event stream --
            run_result_event = None
            async with aclosing(exp.run_stream(
                pg_ctx,
                user_prompt,
                history=history,
                stop_event=stop_event,
                skills=pg_ctx.run_meta.get('skill_config'),
                source_override=exp_name,
            )) as stream:
                async for event in stream:
                    # Source normalization (ESIN-06)
                    if hasattr(event, 'source'):
                        normalized = _normalize_public_source(event.source)
                        if event.source != normalized:
                            event = event.model_copy(update={'source': normalized})

                    await fanout.dispatch(event)

                    # Detect terminal event
                    if isinstance(event, RunResultEvent):
                        run_result_event = event

            # -- Post-processing --
            if run_result_event is None:
                await _emit_error_and_close_fanout(
                    fanout, 'Generator terminated without result'
                )
                return ((False, 'no_result'), _elapsed_ms())

            if run_result_event.reason == 'cancelled':
                await fanout.dispatch(
                    CancelledEvent(source='System', reason='Task cancelled by user.')
                )
                await fanout.dispatch(
                    StreamClosedEvent(
                        source='System',
                        end_reason='cancelled',
                        task_completed=False,
                    )
                )
                return ((False, 'cancelled'), _elapsed_ms())
            else:
                await fanout.dispatch(
                    StreamClosedEvent(
                        source='System',
                        task_completed=run_result_event.reason == 'natural',
                        end_reason=run_result_event.reason,
                        treat_as_failure=run_result_event.status == 'failed' or None,
                    )
                )
                if run_result_event.status == 'completed':
                    user_id = self._sessions_service.get_session_user_id(session_id)
                    if user_id:
                        await use_quota(user_id)
                    return (True, _elapsed_ms())
                fail_reason = (
                    run_result_event.reason or run_result_event.status or 'failed'
                )
                return ((False, fail_reason), _elapsed_ms())

        except Exception as exc:
            logger.exception('run_agent error: session_id=%s', session_id)
            if fanout is not None:
                try:
                    await _emit_error_and_close_fanout(fanout, str(exc))
                except Exception:
                    pass
            return ((False, str(exc)), _elapsed_ms())
        finally:
            elapsed = time.monotonic() - run_started_at
            logger.info(
                'run_agent done: session_id=%s elapsed=%.1fs',
                session_id,
                elapsed,
            )
            # Cleanup order matters:
            # 1. Bohrium FIRST -- cleanup can still emit events via event bridge
            if bohrium_svc:
                try:
                    await bohrium_svc.run_cleanup(
                        session_id=session_id,
                        pg_for_run=playground,
                        ssh_attached=ssh_attached,
                    )
                except Exception:
                    logger.warning('Bohrium cleanup error', exc_info=True)
            # 2. Exp cleanup (with timeout to prevent worker hangs)
            if exp is not None:
                try:
                    await asyncio.wait_for(exp._run_cleanup_callbacks(), timeout=30)
                except Exception:
                    logger.warning('Exp cleanup failed', exc_info=True)
            # 3. Fanout LAST -- drains pending persistence and calls handler close()
            if fanout is not None:
                try:
                    await asyncio.wait_for(fanout.drain_and_close(), timeout=10)
                except Exception:
                    logger.warning(
                        'fanout.drain_and_close() failed during cleanup',
                        exc_info=True,
                    )
            get_redis_dao().delete_stop_requested(session_id, task_id)
            self._pg_manager.release(session_id)
            gc.collect(0)


@lru_cache
def get_agent_run_service() -> AgentRunService:
    return AgentRunService(sessions_service=get_sessions_service())


async def init_playground() -> None:
    """Initialize playground at startup (called in lifespan)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_agent_run_service().init_playground_sync)
