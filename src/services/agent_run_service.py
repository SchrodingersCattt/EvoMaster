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
from matmaster.integration.event_payloads import _normalize_public_source
from matmaster.integration.fanout import RunEventFanout
from matmaster.integration.persistence_handler import PersistenceHandler
from matmaster.integration.sse_handler import SSEHandler
from matmaster.integration.workspace_handler import WorkspaceHandler
from matmaster.types.cancellation import CancellationToken
from matmaster.types.context import WorkspaceArchivalConfig
from matmaster.types.events import (
    BusEvent,
    CancelledEvent,
    ErrorEvent,
    RunResultEvent,
    StreamClosedEvent,
    ToolResultEvent,
)
from matmaster.types.figures import FigureUploadConfig
from src.dao.chat_events_table import get_chat_events_table
from src.dao.oss_io import upload_bytes_to_oss
from src.dao.redis_dao import get_redis_dao
from src.services.agent_run_bohrium import (
    BohriumSetupService,
    derive_skill_sync_spec,
)
from src.services.history_checkpoint_service import HistoryCheckpointService
from src.services.history_restore_service import HistoryRestoreService
from src.services.image_input_service import get_image_input_service
from src.services.quota_service import use_quota
from src.services.response_figures_service import ResponseFiguresAccumulator
from src.services.sessions_service import get_sessions_service
from src.services.user_skills_sync import (
    materialize_user_skills_for_run,
    merge_user_skill_roots_into_exp_config,
)

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


def _invalid_finish_error_message(finish_detail: Any) -> str:
    kind = None
    if finish_detail is not None:
        kind = getattr(finish_detail, 'kind', None)
        if kind is None and isinstance(finish_detail, dict):
            kind = finish_detail.get('kind')

    if kind == 'output_length_exceeded':
        return (
            '模型输出被 provider 的输出 token 上限截断，'
            '未形成可提交的最终回答。请缩短上下文或提高输出上限后重试。'
        )
    if kind == 'content_filtered':
        return '模型输出被 provider 内容策略截断或拦截，未形成可提交的最终回答。'
    if kind == 'reasoning_only':
        return '模型只返回了思考内容，没有生成可见最终回答。请重试。'
    if kind == 'empty_response':
        return '模型本轮没有返回可见最终回答。请重试。'
    if kind == 'missing_llm_response':
        return '模型流结束但没有返回可验证的响应对象。请重试。'
    return '模型没有返回有效最终回答。请重试。'


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


def _build_figure_upload_config(*, session_id: str, task_id: str) -> FigureUploadConfig:
    """Build the per-run figure upload contract injected into tool runtime state."""
    return FigureUploadConfig(
        session_id=session_id,
        task_id=task_id,
        asset_key_prefix='matmaster/chat_figures',
        upload_bytes=upload_bytes_to_oss,
    )


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
        cancel_token: CancellationToken,
        mode: str,
        task_id: str,
        invocation_id: str | None = None,
        llm_override: str | None = None,
        model_override: str | None = None,
        images: list[str] | None = None,
        bohrium_required: bool = False,
        remote_workdir: str | None = None,
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
            user_id_for_skills = self._sessions_service.get_session_user_id(session_id)
            if (
                exp_config.skills.enabled
                and user_id_for_skills
                and user_id_for_skills.strip()
            ):
                user_skill_roots = await asyncio.to_thread(
                    materialize_user_skills_for_run,
                    user_id_for_skills.strip(),
                    project_root=_project_root,
                )
                exp_config = merge_user_skill_roots_into_exp_config(
                    exp_config, user_skill_roots
                )
            skill_sync_spec = derive_skill_sync_spec(
                exp_config, project_root=_project_root
            )

            # -- Stage 3: Bohrium credentials + SSH --
            # Thread-safe event sink: Bohrium worker-thread callbacks
            # schedule fanout.dispatch() onto the event loop.
            loop = asyncio.get_running_loop()

            def _dispatch_from_thread(event: BusEvent) -> None:
                fanout.dispatch_from_thread(loop, event)

            bohrium_svc = BohriumSetupService(
                self._sessions_service,
                event_sink=_dispatch_from_thread,
            )
            effective_bohrium_required = bool(bohrium_required or remote_workdir)
            bohrium_result = await bohrium_svc.run_setup(
                session_id=session_id,
                playground=playground,
                skill_sync_spec=skill_sync_spec,
                run_started_at=run_started_at,
                bohrium_required=effective_bohrium_required,
                remote_workdir=remote_workdir,
            )
            ssh_attached = bohrium_result.ssh_attached
            if bohrium_result.abort_result is not None:
                return bohrium_result.abort_result
            bohrium_meta = (
                bohrium_result.runtime_snapshot.model_dump()
                if bohrium_result.runtime_snapshot is not None
                else {}
            )
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
            resolved_llm = llm_config.resolve_route(
                model_override=model_override,
                llm_override=llm_override,
                default_key=agent_default_llm,
            )
            selected_profile = llm_config.get_profile(resolved_llm.profile_key)
            current_images = list(images or [])
            if current_images:
                selected_profile = get_image_input_service().ensure_vision_supported(
                    llm_config=llm_config,
                    llm_override=llm_override,
                    model_override=model_override,
                    default_profile_key=agent_default_llm,
                )
                image_parts: list[dict[str, Any]] = []
                for image_url in current_images:
                    image_part: dict[str, Any] = {'url': image_url}
                    if selected_profile.vision_detail is not None:
                        image_part['detail'] = selected_profile.vision_detail
                    image_parts.append(image_part)
                pg_ctx = pg_ctx.model_copy(
                    update={
                        'run_meta': {
                            **pg_ctx.run_meta,
                            'current_user_images': image_parts,
                        }
                    }
                )

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
                pg_ctx.session._cancel_token = cancel_token

            checkpoint_service = (
                HistoryCheckpointService(events_table)
                if events_table is not None
                else None
            )
            figure_accumulator = ResponseFiguresAccumulator()
            figure_dispatch_lock = asyncio.Lock()
            figure_upload_config = _build_figure_upload_config(
                session_id=session_id,
                task_id=task_id,
            )

            async def _dispatch_response_figures_if_dirty_unlocked(
                reason: str,
            ) -> None:
                response_figures_event = (
                    figure_accumulator.build_snapshot_event_if_dirty()
                )
                if response_figures_event is None:
                    return
                try:
                    await fanout.flush_persistence_barrier()
                    dispatched = await fanout.dispatch_and_wait_persistence(
                        response_figures_event
                    )
                except Exception:
                    logger.warning(
                        'response_figures dispatch failed reason=%s',
                        reason,
                        exc_info=True,
                    )
                    return

                if dispatched:
                    figure_accumulator.mark_snapshot_emitted()
                else:
                    logger.warning(
                        'response_figures dispatch reported handler failure '
                        'reason=%s',
                        reason,
                    )

            async def _dispatch_response_figures_if_dirty(reason: str) -> None:
                async with figure_dispatch_lock:
                    await _dispatch_response_figures_if_dirty_unlocked(reason)

            async def _record_tool_result_figures_and_dispatch_if_dirty(
                event: ToolResultEvent,
                *,
                include_spawned: bool,
                reason: str,
            ) -> None:
                async with figure_dispatch_lock:
                    figure_accumulator.add_tool_result(
                        event,
                        include_spawned=include_spawned,
                    )
                    await _dispatch_response_figures_if_dirty_unlocked(reason)

            async def _child_event_sink(event: BusEvent) -> None:
                try:
                    await fanout.dispatch(event)
                    if isinstance(event, ToolResultEvent):
                        await _record_tool_result_figures_and_dispatch_if_dirty(
                            event,
                            include_spawned=True,
                            reason='child_tool_result',
                        )
                except Exception:
                    logger.warning(
                        'child event sink failed for event type=%s',
                        getattr(event, 'type', '?'),
                        exc_info=True,
                    )

            def _checkpoint_sink_factory(*, spawn_id: str | None = None):
                if checkpoint_service is None:

                    async def _noop_checkpoint_sink(
                        *,
                        payload: dict[str, Any],
                        base_messages: list[dict[str, Any]],
                    ) -> None:
                        return None

                    return _noop_checkpoint_sink
                return checkpoint_service.build_checkpoint_sink(
                    fanout=fanout,
                    session_id=session_id,
                    task_id=task_id,
                    invocation_id=invocation_id,
                    spawn_id=spawn_id,
                )

            pg_ctx = pg_ctx.model_copy(
                update={
                    'run_meta': {
                        **pg_ctx.run_meta,
                        'event_sink': _child_event_sink,
                        'checkpoint_sink_factory': _checkpoint_sink_factory,
                        'figure_upload_config': figure_upload_config,
                    }
                }
            )

            # -- Stage 4b: AskQuestion bridge --
            from matmaster.integration.interaction_bridge import AskQuestionBridge
            from src.services.stream_service import RedisReplyQueue

            async def _interaction_event_sink(event: BusEvent) -> None:
                await fanout.dispatch(event)

            bridge = AskQuestionBridge(
                session_id=session_id,
                event_sink=_interaction_event_sink,
                reply_queue=RedisReplyQueue(session_id),
                timeout_seconds=1800,
            )
            pg_ctx = pg_ctx.model_copy(update={'interaction_bridge': bridge})
            # -- Stage 5: History --
            history = (
                HistoryRestoreService(events_table).restore_history(
                    session_id=session_id,
                    spawn_id=None,
                    task_id=task_id,
                    raw_limit=_DIALOG_HISTORY_MAX_EVENTS,
                )
                if events_table is not None
                else []
            )
            bohrium_rebuild_events: list[dict] = []
            try:
                if events_table is not None:
                    bohrium_rebuild_events = events_table.get_bohrium_events(session_id)
            except Exception:
                logger.warning(
                    'Failed to load Bohrium events for registry rebuild',
                    exc_info=True,
                )
            if bohrium_rebuild_events:
                pg_ctx = pg_ctx.model_copy(
                    update={
                        'run_meta': {
                            **pg_ctx.run_meta,
                            'bohrium_rebuild_events': bohrium_rebuild_events,
                        }
                    }
                )

            # -- Stage 6: Generator event stream --
            run_result_event = None
            async with aclosing(
                exp.run_stream(
                    pg_ctx,
                    user_prompt,
                    history=history,
                    cancel_token=cancel_token,
                    skills=pg_ctx.run_meta.get('skill_config'),
                    source_override=exp_name,
                )
            ) as stream:
                async for event in stream:
                    # Source normalization (ESIN-06)
                    if hasattr(event, 'source'):
                        normalized = _normalize_public_source(event.source)
                        if event.source != normalized:
                            event = event.model_copy(update={'source': normalized})

                    if isinstance(event, RunResultEvent) and event.spawn_id is None:
                        await _dispatch_response_figures_if_dirty('final_flush')

                    await fanout.dispatch(event)

                    if isinstance(event, ToolResultEvent):
                        await _record_tool_result_figures_and_dispatch_if_dirty(
                            event,
                            include_spawned=False,
                            reason='tool_result',
                        )

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
                if run_result_event.reason == 'invalid_finish':
                    await fanout.dispatch(
                        ErrorEvent(
                            source='System',
                            message=_invalid_finish_error_message(
                                run_result_event.finish_detail
                            ),
                        )
                    )
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
            # 1. Bohrium cleanup -- can still emit events via event bridge
            if bohrium_svc:
                try:
                    await bohrium_svc.run_cleanup(
                        session_id=session_id,
                        pg_for_run=playground,
                        ssh_attached=ssh_attached,
                    )
                except Exception:
                    logger.warning('Bohrium cleanup error', exc_info=True)
            # 2. Exp cleanup is owned by Exp.run_stream() via its finally block.
            #    aclosing() guarantees the generator's finally runs before we
            #    reach this point, so the callback list is already cleared.
            #    Do NOT re-invoke exp._run_cleanup_callbacks() here.
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
